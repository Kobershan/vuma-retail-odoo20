# -*- coding: utf-8 -*-
"""End-to-end control tests for the Vuma Retail operations add-on.

These tests are intentionally written around the business controls rather than
around individual fields: approval bands, two-person stock counts, POD/returns,
credit notes, VAT sign-off, payment runs and offline field actions.
"""
import base64
import uuid
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

# A valid one-pixel PNG.  Odoo's Image fields validate and re-encode uploads,
# so a random base64 string is not an acceptable test attachment.
TEST_PNG = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk'
            '+A8AAQUBAScY42YAAAAASUVORK5CYII=')


@tagged('post_install', '-at_install')
class VumaOperationsTest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Vuma Test Warehouse',
                'code': 'VTW',
                'company_id': cls.company.id,
            })
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.customer_location = cls.env.ref('stock.stock_location_customers')
        cls.supplier_location = cls.env.ref('stock.stock_location_suppliers')

        cls.product = cls.env['product.product'].create({
            'name': 'Vuma Test Product',
            'type': 'consu',
            'is_storable': True,
            'list_price': 100.0,
            'standard_price': 80.0,
            'taxes_id': [Command.clear()],
            'supplier_taxes_id': [Command.clear()],
        })
        cls.service = cls.env['product.product'].create({
            'name': 'Vuma Test Service',
            'type': 'service',
            'list_price': 100.0,
            'standard_price': 0.0,
            'taxes_id': [Command.clear()],
            'supplier_taxes_id': [Command.clear()],
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Vuma Test Customer',
            'customer_rank': 1,
            'street': '1 Test Street',
            'city': 'Johannesburg',
            'phone': '0110000000',
            'email': 'customer@example.com',
            'company_id': cls.company.id,
        })
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Vuma Test Supplier',
            'supplier_rank': 1,
            'company_id': cls.company.id,
        })

        cls.user_buyer = cls._make_user(
            'vuma_buyer_test', ['purchase.group_purchase_user'])
        cls.user_ops = cls._make_user(
            'vuma_ops_test', ['vuma_retail_ops.group_vuma_ops_manager'])
        cls.user_finance = cls._make_user(
            'vuma_finance_test', ['vuma_retail_ops.group_vuma_finance_manager'])
        cls.user_owner = cls._make_user(
            'vuma_owner_test', ['vuma_retail_ops.group_vuma_owner'])
        cls.user_debtors = cls._make_user(
            'vuma_debtors_test', ['vuma_retail_ops.group_vuma_debtors'])
        cls.user_cashier = cls._make_user(
            'vuma_cashier_test', ['vuma_retail_ops.group_vuma_cashier'])
        cls.user_other = cls._make_user(
            'vuma_other_test', ['vuma_retail_ops.group_vuma_ops_manager'])

    @classmethod
    def _make_user(cls, login, groups):
        group_ids = [cls.env.ref('base.group_user').id]
        group_ids += [cls.env.ref(group).id for group in groups]
        return cls.env['res.users'].create({
            'name': login.replace('_', ' ').title(),
            'login': login,
            'email': '%s@example.com' % login,
            'group_ids': [Command.set(group_ids)],
        })

    def _make_po(self, amount):
        return self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'company_id': self.company.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'name': self.product.name,
                'product_qty': 1.0,
                'uom_id': self.product.uom_id.id,
                'price_unit': amount,
                'date_planned': fields.Datetime.now() + timedelta(days=1),
            })],
        })

    def _make_invoice(self, amount=1000.0, quantity=1.0, tax_ids=None):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_line_ids': [Command.create({
                'product_id': self.service.id,
                'name': self.service.name,
                'quantity': quantity,
                'price_unit': amount,
                'tax_ids': [Command.set(tax_ids or [])],
            })],
        })
        invoice.action_post()
        return invoice

    def _make_outgoing_picking(self):
        picking_type = self.warehouse.out_type_id
        return self.env['stock.picking'].create({
            'picking_type_id': picking_type.id,
            'partner_id': self.customer.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customer_location.id,
            'origin': 'Test delivery',
            'move_ids': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 2.0,
                'uom_id': self.product.uom_id.id,
                'location_id': self.stock_location.id,
                'location_dest_id': self.customer_location.id,
            })],
        })

    # ------------------------------------------------------------------ POs
    def test_purchase_order_approval_bands(self):
        buyer_po = self._make_po(5000.0)
        self.assertEqual(buyer_po.vuma_approval_level, 'buyer')
        buyer_po.with_user(self.user_buyer).action_vuma_approve()
        self.assertEqual(buyer_po.vuma_approval_state, 'approved')

        ops_po = self._make_po(20000.0)
        self.assertEqual(ops_po.vuma_approval_level, 'operations')
        with self.assertRaises(AccessError):
            ops_po.with_user(self.user_buyer).action_vuma_approve()
        ops_po.with_user(self.user_ops).action_vuma_approve()
        self.assertEqual(ops_po.vuma_approval_state, 'approved')

        large_po = self._make_po(200000.0)
        self.assertEqual(large_po.vuma_approval_level, 'owner')
        self.assertTrue(large_po.vuma_second_approval_required)
        large_po.with_user(self.user_finance).action_vuma_approve()
        self.assertEqual(large_po.vuma_approval_state, 'pending')
        large_po.with_user(self.user_owner).action_vuma_approve()
        self.assertEqual(large_po.vuma_approval_state, 'approved')
        self.assertEqual(large_po.vuma_second_approved_by_id, self.user_owner)

        # A second owner/finance key must be a different physical user.
        second_large = self._make_po(175000.0)
        second_large.with_user(self.user_finance).action_vuma_approve()
        with self.assertRaises(UserError):
            second_large.with_user(self.user_finance).action_vuma_approve()

    def test_emergency_purchase_can_proceed_then_blocks_after_deadline(self):
        po = self._make_po(12000.0)
        po.write({
            'vuma_is_emergency': True,
            'vuma_emergency_reason': 'Breakdown stopped the DC',
        })
        po.action_vuma_emergency()
        self.assertTrue(po._approval_allowed())
        po.vuma_emergency_deadline = fields.Datetime.now() - timedelta(minutes=1)
        self.assertFalse(po._approval_allowed())

    # --------------------------------------------------------------- stock
    def test_stock_count_reason_and_two_person_recount(self):
        count = self.env['vuma.stock.count'].create({
            'warehouse_id': self.warehouse.id,
            'location_id': self.stock_location.id,
            'count_type': 'full',
            'company_id': self.company.id,
            'tolerance_amount': 50.0,
            'tolerance_percent': 0.5,
        })
        line = self.env['vuma.stock.count.line'].create({
            'count_id': count.id,
            'product_id': self.product.id,
            'location_id': self.stock_location.id,
            'uom_id': self.product.uom_id.id,
            'system_qty': 10.0,
            'counted_qty': 8.0,
            'cost': 80.0,
            'is_counted': True,
            'counted_by_id': self.user_other.id,
        })
        self.assertEqual(line.difference, -2.0)
        with self.assertRaises(UserError):
            count.action_submit()
        line.reason = 'shrinkage'
        with self.assertRaises(UserError):
            count.action_submit()
        line.write({
            'is_recounted': True,
            'recount_qty': 8.0,
            'recounted_by_id': self.user_ops.id,
        })
        count.action_submit()
        self.assertEqual(count.state, 'pending')
        count.with_user(self.user_ops).action_approve()
        self.assertEqual(count.state, 'approved')

    def test_stock_count_recount_must_be_a_different_user(self):
        count = self.env['vuma.stock.count'].create({
            'warehouse_id': self.warehouse.id,
            'location_id': self.stock_location.id,
            'company_id': self.company.id,
            'tolerance_amount': 0.0,
            'tolerance_percent': 0.0,
        })
        line = self.env['vuma.stock.count.line'].create({
            'count_id': count.id,
            'product_id': self.product.id,
            'location_id': self.stock_location.id,
            'uom_id': self.product.uom_id.id,
            'system_qty': 5.0,
            'counted_qty': 4.0,
            'cost': 80.0,
            'reason': 'damage',
            'is_counted': True,
            'counted_by_id': self.user_ops.id,
        })
        line.write({
            'is_recounted': True,
            'recount_qty': 4.0,
            'recounted_by_id': self.user_ops.id,
        })
        with self.assertRaises(UserError):
            count.action_submit()

    # ------------------------------------------------------------- POD
    def test_pod_requires_photo_for_refusal_and_creates_failure(self):
        picking = self._make_outgoing_picking()
        with self.assertRaises(UserError):
            picking.action_vuma_capture_pod({
                'name': 'Jane Customer',
                'signature': TEST_PNG,
                'result': 'refused',
            })
        picking.action_vuma_capture_pod({
            'name': 'Jane Customer',
            'signature': TEST_PNG,
            'photo': TEST_PNG,
            'result': 'refused',
            'id_number': '9001010000000',
            'notes': 'Customer rejected damaged carton',
        })
        self.assertEqual(picking.vuma_pod_state, 'captured')
        self.assertTrue(picking.vuma_pod_damage)
        failure = self.env['vuma.delivery.failure'].search([
            ('picking_id', '=', picking.id),
        ], limit=1)
        self.assertTrue(failure)
        self.assertEqual(failure.reason, 'refused')

    def test_delivery_return_credit_note_approval_and_link(self):
        invoice = self._make_invoice(amount=100.0)
        picking = self._make_outgoing_picking()
        delivery_return = self.env['vuma.delivery.return'].create({
            'picking_id': picking.id,
            'invoice_id': invoice.id,
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'product_id': self.service.id,
            'quantity': 60.0,
            'uom_id': self.service.uom_id.id,
            'price_unit': 100.0,
            'reason': 'damaged',
            'action': 'credit',
            'photo': TEST_PNG,
        })
        delivery_return.action_submit()
        delivery_return.with_user(self.user_ops).action_approve()
        delivery_return.action_create_credit_note()
        credit = delivery_return.credit_note_id
        self.assertTrue(credit.reversed_entry_id)
        self.assertEqual(credit.vuma_delivery_return_id, delivery_return)
        self.assertGreater(credit.amount_total, 5000.0)
        self.assertEqual(credit.vuma_credit_approval_state, 'pending')
        with self.assertRaises(UserError):
            credit.action_post()
        credit.with_user(self.user_owner).action_vuma_approve_credit()
        credit.action_post()
        self.assertEqual(credit.state, 'posted')

    def test_pod_email_queues_message(self):
        picking = self._make_outgoing_picking()
        picking.action_vuma_capture_pod({
            'name': 'Email Customer',
            'signature': TEST_PNG,
            'result': 'full',
        })
        picking.action_vuma_email_pod()
        mail = self.env['mail.mail'].search([
            ('model', '=', 'stock.picking'),
            ('res_id', '=', picking.id),
        ], limit=1)
        self.assertTrue(mail)
        self.assertEqual(mail.email_to, self.customer.email)

    # ---------------------------------------------------------- debtors
    def test_debtor_rating_override_requires_finance(self):
        rating = self.env['vuma.debtor.rating'].create({
            'partner_id': self.customer.id,
            'company_id': self.company.id,
        })
        rating.action_compute()
        rating.write({
            'override_rating': 'd',
            'override_reason': 'Payment behaviour deteriorated',
        })
        with self.assertRaises(AccessError):
            rating.with_user(self.user_debtors).action_confirm()
        rating.with_user(self.user_finance).action_confirm()
        self.assertEqual(rating.state, 'confirmed')
        self.assertEqual(rating.override_by_id, self.user_finance)

    def test_debtor_credit_form_uses_approval_bands(self):
        form = self.env['vuma.debtor.form'].create({
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'form_type': 'credit_application',
            'requested_credit_limit': 50000.0,
            'attachment': base64.b64encode(b'signed form').decode(),
        })
        form.action_submit()
        with self.assertRaises(AccessError):
            form.with_user(self.user_debtors).action_approve()
        # R50,000 is in the owner band, not the finance band.
        with self.assertRaises(AccessError):
            form.with_user(self.user_finance).action_approve()
        form.with_user(self.user_owner).action_approve()
        self.assertEqual(form.state, 'approved')
        self.assertEqual(self.customer.credit_limit, 50000.0)

    def test_credit_limit_is_shared_across_companies(self):
        second = self.env['res.company'].create({
            'name': 'Vuma Second Trading Company',
            'currency_id': self.company.currency_id.id,
        })
        self.customer.credit_limit = 42000.0
        for company in (self.company, second):
            self.assertEqual(
                self.customer.with_company(company).credit_limit,
                42000.0,
                'the debtor credit limit must apply in every company')

    def test_credit_limit_set_in_any_company_applies_everywhere(self):
        second = self.env['res.company'].create({
            'name': 'Vuma Second Trading Company',
            'currency_id': self.company.currency_id.id,
        })
        self.customer.with_company(second).credit_limit = 31500.0
        for company in (self.company, second):
            self.assertEqual(
                self.customer.with_company(company).credit_limit,
                31500.0,
                'a limit set from the second company must reach the first')

    def test_credit_limit_on_create_is_shared_across_companies(self):
        second = self.env['res.company'].create({
            'name': 'Vuma Second Trading Company',
            'currency_id': self.company.currency_id.id,
        })
        partner = self.env['res.partner'].create({
            'name': 'Vuma Credit Created Customer',
            'customer_rank': 1,
            'credit_limit': 27500.0,
        })
        for company in (self.company, second):
            self.assertEqual(
                partner.with_company(company).credit_limit,
                27500.0,
                'a limit captured on creation must apply in every company')

    # ------------------------------------------------------------- cash
    def test_cash_receipt_requires_positive_amount(self):
        journal = self.env['account.journal'].search([
            ('company_id', '=', self.company.id),
            ('type', '=', 'bank'),
        ], limit=1)
        if not journal:
            self.skipTest('No bank journal configured')
        receipt = self.env['vuma.cash.receipt'].create({
            'receipt_type': 'customer',
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'journal_id': journal.id,
            'amount': 0.0,
        })
        with self.assertRaises(UserError):
            receipt.action_post()

    # ------------------------------------------------------------ VAT
    def test_vat_period_prepare_review_submit_and_lock(self):
        tax = self.env['account.tax'].search([
            ('company_id', '=', self.company.id),
            ('type_tax_use', '=', 'sale'),
            ('amount_type', '=', 'percent'),
        ], limit=1)
        if not tax:
            self.skipTest('No sale tax configured in this database')
        invoice = self._make_invoice(amount=1000.0, tax_ids=tax.ids)
        self.assertEqual(invoice.state, 'posted')
        period = self.env['vuma.tax.period'].create({
            'company_id': self.company.id,
            'date_from': fields.Date.context_today(self).replace(day=1),
            'date_to': fields.Date.context_today(self),
        })
        period.with_user(self.user_finance).action_compute()
        self.assertEqual(period.state, 'prepared')
        self.assertTrue(period.line_ids)
        with self.assertRaises(UserError):
            period.with_user(self.user_finance).action_review()
        period.with_user(self.user_owner).action_review()
        self.assertEqual(period.state, 'reviewed')
        period.with_user(self.user_owner).action_submit()
        self.assertEqual(period.state, 'submitted')
        self.assertEqual(period.company_id.tax_lock_date, period.date_to)

    # ----------------------------------------------------- payment run
    def test_payment_run_must_be_approved_before_payment(self):
        bill = self.env['account.move'].create({
            'move_type': 'in_invoice',
            'partner_id': self.vendor.id,
            'invoice_date': fields.Date.context_today(self) - timedelta(days=10),
            'invoice_date_due': fields.Date.context_today(self) + timedelta(days=5),
            'invoice_line_ids': [Command.create({
                'product_id': self.service.id,
                'name': self.service.name,
                'quantity': 1.0,
                'price_unit': 500.0,
                'tax_ids': [Command.clear()],
            })],
        })
        bill.action_post()
        journal = self.env['account.journal'].search([
            ('company_id', '=', self.company.id),
            ('type', '=', 'bank'),
        ], limit=1)
        if not journal:
            self.skipTest('No bank journal configured')
        run = self.env['vuma.payment.run'].create({
            'company_id': self.company.id,
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'date_to': fields.Date.context_today(self) + timedelta(days=10),
        })
        run.action_load_bills()
        self.assertIn(bill, run.line_ids.move_id)
        with self.assertRaises(UserError):
            run.action_create_payments()
        run.with_user(self.user_finance).action_approve()
        self.assertEqual(run.state, 'approved')

    # -------------------------------------------------- standing orders
    def test_standing_order_generates_quotation_and_advances(self):
        standing = self.env['vuma.standing.order'].create({
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'date_start': fields.Date.context_today(self),
            'date_next': fields.Date.context_today(self),
            'interval': 'monthly',
            'line_ids': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 2.0,
            })],
        })
        standing.action_generate_order()
        self.assertTrue(standing.last_order_id)
        self.assertEqual(standing.last_order_id.partner_id, self.customer)
        self.assertEqual(len(standing.last_order_id.order_line), 1)
        self.assertGreater(
            standing.date_next, fields.Date.context_today(self))
        self.assertEqual(
            standing.last_order_id.origin, 'Standing order %s' % standing.name)

    def test_repeat_order_copies_a_quotation(self):
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_uom_qty': 1.0,
                'price_unit': 100.0,
            })],
        })
        action = order.action_vuma_repeat_order()
        repeated = self.env['sale.order'].browse(action['res_id'])
        self.assertNotEqual(repeated.id, order.id)
        self.assertIn('Repeat of', repeated.origin or '')
        self.assertEqual(len(repeated.order_line), 1)

    # ---------------------------------------------------- tax ledger
    def test_tax_ledger_reconciles_to_tax_period(self):
        tax = self.env['account.tax'].search([
            ('company_id', '=', self.company.id),
            ('type_tax_use', '=', 'sale'),
            ('amount_type', '=', 'percent'),
        ], limit=1)
        if not tax:
            self.skipTest('No sale tax configured in this database')
        invoice = self._make_invoice(amount=1000.0, tax_ids=tax.ids)
        today = fields.Date.context_today(self)
        period = self.env['vuma.tax.period'].create({
            'company_id': self.company.id,
            'date_from': today.replace(day=1),
            'date_to': today,
        })
        period.action_compute()
        ledger = self.env['vuma.tax.ledger'].create({
            'company_id': self.company.id,
            'date_from': today.replace(day=1),
            'date_to': today,
            'direction': 'sale',
            'period_id': period.id,
        })
        ledger.action_compute()
        self.assertTrue(ledger.line_ids)
        self.assertIn(invoice.name, ledger.line_ids.mapped('move_name'))
        self.assertEqual(ledger.net_due, ledger.period_net)
        html, _report_type = self.env['ir.actions.report']._render_qweb_html(
            'vuma_retail_ops.action_report_tax_ledger', ledger.ids)
        self.assertIn(b'VAT Ledger Reconciliation', html)

    # ----------------------------------------------------------- reports
    def test_operational_reports_render(self):
        report_model = self.env['ir.actions.report']
        count = self.env['vuma.stock.count'].create({
            'warehouse_id': self.warehouse.id,
            'location_id': self.stock_location.id,
            'company_id': self.company.id,
        })
        self.env['vuma.stock.count.line'].create({
            'count_id': count.id,
            'product_id': self.product.id,
            'location_id': self.stock_location.id,
            'uom_id': self.product.uom_id.id,
            'system_qty': 2.0,
            'counted_qty': 2.0,
            'cost': 80.0,
            'is_counted': True,
        })
        html, _report_type = report_model._render_qweb_html(
            'vuma_retail_ops.action_report_stock_count', count.ids)
        self.assertIn(b'Stock Count Sheet', html)

        journal = self.env['account.journal'].search([
            ('company_id', '=', self.company.id),
            ('type', '=', 'bank'),
        ], limit=1)
        if not journal:
            self.skipTest('No bank journal configured')
        receipt = self.env['vuma.cash.receipt'].create({
            'receipt_type': 'customer',
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'journal_id': journal.id,
            'amount': 10.0,
            'reference': 'Report test',
        })
        html, _report_type = report_model._render_qweb_html(
            'vuma_retail_ops.action_report_cash_receipt', receipt.ids)
        self.assertIn(b'Cash Receipt', html)

        picking = self._make_outgoing_picking()
        picking.action_vuma_capture_pod({
            'name': 'Report Customer',
            'signature': TEST_PNG,
            'photo': TEST_PNG,
            'result': 'partial',
            'notes': 'One carton damaged',
        })
        html, _report_type = report_model._render_qweb_html(
            'vuma_retail_ops.action_report_pod', picking.ids)
        self.assertIn(b'Proof of Delivery', html)

        delivery_return = self.env['vuma.delivery.return'].create({
            'picking_id': picking.id,
            'partner_id': self.customer.id,
            'company_id': self.company.id,
            'product_id': self.product.id,
            'quantity': 1.0,
            'uom_id': self.product.uom_id.id,
            'price_unit': 100.0,
            'reason': 'damaged',
            'action': 'quarantine',
            'photo': TEST_PNG,
        })
        html, _report_type = report_model._render_qweb_html(
            'vuma_retail_ops.action_report_delivery_return',
            delivery_return.ids)
        self.assertIn(b'Delivery Return Note', html)

        payment_run = self.env['vuma.payment.run'].create({
            'company_id': self.company.id,
            'journal_id': journal.id,
            'date': fields.Date.context_today(self),
            'date_to': fields.Date.context_today(self),
        })
        html, _report_type = report_model._render_qweb_html(
            'vuma_retail_ops.action_report_payment_remittance',
            payment_run.ids)
        self.assertIn(b'Payment Remittance Advice', html)

        period = self.env['vuma.tax.period'].create({
            'company_id': self.company.id,
            'date_from': fields.Date.context_today(self).replace(day=1),
            'date_to': fields.Date.context_today(self),
        })
        html, _report_type = report_model._render_qweb_html(
            'vuma_retail_ops.action_report_tax_period', period.ids)
        self.assertIn(b'VAT Pack', html)

    # --------------------------------------------------- bank import
    def test_bank_statement_csv_import(self):
        journal = self.env['account.journal'].search([
            ('company_id', '=', self.company.id),
            ('type', '=', 'bank'),
        ], limit=1)
        if not journal:
            self.skipTest('No bank journal configured')
        csv_data = (
            'date,reference,amount,partner,description\n'
            '2026-10-01,CSV-TEST-1,1250.50,%s,Test receipt\n'
            % self.customer.name)
        wizard = self.env['vuma.bank.import'].create({
            'journal_id': journal.id,
            'file': base64.b64encode(csv_data.encode()).decode(),
            'filename': 'statement.csv',
        })
        wizard.action_import()
        line = self.env['account.bank.statement.line'].search([
            ('journal_id', '=', journal.id),
            ('payment_ref', '=', 'CSV-TEST-1'),
        ], limit=1)
        self.assertTrue(line)
        self.assertEqual(line.amount, 1250.50)
        self.assertEqual(line.partner_id, self.customer)

    # ------------------------------------------------------- field app
    def test_offline_field_action_is_idempotent(self):
        picking = self._make_outgoing_picking()
        client_uuid = str(uuid.uuid4())
        action = self.env['vuma.field.action'].receive('pod', client_uuid, {
            'picking_id': picking.id,
            'name': 'Field Customer',
            'signature': TEST_PNG,
            'result': 'full',
        })
        action.process()
        self.assertEqual(action.state, 'processed')
        duplicate = self.env['vuma.field.action'].receive('pod', client_uuid, {
            'picking_id': picking.id,
        })
        self.assertEqual(duplicate.state, 'duplicate')
        self.assertEqual(picking.vuma_pod_state, 'captured')

    def test_field_collection_accepts_string_invoice_id(self):
        invoice = self._make_invoice(amount=250.0)
        client_uuid = str(uuid.uuid4())
        action = self.env['vuma.field.action'].receive('collection', client_uuid, {
            'partner_id': self.customer.id,
            'amount': 250.0,
            'invoice_ids': str(invoice.id),
            'reference': 'Field receipt',
        })
        action.process()
        self.assertEqual(action.state, 'processed', action.error)
        receipt = self.env['vuma.cash.receipt'].search([
            ('reference', '=', 'Field receipt'),
        ], limit=1)
        self.assertTrue(receipt)
        self.assertEqual(receipt.invoice_ids, invoice)
