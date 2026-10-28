# -*- coding: utf-8 -*-
"""Customer 360 tests: email control, groups, discount and history."""
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, fields
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class VumaCustomer360Test(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.product = cls.env['product.product'].create({
            'name': 'Customer 360 Test Product',
            'type': 'service',
            'list_price': 100.0,
            'taxes_id': [Command.clear()],
        })
        cls.customer = cls.env['res.partner'].create({
            'name': 'Customer 360 Test Customer',
            'customer_rank': 1,
            'email': 'accounts@customer360.test',
            'phone': '0110000000',
            'street': '1 Test Street',
            'city': 'Johannesburg',
        })

    def test_customer_page_is_enabled_for_customers(self):
        self.assertTrue(self.customer.vuma_is_customer)
        vendor = self.env['res.partner'].create({
            'name': 'Customer 360 Vendor',
            'supplier_rank': 1,
        })
        self.assertFalse(vendor.vuma_is_customer)

    def test_customer_email_is_mandatory(self):
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({
                'name': 'Customer Without Email',
                'customer_rank': 1,
                'email': False,
            })

    def test_contact_email_is_mandatory(self):
        self.env['res.partner'].create({
            'name': 'Buyer',
            'parent_id': self.customer.id,
            'type': 'contact',
            'email': 'buyer@customer360.test',
        })
        with self.assertRaises(ValidationError):
            self.env['res.partner'].create({
                'name': 'Buyer Without Email',
                'parent_id': self.customer.id,
                'type': 'contact',
                'email': False,
            })

    def test_contact_and_delivery_lists_are_filtered(self):
        contact = self.env['res.partner'].create({
            'name': 'Buyer',
            'parent_id': self.customer.id,
            'type': 'contact',
            'email': 'buyer@customer360.test',
        })
        delivery = self.env['res.partner'].create({
            'name': 'Main Store',
            'parent_id': self.customer.id,
            'type': 'delivery',
            'street': '2 Delivery Road',
            'city': 'Johannesburg',
        })
        self.assertEqual(self.customer.vuma_contact_ids, contact)
        self.assertEqual(self.customer.vuma_delivery_address_ids, delivery)
        self.assertEqual(
            self.customer.vuma_delivery_address_ids.mapped('street'),
            ['2 Delivery Road'])

    def test_building_and_unit_are_part_of_the_address(self):
        self.customer.write({
            'vuma_building_name': 'Eastgate Business Park',
            'vuma_unit_number': 'Unit 12',
        })
        self.assertIn(
            'Eastgate Business Park, Unit 12, 1 Test Street',
            self.customer.contact_address)

    def test_debtor_and_supplier_searches_are_separated(self):
        supplier = self.env["res.partner"].create({
            "name": "Only Supplier",
            "supplier_rank": 1,
        })
        customer_partner = self.env["res.partner"].with_context(
            res_partner_search_mode="customer")
        supplier_partner = self.env["res.partner"].with_context(
            res_partner_search_mode="supplier")

        self.assertNotIn(
            supplier.id, [pid for pid, _name in customer_partner.name_search("Only Supplier")])
        self.assertIn(
            supplier.id, [pid for pid, _name in supplier_partner.name_search("Only Supplier")])
        self.assertIn(
            self.customer.id,
            [pid for pid, _name in customer_partner.name_search(
                "Customer 360 Test Customer")])
        self.assertNotIn(
            self.customer.id,
            [pid for pid, _name in supplier_partner.name_search(
                "Customer 360 Test Customer")])

    def test_core_partner_fields_carry_the_right_domain(self):
        for xmlid, snippet in (
            ("vuma_retail_ops.view_sale_order_form_vuma_partner_domain", "customer_rank"),
            ("vuma_retail_ops.view_purchase_order_form_vuma_partner_domain", "supplier_rank"),
            ("vuma_retail_ops.view_move_form_vuma_partner_domain", "customer_rank"),
            ("vuma_retail_ops.view_account_payment_form_vuma_partner_domain", "supplier_rank"),
            ("vuma_retail_ops.view_picking_form_vuma_partner_domain", "supplier_rank"),
        ):
            with self.subTest(view=xmlid):
                self.assertIn(snippet, self.env.ref(xmlid).arch_db)

    def test_separate_customer_supplier_and_employee_lists(self):
        contacts_root = self.env.ref("contacts.menu_contacts")
        customers = self.env.ref("vuma_retail_ops.menu_vuma_contacts_customers")
        suppliers = self.env.ref("vuma_retail_ops.menu_vuma_contacts_suppliers")
        employees = self.env.ref("vuma_retail_ops.menu_vuma_contacts_employees")
        self.assertEqual(customers.parent_id, contacts_root)
        self.assertEqual(suppliers.parent_id, contacts_root)
        self.assertEqual(employees.parent_id, contacts_root)
        self.assertIn("customer_rank", customers.action.domain)
        self.assertIn("supplier_rank", suppliers.action.domain)
        self.assertIn("employee", employees.action.domain)

    def test_delivery_address_is_captured_inline(self):
        self.customer.write({
            'vuma_delivery_building_name': 'Graniteside Business Park',
            'vuma_delivery_unit_number': 'Unit 5',
            'vuma_delivery_street': '2 Delivery Road',
            'vuma_delivery_street2': 'Graniteside',
            'vuma_delivery_city': 'Harare',
            'vuma_delivery_zip': '00263',
        })
        address = self.customer.vuma_delivery_address_ids
        self.assertEqual(len(address), 1)
        self.assertEqual(address.type, 'delivery')
        self.assertEqual(address.street, '2 Delivery Road')
        self.assertEqual(address.vuma_building_name, 'Graniteside Business Park')
        self.assertEqual(address.vuma_unit_number, 'Unit 5')
        self.assertEqual(address.city, 'Harare')
        self.assertIn(
            'Graniteside Business Park, Unit 5, 2 Delivery Road',
            address.contact_address)
        self.assertEqual(self.customer.vuma_delivery_street, '2 Delivery Road')
        self.assertEqual(self.customer.vuma_delivery_city, 'Harare')

        # Editing the child address is reflected back on the customer.
        address.write({'street': '9 Warehouse Way'})
        self.assertEqual(self.customer.vuma_delivery_street, '9 Warehouse Way')

        # The inline fields keep using the first (primary) address.
        self.env['res.partner'].create({
            'name': 'Second Store',
            'parent_id': self.customer.id,
            'type': 'delivery',
            'street': '10 Other Road',
        })
        self.assertEqual(self.customer.vuma_delivery_street, '9 Warehouse Way')

    def test_customer_groups_and_discount(self):
        group = self.env['vuma.customer.group'].create({
            'name': 'Wholesale',
            'code': 'WHOLESALE',
            'discount_percent': 7.5,
        })
        self.customer.write({
            'vuma_customer_group_ids': [Command.link(group.id)],
            'vuma_discount_percent': 7.5,
        })
        self.assertEqual(self.customer.vuma_customer_group_ids, group)
        self.assertEqual(group.partner_count, 1)
        with self.assertRaises(ValidationError):
            self.customer.vuma_discount_percent = 101.0

    def test_customer_discount_reaches_order_lines(self):
        self.customer.vuma_discount_percent = 10.0
        order = self.env['sale.order'].create({
            'partner_id': self.customer.id,
        })
        line = self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': self.product.id,
            'product_uom_qty': 1.0,
            'price_unit': 100.0,
        })
        self.assertEqual(line.discount, 10.0)

    def test_history_shows_invoice_and_running_balance(self):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_line_ids': [Command.create({
                'product_id': self.product.id,
                'name': self.product.name,
                'quantity': 1.0,
                'price_unit': 100.0,
                'tax_ids': [Command.clear()],
            })],
        })
        invoice.action_post()
        rows = self.env['vuma.customer.history'].search([
            ('partner_id', '=', self.customer.id),
            ('source', '=', 'move'),
            ('source_id', '=', invoice.id),
        ])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows.document_type, 'out_invoice')
        self.assertAlmostEqual(rows.total, 100.0, places=2)
        self.assertAlmostEqual(rows.balance, 100.0, places=2)
        self.assertTrue(rows.action_open_document()['res_id'])

    def test_payment_terms_are_shared_across_companies(self):
        second = self.env['res.company'].create({
            'name': 'Customer 360 Second Company',
            'currency_id': self.env.company.currency_id.id,
        })
        term = self.env.ref('account.account_payment_term_30days')
        customer = self.env['res.partner'].create({
            'name': 'Terms Customer',
            'customer_rank': 1,
            'email': 'terms@customer360.test',
            'property_payment_term_id': term.id,
        })
        self.assertEqual(
            customer.with_company(second).property_payment_term_id, term)

    def test_statement_schedule_defaults_to_next_month(self):
        today = fields.Date.context_today(self.customer)
        expected = today.replace(day=1) + relativedelta(months=1)
        self.assertTrue(self.customer.vuma_statement_auto)
        self.assertEqual(self.customer.vuma_statement_next_date, expected)

    def test_statement_next_date_clamps_short_months(self):
        january_end = date(2026, 1, 31)
        self.assertEqual(
            self.customer._vuma_next_statement_date(january_end),
            date(2026, 2, 28))

    def test_statement_period_is_previous_calendar_month(self):
        date_from, date_to = self.customer._vuma_statement_period(
            date(2026, 10, 23))
        self.assertEqual(date_from, date(2026, 9, 1))
        self.assertEqual(date_to, date(2026, 9, 30))

    def test_due_statement_with_no_email_is_advanced(self):
        customer = self.env['res.partner'].create({
            'name': 'No Email Statement Customer',
            'customer_rank': 1,
            'vuma_statement_next_date': fields.Date.context_today(self)
            - timedelta(days=1),
        })
        self.assertFalse(customer.email)
        self.customer._cron_send_due_statements()
        self.assertGreater(
            customer.vuma_statement_next_date,
            fields.Date.context_today(self) - timedelta(days=1))
        self.assertFalse(customer.vuma_statement_last_sent)

    def test_customer_rating_can_be_taken_from_calculated_rating(self):
        self.env['vuma.debtor.rating'].create({
            'partner_id': self.customer.id,
            'company_id': self.env.company.id,
            'date': fields.Date.context_today(self),
            'state': 'confirmed',
            'rating': 'a',
        })
        self.assertEqual(self.customer.vuma_debtor_rating, 'a')
        self.customer.action_vuma_use_calculated_rating()
        self.assertEqual(self.customer.vuma_account_rating, 'a')
