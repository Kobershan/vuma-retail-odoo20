# -*- coding: utf-8 -*-
"""Regression tests for the overdue-account credit hold.

The important rules are server-side, so these tests use the ORM as a
salesperson and as an administrator rather than checking view XML:

1. an overdue posted invoice puts the commercial account on hold;
2. a non-administrator cannot quote, confirm, invoice or deliver to it;
3. an administrator gets exactly one quotation, then no more;
4. paying the invoice releases both the hold and the one-quotation allowance.
"""
from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'vuma_credit_hold'


@tagged('post_install', '-at_install', MODULE)
class TestCreditHold(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.sale_journal = cls.env['account.journal'].search([
            ('type', '=', 'sale'),
            ('company_id', '=', cls.company.id),
        ], limit=1)
        cls.bank_journal = cls.env['account.journal'].search([
            ('type', 'in', ('bank', 'cash')),
            ('company_id', '=', cls.company.id),
        ], order='type desc, id', limit=1)
        cls.income_account = cls.env['account.account'].search([
            ('company_ids', 'in', cls.company.id),
            ('account_type', 'in', ('income', 'income_other')),
            ('active', '=', True),
        ], order='id', limit=1)

        cls.product = cls.env['product.product'].create({
            'name': 'Vuma Credit Hold Test Service',
            'type': 'service',
            'sale_ok': True,
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vuma Credit Hold Customer',
            'customer_rank': 1,
            'company_id': False,
        })
        cls.contact = cls.env['res.partner'].create({
            'name': 'Vuma Credit Hold Delivery Contact',
            'parent_id': cls.customer.id,
            'type': 'delivery',
            'customer_rank': 1,
        })

        # A normal salesperson/accounting user: deliberately NOT in
        # base.group_system, which is the administrator boundary used by the
        # feature.
        groups = [
            cls.env.ref('base.group_user').id,
            cls.env.ref('sales_team.group_sale_salesman').id,
            cls.env.ref('sales_team.group_sale_salesman_all_leads').id,
            cls.env.ref('account.group_account_invoice').id,
            cls.env.ref('stock.group_stock_user').id,
        ]
        cls.rep = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Vuma Credit Hold Rep',
                'login': 'vuma.credit.hold.rep',
                'email': 'credit.hold.rep@example.com',
                'company_id': cls.company.id,
                'company_ids': [Command.set([cls.company.id])],
                'group_ids': [Command.set(groups)],
            })
        if cls.rep.has_group('base.group_system'):
            raise AssertionError(
                'the salesperson test user must not be an administrator'
            )

    # ------------------------------------------------------------------
    # Fixtures
    # ------------------------------------------------------------------
    def _rep_env(self):
        return self.env(
            user=self.rep,
            context=dict(
                self.env.context,
                allowed_company_ids=[self.company.id],
            ),
        )

    def _rep_user(self):
        return self.rep

    def _post_invoice(self, partner=None, amount=100.0, due_days_ago=30,
                      move_type='out_invoice'):
        today = fields.Date.context_today(self.env.user)
        invoice = self.env['account.move'].create({
            'move_type': move_type,
            'partner_id': (partner or self.customer).id,
            'company_id': self.company.id,
            'journal_id': self.sale_journal.id,
            'invoice_date': today - relativedelta(days=due_days_ago + 5),
            'invoice_date_due': today - relativedelta(days=due_days_ago),
            'invoice_line_ids': [Command.create({
                'name': 'Credit hold test line',
                'quantity': 1.0,
                'price_unit': amount,
                'account_id': self.income_account.id,
                'tax_ids': [Command.clear()],
            })],
        })
        # The due date is computed from the payment terms on create; pin it so
        # the test is about the hold and not about a payment-term default.
        invoice.write({
            'invoice_date_due': today - relativedelta(days=due_days_ago),
        })
        invoice.action_post()
        return invoice

    def _make_order(self, env=None, partner=None):
        env = env or self.env
        return env['sale.order'].create({
            'partner_id': (partner or self.customer).id,
            'company_id': self.company.id,
            'vuma_is_mixed_basket': False,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 1.0,
                'price_unit': 100.0,
            })],
        })

    def _assert_hold_message(self, callback, *fragments):
        with self.assertRaises(UserError) as caught:
            callback()
        message = str(caught.exception)
        for fragment in fragments:
            self.assertIn(fragment, message)
        return message

    # ------------------------------------------------------------------
    # Hold calculation
    # ------------------------------------------------------------------
    def test_overdue_invoice_puts_account_on_hold(self):
        self.assertFalse(self.customer.vuma_credit_hold)
        invoice = self._post_invoice()
        self.customer.invalidate_recordset()

        self.assertTrue(self.customer.vuma_credit_hold)
        self.assertAlmostEqual(self.customer.vuma_overdue_amount, 100.0)
        self.assertEqual(self.customer.vuma_overdue_invoice_count, 1)
        self.assertEqual(
            self.customer.vuma_oldest_due_date, invoice.invoice_date_due)

    def test_invoice_due_today_or_later_is_not_a_hold(self):
        self._post_invoice(due_days_ago=0)
        self.customer.invalidate_recordset()
        self.assertFalse(
            self.customer.vuma_credit_hold,
            'an invoice due today is not overdue until tomorrow',
        )

        self._post_invoice(due_days_ago=-10)
        self.customer.invalidate_recordset()
        self.assertFalse(self.customer.vuma_credit_hold)

    def test_credit_note_reduces_the_overdue_balance(self):
        self._post_invoice(amount=100.0)
        self.customer.invalidate_recordset()
        self.assertTrue(self.customer.vuma_credit_hold)

        self._post_invoice(amount=100.0, move_type='out_refund')
        self.customer.invalidate_recordset()
        self.assertFalse(self.customer.vuma_credit_hold)
        self.assertAlmostEqual(self.customer.vuma_overdue_amount, 0.0)

    def test_child_contact_shares_the_commercial_hold(self):
        self._post_invoice(partner=self.contact)
        self.customer.invalidate_recordset()
        self.contact.invalidate_recordset()
        self.assertTrue(self.customer.vuma_credit_hold)
        self.assertTrue(self.contact.vuma_credit_hold)

        self._assert_hold_message(
            lambda: self._make_order(env=self._rep_env(), partner=self.contact),
            'credit hold', 'administrator',
        )

    # ------------------------------------------------------------------
    # Administrator's one quotation
    # ------------------------------------------------------------------
    def test_non_admin_cannot_create_a_quotation(self):
        self._post_invoice()
        self.customer.invalidate_recordset()
        self._assert_hold_message(
            lambda: self._make_order(env=self._rep_env()),
            'credit hold', 'Only an administrator',
        )

    def test_admin_gets_exactly_one_quotation(self):
        self._post_invoice()
        self.customer.invalidate_recordset()

        first = self._make_order()
        self.assertTrue(first.vuma_hold_quote_exception)
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.vuma_hold_quote_id, first)
        self.assertTrue(self.customer.vuma_hold_quote_used)

        self._assert_hold_message(
            lambda: self._make_order(),
            'already been used', first.name,
        )

        # Confirming the one quotation does not create a new allowance.
        first.action_confirm()
        self.assertEqual(first.state, 'sale')
        self.customer.invalidate_recordset()
        self.assertTrue(self.customer.vuma_hold_quote_used)
        self._assert_hold_message(
            lambda: self._make_order(),
            'already been used',
        )

    def test_admin_mixed_basket_quote_counts_as_the_one_quotation(self):
        trade = self.env['res.company'].search(
            [('name', '=', 'Trade Rite')], limit=1)
        siyaya = self.env['res.company'].search(
            [('name', '=', 'Siyaya DC')], limit=1)
        if not (trade and siyaya):
            self.skipTest('Trade Rite and Siyaya DC are required')

        Product = self.env['product.product']
        products = {}
        for company in (trade, siyaya):
            product = Product.search([
                ('company_id', '=', company.id),
                ('sale_ok', '=', True),
                ('type', '=', 'consu'),
            ], limit=1)
            if not product:
                product = Product.create({
                    'name': f'Vuma Credit Hold Mixed {company.name}',
                    'type': 'consu',
                    'sale_ok': True,
                    'company_id': company.id,
                    'taxes_id': [Command.clear()],
                })
            products[company] = product

        self._post_invoice()
        self.customer.invalidate_recordset()

        both = self.env(context=dict(
            self.env.context,
            allowed_company_ids=[trade.id, siyaya.id],
        ))
        order = both['sale.order'].create({
            'partner_id': self.customer.id,
            'company_id': trade.id,
            'vuma_is_mixed_basket': True,
            'order_line': [
                Command.create({
                    'product_id': products[company].id,
                    'product_uom_qty': 1.0,
                    'vuma_source_company_id': company.id,
                })
                for company in (trade, siyaya)
            ],
        })
        self.assertTrue(order.vuma_hold_quote_exception)
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.vuma_hold_quote_id, order)

        order.action_confirm()
        self.customer.invalidate_recordset()
        self.assertTrue(self.customer.vuma_hold_quote_used)
        self.assertEqual(order.state, 'cancel')
        split_orders = (
            order.vuma_split_quote_trade_rite_id
            | order.vuma_split_quote_siyaya_dc_id
        )
        self.assertEqual(len(split_orders), 2)
        for split in split_orders:
            self.assertEqual(split.state, 'sale')
            self.assertTrue(split.vuma_hold_quote_exception)
            self.assertEqual(split.vuma_original_mixed_quote_id, order)
            # The split company orders are part of the one quotation, so the
            # administrator may still work on them.
            split.write({'client_order_ref': 'split quotation'})

        self._assert_hold_message(
            lambda: both['sale.order'].create({
                'partner_id': self.customer.id,
                'company_id': trade.id,
                'vuma_is_mixed_basket': False,
                'order_line': [Command.create({
                    'product_id': products[trade].id,
                    'product_uom_qty': 1.0,
                })],
            }),
            'already been used',
        )

    def test_deleting_the_one_quotation_does_not_restore_the_allowance(self):
        self._post_invoice()
        self.customer.invalidate_recordset()
        first = self._make_order()
        self.customer.invalidate_recordset()
        self.assertTrue(self.customer.vuma_hold_quote_used)

        first.unlink()
        self.customer.invalidate_recordset()
        self.assertFalse(self.customer.vuma_hold_quote_id)
        self.assertTrue(
            self.customer.vuma_hold_quote_used,
            'deleting the quotation must not hand out a second allowance',
        )
        self._assert_hold_message(
            lambda: self._make_order(),
            'already been used', 'deleted or cancelled',
        )

    def test_admin_can_claim_an_existing_draft_once(self):
        draft = self._make_order()
        self._post_invoice()
        self.customer.invalidate_recordset()

        rep = self._rep_user()
        self._assert_hold_message(
            lambda: draft.with_user(rep).write({'client_order_ref': 'rep'}),
            'credit hold',
        )
        self._assert_hold_message(
            lambda: draft.with_user(rep).action_confirm(),
            'credit hold',
        )
        # Even an administrator must make the intent explicit before touching
        # an old quotation while the account is on hold.
        self._assert_hold_message(
            lambda: draft.write({'client_order_ref': 'admin'}),
            'Use as Credit-Hold Quotation',
        )

        draft.action_vuma_claim_hold_quote()
        self.assertTrue(draft.vuma_hold_quote_exception)
        self.customer.invalidate_recordset()
        self.assertEqual(self.customer.vuma_hold_quote_id, draft)
        self.assertTrue(self.customer.vuma_hold_quote_used)

        draft.action_confirm()
        self.assertEqual(draft.state, 'sale')
        self._assert_hold_message(
            lambda: self._make_order(),
            'already been used',
        )

    def test_order_lines_cannot_bypass_the_hold(self):
        self._post_invoice()
        self.customer.invalidate_recordset()
        first = self._make_order()
        rep = self._rep_user()

        self._assert_hold_message(
            lambda: self.env['sale.order.line'].with_user(rep).create({
                'order_id': first.id,
                'product_id': self.product.id,
                'product_uom_qty': 2.0,
                'price_unit': 100.0,
            }),
            'credit hold',
        )
        self._assert_hold_message(
            lambda: first.with_user(rep).order_line.write({
                'product_uom_qty': 3.0,
            }),
            'credit hold',
        )

    # ------------------------------------------------------------------
    # Other sales documents
    # ------------------------------------------------------------------
    def test_non_admin_cannot_post_or_cancel_an_invoice(self):
        invoice = self._post_invoice()
        self.customer.invalidate_recordset()
        draft = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'journal_id': self.sale_journal.id,
            'invoice_date': fields.Date.context_today(self.env.user),
            'invoice_line_ids': [Command.create({
                'name': 'Credit hold draft line',
                'quantity': 1.0,
                'price_unit': 50.0,
                'account_id': self.income_account.id,
                'tax_ids': [Command.clear()],
            })],
        })
        rep = self._rep_user()
        self._assert_hold_message(
            lambda: draft.with_user(rep).action_post(),
            'credit hold', 'administrator',
        )
        self._assert_hold_message(
            lambda: invoice.with_user(rep).button_cancel(),
            'credit hold', 'administrator',
        )

    def test_non_admin_cannot_validate_a_delivery(self):
        self._post_invoice()
        self.customer.invalidate_recordset()
        picking_type = self.env['stock.picking.type'].search([
            ('code', '=', 'outgoing'),
            ('company_id', '=', self.company.id),
        ], limit=1)
        self.assertTrue(picking_type, 'this database needs an outgoing operation')
        source = picking_type.default_location_src_id
        if not source:
            warehouse = self.env['stock.warehouse'].search([
                ('company_id', '=', self.company.id),
            ], limit=1)
            source = warehouse.lot_stock_id
        destination = picking_type.default_location_dest_id
        if not destination:
            destination = self.env.ref('stock.stock_location_customers')
        picking = self.env['stock.picking'].create({
            'partner_id': self.customer.id,
            'picking_type_id': picking_type.id,
            'location_id': source.id,
            'location_dest_id': destination.id,
        })
        self._assert_hold_message(
            lambda: picking.with_user(self._rep_user()).button_validate(),
            'credit hold', 'administrator',
        )

    # ------------------------------------------------------------------
    # Payment releases everything
    # ------------------------------------------------------------------
    def test_non_admin_can_still_pay_the_overdue_invoice(self):
        invoice = self._post_invoice()
        self.customer.invalidate_recordset()
        self.assertTrue(self.customer.vuma_credit_hold)

        rep_env = self._rep_env()
        payment = rep_env['account.payment.register'].with_context(
            active_model='account.move',
            active_ids=invoice.ids,
        ).create({
            'payment_date': fields.Date.context_today(self.env.user),
            'journal_id': self.bank_journal.id,
            'amount': invoice.amount_residual,
        })
        payment.action_create_payments()

        invoice.invalidate_recordset()
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(invoice.amount_residual, 0.0)
        self.assertFalse(self.customer.vuma_credit_hold)

    def test_payment_releases_hold_and_quotation_allowance(self):
        invoice = self._post_invoice()
        self.customer.invalidate_recordset()
        first = self._make_order()
        self.assertTrue(first.vuma_hold_quote_exception)
        self.customer.invalidate_recordset()
        self.assertTrue(self.customer.vuma_hold_quote_used)

        payment = self.env['account.payment.register'].with_context(
            active_model='account.move',
            active_ids=invoice.ids,
        ).create({
            'payment_date': fields.Date.context_today(self.env.user),
            'journal_id': self.bank_journal.id,
            'amount': invoice.amount_residual,
        })
        payment.action_create_payments()

        invoice.invalidate_recordset()
        self.customer.invalidate_recordset()
        self.assertAlmostEqual(invoice.amount_residual, 0.0)
        self.assertFalse(self.customer.vuma_credit_hold)
        self.assertFalse(self.customer.vuma_hold_quote_id)
        self.assertFalse(self.customer.vuma_hold_quote_used)

        # A normal salesperson can quote again once the account is paid.
        fresh = self._make_order(env=self._rep_env())
        self.assertTrue(fresh)
        self.customer.invalidate_recordset()
        self.assertFalse(self.customer.vuma_hold_quote_id)
        self.assertFalse(self.customer.vuma_hold_quote_used)
