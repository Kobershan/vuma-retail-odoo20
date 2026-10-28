# -*- coding: utf-8 -*-
"""The consolidated picking slip has to add invoices together correctly.

The tests build real customer invoices, save real picking slips and render the
real QWeb reports, so a broken view, a missing field or a duplicated product
line fails here rather than in the warehouse.
"""
from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'vuma_picking_slip'
INVOICE_REPORT = 'vuma_picking_slip.action_report_consolidated_picking_slip'
SLIP_REPORT = 'vuma_picking_slip.action_report_picking_slip'


@tagged('post_install', '-at_install', MODULE)
class TestConsolidatedPickingSlip(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        assert cls.journal, 'this database needs a sales journal'

        cls.customer = cls.env['res.partner'].create({
            'name': 'Picking Slip Customer',
            'customer_rank': 1,
        })
        cls.other_customer = cls.env['res.partner'].create({
            'name': 'Second Picking Slip Customer',
            'customer_rank': 1,
        })

        cls.widget = cls._product('Picking Slip Widget', 'PS-WIDGET')
        cls.bolt = cls._product('Picking Slip Bolt', 'PS-BOLT')
        cls.nut = cls._product('Picking Slip Nut', 'PS-NUT')
        cls.service = cls.env['product.product'].create({
            'name': 'Picking Slip Delivery Service',
            'type': 'service',
            'list_price': 5.0,
        })

        cls.invoice_one = cls._invoice([
            (cls.widget, 2),
            (cls.bolt, 1),
            (cls.widget, 0),          # zero line: must not reach the slip
        ])
        cls.invoice_two = cls._invoice([
            (cls.widget, 3),
            (cls.nut, 1),
            (cls.service, 4),         # service: must not reach the slip
        ], partner=cls.other_customer)

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _product(cls, name, code):
        return cls.env['product.product'].create({
            'name': name,
            'default_code': code,
            'barcode': code,
            'type': 'consu',
            'is_storable': True,
            'list_price': 10.0,
            'standard_price': 5.0,
            'categ_id': cls.env.ref('product.product_category_goods').id,
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
                })
                for product, quantity in lines
            ],
        })
        if post:
            invoice.action_post()
        return invoice

    def _render(self, invoices):
        return self.env['ir.actions.report']._render_qweb_html(
            INVOICE_REPORT, invoices.ids)[0].decode()

    def _render_slip(self, slip):
        return self.env['ir.actions.report']._render_qweb_html(
            SLIP_REPORT, slip.ids)[0].decode()

    def _slip(self):
        return self.env['vuma.picking.slip'].create_from_invoices(
            self.invoice_one | self.invoice_two)

    # ----------------------------------------------------------- aggregation
    def test_same_product_is_consolidated_across_invoices(self):
        data = (self.invoice_one | self.invoice_two).get_consolidated_picking_data()

        self.assertEqual(len(data.lines), 3,
                         'zero, service and note lines must be left off')
        by_product = {line.product: line for line in data.lines}

        self.assertAlmostEqual(by_product[self.widget].quantity, 5.0)
        self.assertAlmostEqual(by_product[self.bolt].quantity, 1.0)
        self.assertAlmostEqual(by_product[self.nut].quantity, 1.0)
        self.assertNotIn(self.service, by_product)
        self.assertFalse(data.multi_company)
        self.assertTrue(data.has_barcode)

    def test_invoices_are_kept_on_the_slip(self):
        data = (self.invoice_one | self.invoice_two).get_consolidated_picking_data()

        self.assertEqual(len(data.invoices), 2)
        self.assertEqual(data.invoices.partner_id, self.customer | self.other_customer)
        self.assertIn(self.invoice_one.name, data.invoices.mapped('name'))
        self.assertIn(self.invoice_two.name, data.invoices.mapped('name'))

    def test_draft_invoice_can_be_picked(self):
        draft = self._invoice([(self.widget, 1)], post=False)
        data = (self.invoice_one | draft).get_consolidated_picking_data()

        by_product = {line.product: line for line in data.lines}
        self.assertEqual(by_product[self.widget].quantity, 3.0,
                         'the draft invoice must contribute its widget')
        self.assertIn(draft, data.invoices)
        self.assertIn('Draft', self._render(self.invoice_one | draft))

    def test_mixed_company_lines_stay_on_separate_lines(self):
        """Different stock companies cannot be silently added together."""
        if 'vuma_source_company_id' not in self.env['account.move.line']._fields:
            self.skipTest('vuma_multi_tenant is not installed')

        other_company = self.env['res.company'].create({
            'name': 'Picking Slip Source Company',
        })
        source_invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self.env.company),
            'journal_id': self.journal.id,
            'invoice_line_ids': [Command.create({
                'product_id': self.widget.id,
                'quantity': 4,
                'price_unit': 10.0,
                'vuma_source_company_id': other_company.id,
            })],
        })
        source_invoice.action_post()

        data = (self.invoice_one | self.invoice_two
                | source_invoice).get_consolidated_picking_data()
        widget_lines = [line for line in data.lines if line.product == self.widget]

        self.assertEqual(len(widget_lines), 2,
                         'one widget line per source company')
        self.assertEqual({line.company.id for line in widget_lines},
                         {self.company.id, other_company.id})
        self.assertTrue(data.multi_company)

    def test_source_location_quantities_are_shown(self):
        if 'vuma_source_location_id' not in self.env['account.move.line']._fields:
            self.skipTest('vuma_multi_tenant is not installed')

        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.company.id)], limit=1)
        self.assertTrue(warehouse, 'this database needs a warehouse')
        overflow = self.env['stock.location'].create({
            'name': 'Picking Slip Overflow',
            'location_id': warehouse.view_location_id.id,
            'usage': 'internal',
        })
        source_invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self.env.company),
            'journal_id': self.journal.id,
            'invoice_line_ids': [
                Command.create({
                    'product_id': self.widget.id,
                    'quantity': 4,
                    'price_unit': 10.0,
                    'vuma_source_location_id': warehouse.lot_stock_id.id,
                }),
                Command.create({
                    'product_id': self.widget.id,
                    'quantity': 1,
                    'price_unit': 10.0,
                    'vuma_source_location_id': overflow.id,
                }),
            ],
        })
        source_invoice.action_post()

        data = source_invoice.get_consolidated_picking_data()
        widget_line = next(line for line in data.lines
                           if line.product == self.widget)
        self.assertEqual(widget_line.quantity, 5.0)
        self.assertIn(warehouse.lot_stock_id.display_name,
                      widget_line.source_label)
        self.assertIn(overflow.display_name, widget_line.source_label)
        self.assertIn('(4)', widget_line.source_label)
        self.assertIn('(1)', widget_line.source_label)

    # --------------------------------------------------------------- errors
    def test_no_selection_raises_a_friendly_error(self):
        with self.assertRaisesRegex(UserError, 'at least one customer invoice'):
            self.env['account.move'].get_consolidated_picking_data()

    def test_credit_note_is_not_picked(self):
        refund = self.env['account.move'].create({
            'move_type': 'out_refund',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self.env.company),
            'journal_id': self.journal.id,
            'invoice_line_ids': [Command.create({
                'product_id': self.widget.id, 'quantity': 1, 'price_unit': 10.0,
            })],
        })
        with self.assertRaisesRegex(UserError, 'at least one customer invoice'):
            refund.action_create_consolidated_picking_slip()

    def test_service_only_invoice_raises_a_friendly_error(self):
        service_invoice = self._invoice([(self.service, 2)])
        with self.assertRaisesRegex(UserError, 'no storable products'):
            service_invoice.action_create_consolidated_picking_slip()

    # ------------------------------------------------------- saved slip flow
    def test_create_action_saves_a_slip_and_opens_the_tablet(self):
        action = (self.invoice_one | self.invoice_two).action_create_consolidated_picking_slip()

        self.assertEqual(action['type'], 'ir.actions.client')
        self.assertEqual(action['tag'], 'vuma_picking_slip.tablet')
        slip = self.env['vuma.picking.slip'].browse(action['params']['slip_id'])
        self.assertTrue(slip.exists())
        self.assertEqual(set(slip.invoice_ids.ids),
                         set((self.invoice_one | self.invoice_two).ids))
        self.assertEqual(len(slip.line_ids), 3)
        widget_line = slip.line_ids.filtered(
            lambda line: line.product_id == self.widget)
        self.assertEqual(widget_line.demand_qty, 5.0)
        self.assertTrue(slip.name.startswith('PS/'))

    def test_print_action_still_returns_the_invoice_pdf(self):
        action = (self.invoice_one | self.invoice_two).action_print_consolidated_picking_slip()

        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'],
                         'vuma_picking_slip.report_consolidated_picking_slip')
        self.assertEqual(
            sorted(action['context']['active_ids']),
            sorted((self.invoice_one | self.invoice_two).ids))

    def test_capture_quantities_and_finish(self):
        slip = self._slip()
        self.assertEqual(slip.state, 'draft')
        self.assertEqual(slip.progress, 0.0)

        widget_line = slip.line_ids.filtered(
            lambda line: line.product_id == self.widget)
        widget_line.write({'picked_qty': 4.0, 'is_picked': True})

        self.assertEqual(slip.state, 'in_progress',
                         'the first captured line starts the pick')
        self.assertEqual(slip.picked_count, 1)
        self.assertEqual(slip.short_count, 1)
        self.assertTrue(widget_line.is_short)
        self.assertAlmostEqual(slip.progress, 100.0 / 3.0, places=2)

        with self.assertRaisesRegex(UserError, 'still need to be marked'):
            slip.action_finish()

        for line in slip.line_ids - widget_line:
            line.write({
                'picked_qty': line.demand_qty,
                'is_picked': True,
            })
        slip.action_finish()

        self.assertEqual(slip.state, 'done')
        self.assertEqual(slip.finished_by_id, self.env.user)
        self.assertTrue(slip.finished_date)
        self.assertEqual(slip.picked_count, 3)

    def test_mark_all_picked_fills_every_line(self):
        slip = self._slip()
        slip.action_mark_all_picked()

        self.assertEqual(slip.state, 'in_progress')
        self.assertEqual(slip.picked_count, 3)
        self.assertEqual(slip.short_count, 0)
        self.assertEqual(slip.progress, 100.0)
        for line in slip.line_ids:
            self.assertEqual(line.picked_qty, line.demand_qty)
            self.assertTrue(line.is_picked)

    def test_reopen_clears_the_finish_details(self):
        slip = self._slip()
        slip.action_mark_all_picked()
        slip.action_finish()
        self.assertEqual(slip.state, 'done')

        slip.action_reopen()

        self.assertEqual(slip.state, 'in_progress')
        self.assertFalse(slip.finished_date)
        self.assertFalse(slip.finished_by_id)

    def test_tablet_action_points_at_the_saved_slip(self):
        slip = self._slip()
        action = slip.action_open_tablet()

        self.assertEqual(action['tag'], 'vuma_picking_slip.tablet')
        self.assertEqual(action['params']['slip_id'], slip.id)

    def test_print_pdf_action_uses_the_saved_slip_report(self):
        slip = self._slip()
        action = slip.action_print_pdf()

        self.assertEqual(action['type'], 'ir.actions.report')
        self.assertEqual(action['report_name'],
                         'vuma_picking_slip.report_picking_slip')
        self.assertEqual(action['context']['active_ids'], slip.ids)

    # --------------------------------------------------------------- reports
    def test_report_renders_one_line_per_product(self):
        html = self._render(self.invoice_one | self.invoice_two)

        self.assertIn('Consolidated Picking Slip', html)
        self.assertIn(self.invoice_one.name, html)
        self.assertIn(self.invoice_two.name, html)
        self.assertIn(self.customer.name, html)
        self.assertIn(self.other_customer.name, html)
        self.assertIn(self.widget.name, html)
        self.assertIn(self.bolt.name, html)
        self.assertIn(self.nut.name, html)
        self.assertNotIn(self.service.name, html,
                         'a service must not be printed as a warehouse item')

        # The widget is on both invoices but appears once in the item table.
        self.assertEqual(html.count(self.widget.name), 1)
        self.assertIn('Picking Slip Bolt', html)

    def test_saved_slip_pdf_shows_captured_and_short_quantities(self):
        slip = self._slip()
        widget_line = slip.line_ids.filtered(
            lambda line: line.product_id == self.widget)
        widget_line.write({'picked_qty': 4.0, 'is_picked': True})

        html = self._render_slip(slip)

        self.assertIn('Consolidated Picking Slip', html)
        self.assertIn(slip.name, html)
        self.assertIn(self.invoice_one.name, html)
        self.assertIn(self.widget.name, html)
        self.assertIn('Short', html)
        self.assertIn('4.00', html)
        self.assertIn('5.00', html)

    def test_report_action_definitions(self):
        invoice_report = self.env.ref(INVOICE_REPORT)
        self.assertEqual(invoice_report.model, 'account.move')
        self.assertEqual(invoice_report.report_type, 'qweb-pdf')

        slip_report = self.env.ref(SLIP_REPORT)
        self.assertEqual(slip_report.model, 'vuma.picking.slip')
        self.assertEqual(slip_report.report_type, 'qweb-pdf')

    def test_customer_invoice_list_has_both_buttons(self):
        view = self.env['account.move'].get_view(
            view_id=self.env.ref('account.view_out_invoice_tree').id,
            view_type='list')
        self.assertIn('action_create_consolidated_picking_slip', view['arch'])
        self.assertIn('action_print_consolidated_picking_slip', view['arch'])
        self.assertIn('Create Picking Slip', view['arch'])
        self.assertIn('Print Picking PDF', view['arch'])

    def test_menu_is_under_sales(self):
        menu = self.env.ref('vuma_picking_slip.menu_picking_slip')
        self.assertEqual(menu.parent_id,
                         self.env.ref('sale.sale_menu_root'))
        self.assertEqual(menu.action,
                         self.env.ref('vuma_picking_slip.action_picking_slip'))
