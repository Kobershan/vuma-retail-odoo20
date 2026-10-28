# -*- coding: utf-8 -*-
"""Delivery documents have to total the truck load correctly.

The tests create real invoices, pay them, build the document and render the
real QWeb report. A wrong weight, a missing financial split or a broken view
therefore fails here rather than at the loading bay.
"""
from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'vuma_picking_slip'
REPORT = 'vuma_picking_slip.action_report_delivery_document'


@tagged('post_install', '-at_install', MODULE)
class TestDeliveryDocument(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)],
            limit=1)
        assert cls.journal, 'this database needs a sales journal'
        cls.bank_journal = cls.env['account.journal'].search(
            [('type', 'in', ('bank', 'cash')),
             ('company_id', '=', cls.company.id)], limit=1)

        cls.customer = cls.env['res.partner'].create({
            'name': 'Delivery Document Customer',
            'customer_rank': 1,
        })
        cls.other_customer = cls.env['res.partner'].create({
            'name': 'Second Delivery Customer',
            'customer_rank': 1,
        })

        cls.widget = cls._product('Delivery Widget', 'DD-WIDGET', 2.0, 10.0)
        cls.bolt = cls._product('Delivery Bolt', 'DD-BOLT', 0.5, 10.0)
        cls.service = cls.env['product.product'].create({
            'name': 'Delivery Document Service',
            'type': 'service',
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })

        cls.invoice_one = cls._invoice([
            (cls.widget, 2),
            (cls.bolt, 1),
        ])
        cls.invoice_two = cls._invoice([
            (cls.widget, 3),
            (cls.service, 1),
        ], partner=cls.other_customer)

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _product(cls, name, code, weight, price):
        return cls.env['product.product'].create({
            'name': name,
            'default_code': code,
            'type': 'consu',
            'is_storable': True,
            'weight': weight,
            'list_price': price,
            'standard_price': price / 2.0,
            'categ_id': cls.env.ref('product.product_category_goods').id,
            'taxes_id': [Command.clear()],
        })

    @classmethod
    def _invoice(cls, lines, partner=None, post=True):
        invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': (partner or cls.customer).id,
            'invoice_date': fields.Date.context_today(cls.env.company),
            'journal_id': cls.journal.id,
            'invoice_line_ids': [
                Command.create({
                    'product_id': product.id,
                    'quantity': quantity,
                    'price_unit': product.list_price,
                    'tax_ids': [Command.clear()],
                })
                for product, quantity in lines
            ],
        })
        if post:
            invoice.action_post()
        return invoice

    def _pay(self, invoice, amount=None):
        self.assertTrue(self.bank_journal, 'this database needs a bank journal')
        wizard = self.env['account.payment.register'].with_context(
            active_model='account.move',
            active_ids=invoice.ids,
        ).create({
            'payment_date': fields.Date.context_today(self.env.user),
            'journal_id': self.bank_journal.id,
            'amount': amount if amount is not None else invoice.amount_residual,
        })
        wizard.action_create_payments()
        invoice.invalidate_recordset()
        return invoice

    def _document(self, invoices):
        return self.env['vuma.delivery.document'].create({
            'invoice_ids': [Command.set(invoices.ids)],
        })

    def _render(self, document):
        return self.env['ir.actions.report']._render_qweb_html(
            REPORT, document.ids)[0].decode()

    # --------------------------------------------------------- weight/value
    def test_weight_is_summed_from_every_product_on_every_invoice(self):
        document = self._document(self.invoice_one | self.invoice_two)

        # Widget: 2 + 3 at 2 kg = 10 kg. Bolt: 1 at 0.5 kg.
        self.assertAlmostEqual(document.total_weight, 10.5)
        self.assertEqual(len(document.get_weight_lines()), 2)
        widget_line = next(
            line for line in document.get_weight_lines()
            if line.product == self.widget)
        self.assertAlmostEqual(widget_line.quantity, 5.0)
        self.assertAlmostEqual(widget_line.weight, 10.0)

    def test_service_lines_do_not_add_weight_but_stay_on_the_invoice(self):
        document = self._document(self.invoice_two)
        self.assertAlmostEqual(document.total_weight, 6.0)
        self.assertIn(self.service,
                      document.invoice_ids.invoice_line_ids.product_id)
        self.assertNotIn(self.service,
                         [line.product for line in document.get_weight_lines()])

    def test_invoice_unit_of_measure_is_converted_before_weighting(self):
        dozen = self.env.ref('uom.product_uom_dozen')
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self.env.company),
            'journal_id': self.journal.id,
            'invoice_line_ids': [Command.create({
                'product_id': self.widget.id,
                'quantity': 1,
                'product_uom_id': dozen.id,
                'price_unit': self.widget.list_price,
                'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()

        document = self._document(invoice)

        self.assertAlmostEqual(document.total_weight, 24.0,
                               msg='one dozen of a 2 kg unit weighs 24 kg')

    def test_total_value_is_the_selling_value_of_the_invoices(self):
        document = self._document(self.invoice_one | self.invoice_two)

        # Invoice one: 2 widgets + 1 bolt at 10. Invoice two: 3 widgets and
        # one service at 100. No taxes in the fixture.
        self.assertAlmostEqual(document.total_value, 30.0 + 130.0)
        self.assertAlmostEqual(document.on_account_amount, 160.0)
        self.assertAlmostEqual(document.paid_upfront_amount, 0.0)

    # -------------------------------------------------------- cash vs credit
    def test_fully_paid_invoice_counts_as_paid_upfront(self):
        self._pay(self.invoice_one)
        document = self._document(self.invoice_one | self.invoice_two)

        self.assertAlmostEqual(document.paid_upfront_amount, 30.0)
        self.assertAlmostEqual(document.on_account_amount, 130.0)
        self.assertAlmostEqual(document.total_value, 160.0)

    def test_part_paid_invoice_is_split_between_cash_and_account(self):
        self._pay(self.invoice_two, amount=30.0)
        document = self._document(self.invoice_two)

        self.assertAlmostEqual(document.total_value, 130.0)
        self.assertAlmostEqual(document.paid_upfront_amount, 30.0)
        self.assertAlmostEqual(document.on_account_amount, 100.0)

    def test_draft_invoice_is_on_account(self):
        draft = self._invoice([(self.widget, 1)], post=False)
        document = self._document(draft)

        self.assertAlmostEqual(document.total_weight, 2.0)
        self.assertAlmostEqual(document.total_value, 10.0)
        self.assertAlmostEqual(document.paid_upfront_amount, 0.0)
        self.assertAlmostEqual(document.on_account_amount, 10.0)

    def test_removing_an_invoice_recalculates_the_load(self):
        document = self._document(self.invoice_one | self.invoice_two)
        document.invoice_ids = [Command.set(self.invoice_one.ids)]

        self.assertEqual(document.invoice_count, 1)
        self.assertAlmostEqual(document.total_weight, 4.5)
        self.assertAlmostEqual(document.total_value, 30.0)

    # ------------------------------------------------------------- workflow
    def test_create_action_builds_and_opens_a_document(self):
        action = (self.invoice_one | self.invoice_two) \
            .action_create_delivery_document()

        self.assertEqual(action['type'], 'ir.actions.act_window')
        self.assertEqual(action['res_model'], 'vuma.delivery.document')
        document = self.env['vuma.delivery.document'].browse(action['res_id'])
        self.assertTrue(document.exists())
        self.assertEqual(set(document.invoice_ids.ids),
                         set((self.invoice_one | self.invoice_two).ids))
        self.assertTrue(document.name.startswith('DD/'))
        self.assertEqual(document.generated_by_id, self.env.user)
        self.assertTrue(document.generated_at)

    def test_action_ignores_credit_notes_and_rejects_an_empty_selection(self):
        refund = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self.env.company),
            'journal_id': self.journal.id,
            'invoice_line_ids': [Command.create({
                'product_id': self.widget.id,
                'quantity': 1,
                'price_unit': 10.0,
                'tax_ids': [Command.clear()],
            })],
        })
        with self.assertRaisesRegex(UserError, 'at least one customer invoice'):
            refund.action_create_delivery_document()

        action = (self.invoice_one | refund).action_create_delivery_document()
        document = self.env['vuma.delivery.document'].browse(action['res_id'])
        self.assertEqual(document.invoice_ids, self.invoice_one)

    def test_print_action_uses_the_delivery_document_report(self):
        document = self._document(self.invoice_one)
        action = document.action_print_pdf()

        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'],
                         'vuma_picking_slip.report_delivery_document')
        self.assertEqual(action['context']['active_ids'], document.ids)

    # --------------------------------------------------------------- report
    def test_report_shows_load_weight_and_financial_split(self):
        self._pay(self.invoice_one)
        document = self._document(self.invoice_one | self.invoice_two)

        html = self._render(document)

        self.assertIn('Delivery Document', html)
        self.assertIn(document.name, html)
        self.assertIn(self.invoice_one.name, html)
        self.assertIn(self.invoice_two.name, html)
        self.assertIn(self.customer.name, html)
        self.assertIn(self.other_customer.name, html)
        self.assertIn(self.widget.name, html)
        self.assertIn('Total Weight', html)
        self.assertIn('10.50', html)
        self.assertIn('Paid Upfront', html)
        self.assertIn('On Account', html)
        self.assertIn(self.env.user.name, html)

    # ---------------------------------------------------------------- views
    def test_customer_invoice_list_has_the_delivery_button(self):
        view = self.env['account.move'].get_view(
            view_id=self.env.ref('account.view_out_invoice_tree').id,
            view_type='list')
        self.assertIn('action_create_delivery_document', view['arch'])
        self.assertIn('Create Delivery Document', view['arch'])

    def test_document_is_listed_under_sales(self):
        menu = self.env.ref('vuma_picking_slip.menu_delivery_document')
        self.assertEqual(menu.parent_id, self.env.ref('sale.sale_menu_root'))
        self.assertEqual(menu.action,
                         self.env.ref('vuma_picking_slip.action_delivery_document'))
