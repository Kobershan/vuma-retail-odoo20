# -*- coding: utf-8 -*-
"""Supplier stock codes and multi-supplier RFQ tests."""
from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class VumaSupplierCodeTest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.supplier_a = cls.env['res.partner'].create({
            'name': 'Supplier Code A',
            'supplier_rank': 1,
            'email': 'a@supplier-code.test',
        })
        cls.supplier_b = cls.env['res.partner'].create({
            'name': 'Supplier Code B',
            'supplier_rank': 1,
            'email': 'b@supplier-code.test',
        })
        cls.product = cls.env['product.product'].create({
            'name': 'Supplier Code Widget',
            'type': 'consu',
            'is_storable': True,
            'purchase_ok': True,
            'list_price': 100.0,
            'standard_price': 40.0,
            'taxes_id': [Command.clear()],
            'supplier_taxes_id': [Command.clear()],
        })
        cls.info_a = cls.env['product.supplierinfo'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'partner_id': cls.supplier_a.id,
            'product_code': 'SUP-A-001',
            'product_name': 'Widget A description',
            'price': 10.0,
            'min_qty': 1.0,
            'delay': 3,
        })
        cls.info_b = cls.env['product.supplierinfo'].create({
            'product_tmpl_id': cls.product.product_tmpl_id.id,
            'partner_id': cls.supplier_b.id,
            'product_code': 'SUP-B-900',
            'product_name': 'Widget B description',
            'price': 12.0,
            'min_qty': 2.0,
            'delay': 5,
        })

    def _wizard_for_product(self):
        action = self.product.product_tmpl_id.action_vuma_request_supplier_quotes()
        return self.env['vuma.supplier.rfq.wizard'].browse(action['res_id'])

    def _orders_from_result(self, result):
        order_ids = result['domain'][0][2]
        return self.env['purchase.order'].browse(order_ids)

    def test_wizard_lists_each_supplier_code(self):
        wizard = self._wizard_for_product()
        self.assertEqual(len(wizard.line_ids), 2)
        self.assertEqual(
            set(wizard.line_ids.mapped('stock_code')),
            {'SUP-A-001', 'SUP-B-900'})
        self.assertEqual(
            set(wizard.line_ids.mapped('supplier_id')),
            {self.supplier_a, self.supplier_b})

    def test_creates_one_rfq_per_supplier_with_correct_code(self):
        wizard = self._wizard_for_product()
        orders = self._orders_from_result(wizard.action_create_rfqs())
        self.assertEqual(len(orders), 2)
        order_a = orders.filtered(lambda order: order.partner_id == self.supplier_a)
        order_b = orders.filtered(lambda order: order.partner_id == self.supplier_b)
        self.assertEqual(len(order_a), 1)
        self.assertEqual(len(order_b), 1)
        self.assertEqual(order_a.order_line.vuma_supplier_code, 'SUP-A-001')
        self.assertEqual(order_b.order_line.vuma_supplier_code, 'SUP-B-900')
        self.assertEqual(order_a.order_line.vuma_supplierinfo_id, self.info_a)
        self.assertEqual(order_b.order_line.vuma_supplierinfo_id, self.info_b)

    def test_two_codes_for_same_supplier_share_one_rfq(self):
        second_code = self.env['product.supplierinfo'].create({
            'product_tmpl_id': self.product.product_tmpl_id.id,
            'partner_id': self.supplier_a.id,
            'product_code': 'SUP-A-002',
            'product_name': 'Widget A second code',
            'price': 9.5,
            'min_qty': 1.0,
            'delay': 4,
        })
        wizard = self._wizard_for_product()
        orders = self._orders_from_result(wizard.action_create_rfqs())
        order_a = orders.filtered(lambda order: order.partner_id == self.supplier_a)
        self.assertEqual(len(order_a), 1)
        self.assertEqual(len(order_a.order_line), 2)
        self.assertEqual(
            set(order_a.order_line.mapped('vuma_supplier_code')),
            {'SUP-A-001', 'SUP-A-002'})
        self.assertIn(second_code, order_a.order_line.vuma_supplierinfo_id)

    def test_manual_rfq_line_gets_supplier_code(self):
        order = self.env['purchase.order'].create({
            'partner_id': self.supplier_a.id,
            'order_line': [Command.create({
                'product_id': self.product.id,
                'product_qty': 3.0,
                'price_unit': 10.0,
            })],
        })
        self.assertEqual(order.order_line.vuma_supplier_code, 'SUP-A-001')

    def test_product_without_supplier_codes_cannot_start_wizard(self):
        empty_product = self.env['product.product'].create({
            'name': 'No Supplier Code Product',
            'type': 'consu',
            'purchase_ok': True,
        })
        with self.assertRaises(UserError):
            empty_product.product_tmpl_id.action_vuma_request_supplier_quotes()
