# -*- coding: utf-8 -*-
"""Delivery documents have to total the truck load correctly.

The tests create real invoices, pay them, build the document and render the
real QWeb report. A wrong weight, a missing financial split or a broken view
therefore fails here rather than at the loading bay.
"""
import re

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
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

        # Real fleet and employee records: the new fields are foreign keys
        # into them, so the fixtures have to be the genuine models rather
        # than stand-ins.
        cls.vehicle = cls.env['fleet.vehicle'].create({
            'license_plate': 'VD-TEST-01',
        })
        cls.driver = cls.env['hr.employee'].create({
            'name': 'Vuma Test Driver',
        })
        cls.helper_one = cls.env['hr.employee'].create({
            'name': 'Vuma Test Helper One',
        })
        cls.helper_two = cls.env['hr.employee'].create({
            'name': 'Vuma Test Helper Two',
        })
        cls.helper_three = cls.env['hr.employee'].create({
            'name': 'Vuma Test Helper Three',
        })

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

    @staticmethod
    def _text(html):
        """The rendered sheet as plain text, so assertions read as printed.

        Fields render inside tags (`goods <span>10.50</span>`), so a plain
        substring check on the raw HTML would never match the sentence a
        dispatcher actually reads.
        """
        return re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', '', html)).strip()

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

    # ------------------------------------------------------------- pallets
    def test_pallet_line_weight_is_quantity_times_weight_per_pallet(self):
        document = self._document(self.invoice_one)
        pallet = self.env['vuma.delivery.pallet'].create({
            'document_id': document.id,
            'name': 'Widgets on one pallet',
            'quantity': 3.0,
            'weight_per_pallet': 40.0,
        })

        self.assertAlmostEqual(pallet.line_weight, 120.0)

    def test_document_totals_sum_every_pallet_line(self):
        document = self._document(self.invoice_one)
        self.env['vuma.delivery.pallet'].create([
            {'document_id': document.id, 'name': 'Widget pallets',
             'quantity': 3.0, 'weight_per_pallet': 40.0},
            {'document_id': document.id, 'name': 'Bolt pallets',
             'quantity': 2.0, 'weight_per_pallet': 25.0},
        ])

        self.assertAlmostEqual(document.total_pallet_quantity, 5.0)
        self.assertAlmostEqual(document.total_pallet_weight, 3 * 40.0 + 2 * 25.0)

    def test_pallet_weight_is_added_to_the_load_weight_only(self):
        document = self._document(self.invoice_one | self.invoice_two)
        self.env['vuma.delivery.pallet'].create({
            'document_id': document.id, 'name': 'Mixed pallets',
            'quantity': 4.0, 'weight_per_pallet': 100.0,
        })

        # The goods weight stays the invoiced-product weight alone, so the
        # components can always be read side by side without ambiguity...
        self.assertAlmostEqual(document.total_weight, 10.5)
        self.assertAlmostEqual(document.total_pallet_weight, 400.0)
        # ...and the load weight is the sum, which is what the truck
        # actually weighs.
        self.assertAlmostEqual(document.total_load_weight, 10.5 + 400.0)

    def test_load_weight_equals_goods_weight_when_no_pallets_are_entered(self):
        document = self._document(self.invoice_one | self.invoice_two)

        self.assertAlmostEqual(document.total_pallet_weight, 0.0)
        self.assertAlmostEqual(document.total_load_weight, 10.5)

    def test_load_weight_recalculates_when_a_pallet_changes(self):
        document = self._document(self.invoice_one)
        pallet = self.env['vuma.delivery.pallet'].create({
            'document_id': document.id, 'name': 'One pallet',
            'quantity': 1.0, 'weight_per_pallet': 90.0,
        })
        # invoice_one goods weigh 4.5 kg.
        self.assertAlmostEqual(document.total_load_weight, 4.5 + 90.0)

        pallet.write({'quantity': 2.0, 'weight_per_pallet': 95.0})
        self.assertAlmostEqual(document.total_load_weight, 4.5 + 190.0)

        pallet.unlink()
        self.assertAlmostEqual(document.total_load_weight, 4.5)

    def test_editing_or_removing_a_pallet_recalculates_the_totals(self):
        document = self._document(self.invoice_one)
        pallet = self.env['vuma.delivery.pallet'].create({
            'document_id': document.id, 'name': 'One pallet',
            'quantity': 1.0, 'weight_per_pallet': 90.0,
        })
        self.assertAlmostEqual(document.total_pallet_quantity, 1.0)
        self.assertAlmostEqual(document.total_pallet_weight, 90.0)

        pallet.write({'quantity': 2.0, 'weight_per_pallet': 95.0})
        self.assertAlmostEqual(document.total_pallet_quantity, 2.0)
        self.assertAlmostEqual(document.total_pallet_weight, 190.0)

        pallet.unlink()
        self.assertAlmostEqual(document.total_pallet_quantity, 0.0)
        self.assertAlmostEqual(document.total_pallet_weight, 0.0)

    def test_pallet_lines_are_cascaded_when_the_document_is_deleted(self):
        document = self._document(self.invoice_one)
        self.env['vuma.delivery.pallet'].create({
            'document_id': document.id, 'name': 'Doomed pallet',
            'quantity': 1.0, 'weight_per_pallet': 10.0,
        })

        document.unlink()

        self.assertFalse(self.env['vuma.delivery.pallet'].search(
            [('name', '=', 'Doomed pallet')]))

    def test_report_shows_pallets_with_quantity_and_weight(self):
        # Both invoices: the goods weigh 10.5 kg, so the figure is easy to
        # tell apart from the 620 kg of pallets below.
        document = self._document(self.invoice_one | self.invoice_two)
        document.write({'pallet_ids': [Command.create({
            'name': 'Cola 2L -- Shoprite',
            'quantity': 4.0,
            'weight_per_pallet': 155.0,
        })]})

        html = self._render(document)

        self.assertIn('Pallets', html)
        self.assertIn('Cola 2L -- Shoprite', html)
        self.assertIn('155.0', html)
        # The pallet total: 4 x 155 = 620 kg, shown on its own row and in
        # the KPI strip.
        self.assertIn('620', html)
        # Load Weight is the headline figure: 10.5 goods + 620 pallets.
        self.assertIn('630.50', html)
        self.assertIn('Load Weight', html)
        # The goods weight is still shown as its own component.
        self.assertIn('10.50', html)
        # The KPI strip widens to five cells once pallets are present.
        self.assertIn('kpi has-pallets', html)

    def test_report_has_no_pallet_section_when_none_are_entered(self):
        document = self._document(self.invoice_one)

        html = self._render(document)

        # The section, its column heading and the KPI cell all stay hidden.
        self.assertNotIn('Weight per Pallet', html)
        # Match the rendered label, not the bare word: the word also occurs
        # in the stylesheet's class names and comments.
        self.assertNotIn('>Pallets<', html)
        # The CSS rule for the widened KPI strip is always in the stylesheet,
        # so assert the class is not actually applied to the table.
        self.assertNotIn('kpi has-pallets', html)

    # ---------------------------------------------------------------- boxes
    def test_box_line_weight_is_quantity_times_weight_per_box(self):
        document = self._document(self.invoice_one)
        box = self.env['vuma.delivery.box'].create({
            'document_id': document.id,
            'name': 'Widget cartons',
            'quantity': 3.0,
            'weight_per_box': 40.0,
        })

        self.assertAlmostEqual(box.line_weight, 120.0)

    def test_document_totals_sum_every_box_line(self):
        document = self._document(self.invoice_one)
        self.env['vuma.delivery.box'].create([
            {'document_id': document.id, 'name': 'Widget cartons',
             'quantity': 3.0, 'weight_per_box': 40.0},
            {'document_id': document.id, 'name': 'Bolt cartons',
             'quantity': 2.0, 'weight_per_box': 25.0},
        ])

        self.assertAlmostEqual(document.total_box_quantity, 5.0)
        self.assertAlmostEqual(document.total_box_weight, 3 * 40.0 + 2 * 25.0)

    def test_box_weight_is_added_to_the_load_weight_only(self):
        document = self._document(self.invoice_one | self.invoice_two)
        self.env['vuma.delivery.box'].create({
            'document_id': document.id, 'name': 'Mixed cartons',
            'quantity': 4.0, 'weight_per_box': 100.0,
        })

        # The goods weight stays the invoiced-product weight alone, so the
        # components can always be read side by side...
        self.assertAlmostEqual(document.total_weight, 10.5)
        self.assertAlmostEqual(document.total_box_weight, 400.0)
        self.assertAlmostEqual(document.total_pallet_weight, 0.0)
        # ...and the load weight adds them once.
        self.assertAlmostEqual(document.total_load_weight, 10.5 + 400.0)

    def test_load_weight_is_goods_plus_boxes_plus_pallets_counted_once(self):
        document = self._document(self.invoice_one | self.invoice_two)
        self.env['vuma.delivery.box'].create({
            'document_id': document.id, 'name': 'Cartons',
            'quantity': 4.0, 'weight_per_box': 100.0,
        })
        self.env['vuma.delivery.pallet'].create({
            'document_id': document.id, 'name': 'Pallets',
            'quantity': 4.0, 'weight_per_pallet': 100.0,
        })

        # Nothing is folded into anything else: all three survive intact...
        self.assertAlmostEqual(document.total_weight, 10.5)
        self.assertAlmostEqual(document.total_box_weight, 400.0)
        self.assertAlmostEqual(document.total_pallet_weight, 400.0)
        # ...and the headline figure is their sum, each counted exactly once.
        self.assertAlmostEqual(document.total_load_weight,
                               10.5 + 400.0 + 400.0)

    def test_editing_or_removing_a_box_recalculates_the_totals(self):
        document = self._document(self.invoice_one)
        box = self.env['vuma.delivery.box'].create({
            'document_id': document.id, 'name': 'One carton',
            'quantity': 1.0, 'weight_per_box': 90.0,
        })
        self.assertAlmostEqual(document.total_box_quantity, 1.0)
        self.assertAlmostEqual(document.total_box_weight, 90.0)

        box.write({'quantity': 2.0, 'weight_per_box': 95.0})
        self.assertAlmostEqual(document.total_box_quantity, 2.0)
        self.assertAlmostEqual(document.total_box_weight, 190.0)

        box.unlink()
        self.assertAlmostEqual(document.total_box_quantity, 0.0)
        self.assertAlmostEqual(document.total_box_weight, 0.0)

    def test_box_lines_are_cascaded_when_the_document_is_deleted(self):
        document = self._document(self.invoice_one)
        self.env['vuma.delivery.box'].create({
            'document_id': document.id, 'name': 'Doomed carton',
            'quantity': 1.0, 'weight_per_box': 10.0,
        })

        document.unlink()

        self.assertFalse(self.env['vuma.delivery.box'].search(
            [('name', '=', 'Doomed carton')]))

    def test_report_shows_boxes_with_quantity_and_weight(self):
        document = self._document(self.invoice_one | self.invoice_two)
        document.write({'box_ids': [Command.create({
            'name': 'Soap -- 12 per carton',
            'quantity': 8.0,
            'weight_per_box': 15.0,
        })]})

        html = self._render(document)

        self.assertIn('Cardboard Boxes', html)
        self.assertIn('Soap -- 12 per carton', html)
        self.assertIn('15.0', html)
        # 8 boxes x 15 kg = 120 kg, shown on its own total row and in the
        # KPI strip.
        self.assertIn('120', html)
        # Load Weight adds that once to the 10.5 kg of goods.
        self.assertIn('130.50', html)
        self.assertIn('Load Weight', html)
        self.assertIn('kpi has-boxes', html)
        self.assertNotIn('kpi has-pallets', html)

    def test_report_has_no_box_section_when_none_are_entered(self):
        document = self._document(self.invoice_one)

        html = self._render(document)

        self.assertNotIn('Weight per Box', html)
        self.assertNotIn('>Cardboard Boxes<', html)
        self.assertNotIn('kpi has-boxes', html)

    def test_report_kpi_fits_every_cell_when_boxes_and_pallets_both_exist(self):
        document = self._document(self.invoice_one | self.invoice_two)
        document.write({
            'box_ids': [Command.create({
                'name': 'Cartons', 'quantity': 4.0, 'weight_per_box': 100.0,
            })],
            'pallet_ids': [Command.create({
                'name': 'Pallets', 'quantity': 4.0,
                'weight_per_pallet': 100.0,
            })],
        })

        html = self._render(document)

        # Six KPI cells need the layout class that shrinks each of them; the
        # narrower two-cell classes must not be applied instead.
        self.assertIn('kpi has-both', html)
        self.assertNotIn('kpi has-pallets', html)
        self.assertNotIn('kpi has-boxes', html)
        # 10.5 goods + 400 boxes + 400 pallets.
        self.assertIn('810.50', html)
        # The breakdown stays on the sheet, so a reader can reconcile it.
        text = self._text(html)
        self.assertIn('goods 10.5', text)
        self.assertIn('+ boxes 400.0', text)
        self.assertIn('+ pallets 400.0', text)

    # ----------------------------------------------------------------- crew
    def test_crew_count_is_the_driver_plus_every_helper(self):
        document = self._document(self.invoice_one)
        self.assertEqual(document.crew_count, 0)

        document.write({
            'driver_id': self.driver.id,
            'helper_ids': [Command.set([self.helper_one.id,
                                        self.helper_two.id])],
        })

        self.assertEqual(document.crew_count, 3)

    def test_a_truck_carries_at_most_three_people(self):
        document = self._document(self.invoice_one)
        document.write({
            'driver_id': self.driver.id,
            'helper_ids': [Command.set([self.helper_one.id,
                                        self.helper_two.id])],
        })
        self.assertEqual(document.crew_count, 3)

        with self.assertRaisesRegex(ValidationError, 'at most three people'):
            document.write({
                'helper_ids': [Command.set([self.helper_one.id,
                                            self.helper_two.id,
                                            self.helper_three.id])],
            })

    def test_the_driver_cannot_be_counted_twice_as_a_helper(self):
        document = self._document(self.invoice_one)
        document.write({'driver_id': self.driver.id})

        with self.assertRaisesRegex(ValidationError, 'cannot also be listed'):
            document.write({
                'helper_ids': [Command.set([self.helper_one.id,
                                            self.driver.id])],
            })

    def test_crew_slots_list_the_vehicle_driver_and_helpers_in_order(self):
        document = self._document(self.invoice_one)
        document.write({
            'vehicle_id': self.vehicle.id,
            'driver_id': self.driver.id,
            'helper_ids': [Command.set([self.helper_one.id])],
        })

        self.assertEqual(
            [(slot.label, slot.value) for slot in document.get_crew_slots()],
            [
                ('Vehicle', self.vehicle.display_name),
                ('Driver', self.driver.name),
                ('Helper 1', self.helper_one.name),
            ])
        # Three cells share the strip.
        self.assertEqual(
            {slot.style for slot in document.get_crew_slots()},
            {'width: 33%;'})

    def test_crew_slots_show_a_dash_for_a_slot_that_was_never_filled_in(self):
        document = self._document(self.invoice_one)

        self.assertEqual(
            [(slot.label, slot.value) for slot in document.get_crew_slots()],
            [('Vehicle', '-'), ('Driver', '-')])
        # Two cells share the strip.
        self.assertEqual(
            {slot.style for slot in document.get_crew_slots()},
            {'width: 50%;'})

    def test_report_shows_the_vehicle_and_every_member_of_the_crew(self):
        document = self._document(self.invoice_one)
        document.write({
            'vehicle_id': self.vehicle.id,
            'driver_id': self.driver.id,
            'helper_ids': [Command.set([self.helper_one.id,
                                        self.helper_two.id])],
        })

        html = self._render(document)

        self.assertIn('Truck and crew', html)
        self.assertIn(self.vehicle.display_name, html)
        self.assertIn(self.driver.name, html)
        self.assertIn(self.helper_one.name, html)
        self.assertIn(self.helper_two.name, html)
        self.assertIn('Helper 1', html)
        self.assertIn('Helper 2', html)
        # Vehicle, Driver, Helper 1, Helper 2 -- three people on the truck.
        self.assertEqual(len(document.get_crew_slots()), 4)
        # Four cells share the strip.
        self.assertEqual(
            {slot.style for slot in document.get_crew_slots()},
            {'width: 25%;'})

    def test_report_keeps_the_crew_block_when_nothing_is_assigned_yet(self):
        document = self._document(self.invoice_one)

        html = self._render(document)

        # Printed even when empty: a blank slot is a prompt to fill it in
        # before the truck leaves, not something to hide.
        self.assertIn('Truck and crew', html)
        self.assertIn('Driver', html)
        self.assertEqual(
            [(slot.label, slot.value) for slot in document.get_crew_slots()],
            [('Vehicle', '-'), ('Driver', '-')])

    def test_helper_domain_does_not_hide_employees_before_a_driver_is_chosen(self):
        # The form filters helpers with [('id', '!=', driver_id)]. Before a
        # driver is picked that collapses to [('id', '!=', False)], which has
        # to keep every employee selectable rather than emptying the dropdown.
        employees = self.env['hr.employee'].search([('id', '!=', False)])

        self.assertIn(self.driver, employees)
        self.assertIn(self.helper_one, employees)
        self.assertIn(self.helper_two, employees)

    def test_stock_user_can_read_and_reassign_the_crew(self):
        """Dispatch is a stock user, and neither fleet nor hr grants them
        rights by default -- without this module's own access rules every one
        of these fields raises AccessError for the people who use them."""
        stock_user = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'Vuma Dispatch Stock User',
                'login': 'vuma.dispatch.stock.test',
                'email': 'dispatch@vuma.test',
                'group_ids': [Command.set([
                    self.env.ref('base.group_user').id,
                    self.env.ref('stock.group_stock_user').id,
                ])],
            })
        document = self._document(self.invoice_one)
        document.write({
            'vehicle_id': self.vehicle.id,
            'driver_id': self.driver.id,
            'helper_ids': [Command.set([self.helper_one.id,
                                        self.helper_two.id])],
            'box_ids': [Command.create({
                'name': 'Cartons', 'quantity': 2.0, 'weight_per_box': 15.0,
            })],
        })

        # Reading the linked records themselves, not just their ids: that is
        # what actually triggers the access check on fleet and hr.
        self.assertEqual(
            self.env['fleet.vehicle'].with_user(stock_user)
            .browse(self.vehicle.id).display_name,
            self.vehicle.display_name)
        self.assertEqual(
            self.env['hr.employee'].with_user(stock_user)
            .browse(self.driver.id).display_name,
            self.driver.name)

        record = document.with_user(stock_user).read([
            'name', 'vehicle_id', 'driver_id', 'helper_ids', 'crew_count',
            'total_load_weight', 'total_box_weight', 'box_ids',
        ])[0]
        self.assertEqual(record['crew_count'], 3)
        self.assertEqual(len(record['helper_ids']), 2)
        self.assertAlmostEqual(record['total_box_weight'], 30.0)

        # Writing the crew back is allowed too, not just reading it.
        document.with_user(stock_user).write({
            'driver_id': self.helper_three.id,
        })
        self.assertEqual(document.driver_id, self.helper_three)

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
        self.assertIn('Load Weight', html)
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

    def test_document_form_has_an_editable_pallets_tab(self):
        view = self.env['vuma.delivery.document'].get_view(
            view_id=self.env.ref(
                'vuma_picking_slip.view_vuma_delivery_document_form').id,
            view_type='form')
        self.assertIn('Pallets', view['arch'])
        self.assertIn('pallet_ids', view['arch'])
        self.assertIn('weight_per_pallet', view['arch'])
        self.assertIn('total_pallet_quantity', view['arch'])
        self.assertIn('total_load_weight', view['arch'])

    def test_document_form_has_a_boxes_tab_and_a_truck_and_crew_group(self):
        view = self.env['vuma.delivery.document'].get_view(
            view_id=self.env.ref(
                'vuma_picking_slip.view_vuma_delivery_document_form').id,
            view_type='form')

        self.assertIn('Boxes', view['arch'])
        self.assertIn('box_ids', view['arch'])
        self.assertIn('weight_per_box', view['arch'])
        self.assertIn('total_box_quantity', view['arch'])
        self.assertIn('Truck and Crew', view['arch'])
        self.assertIn('vehicle_id', view['arch'])
        self.assertIn('driver_id', view['arch'])
        self.assertIn('helper_ids', view['arch'])
        self.assertIn('crew_count', view['arch'])
        # Helpers are filtered so the driver cannot be picked twice.
        self.assertIn("('id', '!=', driver_id)", view['arch'])

    def test_document_list_shows_the_box_and_crew_columns(self):
        view = self.env['vuma.delivery.document'].get_view(
            view_id=self.env.ref(
                'vuma_picking_slip.view_vuma_delivery_document_list').id,
            view_type='list')

        self.assertIn('total_box_quantity', view['arch'])
        self.assertIn('total_box_weight', view['arch'])
        self.assertIn('vehicle_id', view['arch'])
        self.assertIn('driver_id', view['arch'])

    def test_document_is_listed_under_sales(self):
        menu = self.env.ref('vuma_picking_slip.menu_delivery_document')
        self.assertEqual(menu.parent_id, self.env.ref('sale.sale_menu_root'))
        self.assertEqual(menu.action,
                         self.env.ref('vuma_picking_slip.action_delivery_document'))
