# -*- coding: utf-8 -*-
"""Pack sizes for one catalogue item.

Each pack size must become its own stock-keeping variant while the item
remains a single product template.  The tests cover the shared pack-size
library, arbitrary pack names and units, the quantity override per item,
separate stock per pack and the conversion of an existing stocked product.
"""
from odoo import Command
from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class VumaPackSizeTest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        if not cls.warehouse:
            cls.warehouse = cls.env['stock.warehouse'].create({
                'name': 'Pack Size Test Warehouse',
                'code': 'PSTW',
                'company_id': cls.company.id,
            })
        cls.stock_location = cls.warehouse.lot_stock_id
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.ml = cls.env.ref('uom.product_uom_milliliter')
        cls.litre = cls.env.ref('uom.product_uom_litre')
        cls.single = cls.env.ref('vuma_retail_ops.pack_size_single')
        cls.pack_6 = cls.env.ref('vuma_retail_ops.pack_size_pack_6')
        cls.pack_12 = cls.env.ref('vuma_retail_ops.pack_size_pack_12')
        cls.pack_24 = cls.env.ref('vuma_retail_ops.pack_size_pack_24')
        cls.pallet = cls.env.ref('vuma_retail_ops.pack_size_pallet')

    def _create_item(self, name='Test Cola', list_price=10.0, uom=None):
        return self.env['product.template'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'uom_id': (uom or self.unit).id,
            'list_price': list_price,
            'standard_price': list_price / 2.0,
            'taxes_id': [Command.clear()],
            'supplier_taxes_id': [Command.clear()],
        })

    def _pack_line(self, name, qty, uom=None, price=0.0, barcode=False):
        return Command.create({
            'name': name,
            'pack_qty': qty,
            'pack_uom_id': (uom or self.unit).id,
            'price': price,
            'barcode': barcode or False,
        })

    def test_shared_pack_sizes_exist(self):
        packs = self.env['vuma.pack.size'].search(
            [('code', 'in', ['single', 'pack_6', 'pack_12', 'pack_24',
                             'pallet', '50ml', '500ml', '1l', '25l'])])
        self.assertEqual(len(packs), 9)
        self.assertEqual(self.pack_6.units, 6.0)
        self.assertEqual(self.pack_6.default_uom_id, self.unit)
        pack_50ml = self.env.ref('vuma_retail_ops.pack_size_50ml')
        self.assertEqual(pack_50ml.units, 50.0)
        self.assertEqual(pack_50ml.default_uom_id, self.ml)
        pack_1l = self.env.ref('vuma_retail_ops.pack_size_1l')
        self.assertEqual(pack_1l.units, 1.0)
        self.assertEqual(pack_1l.default_uom_id, self.litre)
        for pack in packs:
            self.assertTrue(pack.attribute_value_id)
            self.assertEqual(pack.attribute_value_id.attribute_id,
                             self.env.ref(
                                 'vuma_retail_ops.product_attribute_pack_size'))

    def test_item_gets_one_variant_per_pack_size(self):
        item = self._create_item()
        item.write({'vuma_pack_line_ids': [
            self._pack_line('Single', 1.0, price=10.0, barcode='SINGLE-1'),
            self._pack_line('6 Pack', 6.0, price=55.0, barcode='PACK6-1'),
            self._pack_line('12 Pack', 12.0, price=105.0, barcode='PACK12-1'),
            self._pack_line('24 Pack', 24.0, price=200.0, barcode='PACK24-1'),
            self._pack_line('Pallet', 480.0, price=4000.0,
                            barcode='PALLET-1'),
        ]})

        variants = item.product_variant_ids
        self.assertEqual(len(variants), 5)
        self.assertEqual(item.vuma_pack_count, 5)
        self.assertTrue(item.vuma_has_pack_sizes)
        self.assertIn('6 Pack', item.vuma_pack_summary)

        by_name = {product.vuma_pack_size_id.name: product
                   for product in variants}
        self.assertEqual(set(by_name), {
            'Single', '6 Pack', '12 Pack', '24 Pack', 'Pallet'})
        self.assertEqual(by_name['6 Pack'].vuma_units_per_pack, 6.0)
        self.assertEqual(by_name['Pallet'].vuma_units_per_pack, 480.0)
        self.assertEqual(by_name['6 Pack'].barcode, 'PACK6-1')
        self.assertEqual(by_name['6 Pack'].lst_price, 55.0)
        self.assertEqual(by_name['Pallet'].lst_price, 4000.0)

    def test_each_pack_size_has_its_own_stock(self):
        item = self._create_item()
        item.write({'vuma_pack_line_ids': [
            self._pack_line('Single', 1.0),
            self._pack_line('6 Pack', 6.0),
        ]})
        single = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == 'Single')
        six_pack = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == '6 Pack')

        self.env['stock.quant']._update_available_quantity(
            single, self.stock_location, 10.0)
        self.env['stock.quant']._update_available_quantity(
            six_pack, self.stock_location, 5.0)

        single.invalidate_recordset(['qty_available'])
        six_pack.invalidate_recordset(['qty_available'])
        self.assertEqual(single.qty_available, 10.0)
        self.assertEqual(six_pack.qty_available, 5.0)
        self.assertEqual(single.vuma_single_equivalent_qty, 10.0)
        self.assertEqual(six_pack.vuma_single_equivalent_qty, 30.0)

        line_six = item.vuma_pack_line_ids.filtered(
            lambda line: line.name == '6 Pack')
        self.assertEqual(line_six.qty_available, 5.0)
        self.assertEqual(line_six.single_equivalent_qty, 30.0)

    def test_existing_stock_stays_on_single_when_converted(self):
        product = self.env['product.product'].create({
            'name': 'Existing Stocked Item',
            'type': 'consu',
            'is_storable': True,
            'list_price': 20.0,
            'standard_price': 10.0,
            'taxes_id': [Command.clear()],
            'supplier_taxes_id': [Command.clear()],
        })
        self.env['stock.quant']._update_available_quantity(
            product, self.stock_location, 7.0)
        template = product.product_tmpl_id

        template.write({'vuma_pack_line_ids': [
            self._pack_line('Single', 1.0),
            self._pack_line('6 Pack', 6.0),
        ]})

        single = template.product_variant_ids.filtered(
            lambda variant: variant.vuma_pack_size_id.name == 'Single')
        self.assertEqual(single, product)
        product.invalidate_recordset(['qty_available'])
        self.assertEqual(product.qty_available, 7.0)
        self.assertEqual(len(template.product_variant_ids), 2)

    def test_pack_quantity_is_per_item(self):
        first = self._create_item('First Item')
        first.write({'vuma_pack_line_ids': [
            self._pack_line('Pallet', 480.0),
        ]})
        second = self._create_item('Second Item')
        second.write({'vuma_pack_line_ids': [
            self._pack_line('Pallet', 600.0),
        ]})

        first_pallet = first.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == 'Pallet')
        second_pallet = second.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == 'Pallet')
        self.assertEqual(first_pallet.vuma_units_per_pack, 480.0)
        self.assertEqual(second_pallet.vuma_units_per_pack, 600.0)

    def test_standard_pack_sizes_button(self):
        item = self._create_item('Standard Sizes Item', list_price=12.0)
        item.action_add_standard_pack_sizes()
        self.assertEqual(item.vuma_pack_count, 5)
        self.assertEqual(len(item.product_variant_ids), 5)

        pallet_line = item.vuma_pack_line_ids.filtered(
            lambda line: line.name == 'Pallet')
        pallet = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == 'Pallet')
        self.assertEqual(pallet_line.pack_qty, 0.0)
        self.assertEqual(pallet.vuma_units_per_pack, 0.0)

        pallet_line.write({
            'pack_qty': 480.0,
            'pack_uom_id': self.unit.id,
            'price': 4000.0,
        })
        self.assertEqual(pallet_line.units_per_pack, 480.0)
        self.assertEqual(pallet.vuma_units_per_pack, 480.0)
        self.assertEqual(pallet.lst_price, 4000.0)

    def test_clearing_pack_price_falls_back_to_item_price(self):
        item = self._create_item('Price Fallback Item', list_price=10.0)
        item.write({'vuma_pack_line_ids': [
            self._pack_line('6 Pack', 6.0, price=55.0),
        ]})
        variant = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == '6 Pack')
        self.assertEqual(variant.lst_price, 55.0)

        item.vuma_pack_line_ids.write({'price': 0.0})
        self.assertEqual(variant.lst_price, 10.0)

    def test_arbitrary_volume_packs(self):
        item = self._create_item('Bulk Paint 25L', list_price=25.0,
                                 uom=self.litre)
        item.write({'vuma_pack_line_ids': [
            self._pack_line('50ml', 50.0, self.ml, price=5.0),
            self._pack_line('500ml', 500.0, self.ml, price=15.0),
            self._pack_line('1L', 1.0, self.litre, price=25.0),
            self._pack_line('25L', 25.0, self.litre, price=400.0),
            self._pack_line('Pallet', 600.0, self.litre, price=8000.0),
        ]})

        lines = {line.name: line for line in item.vuma_pack_line_ids}
        self.assertEqual(lines['50ml'].units_per_pack, 0.05)
        self.assertEqual(lines['500ml'].units_per_pack, 0.5)
        self.assertEqual(lines['1L'].units_per_pack, 1.0)
        self.assertEqual(lines['25L'].units_per_pack, 25.0)
        self.assertEqual(lines['Pallet'].units_per_pack, 600.0)
        self.assertIn('50ml (50 ml)', item.vuma_pack_summary)

        self.assertEqual(len(item.product_variant_ids), 5)
        by_name = {product.vuma_pack_size_id.name: product
                   for product in item.product_variant_ids}
        self.assertEqual(by_name['50ml'].vuma_units_per_pack, 0.05)
        self.assertEqual(by_name['500ml'].vuma_units_per_pack, 0.5)
        self.assertEqual(by_name['Pallet'].vuma_units_per_pack, 600.0)

    def test_arbitrary_name_auto_creates_library_entry(self):
        item = self._create_item('Odd Pack Item')
        item.write({'vuma_pack_line_ids': [
            self._pack_line('Box of 37', 37.0),
        ]})
        pack = self.env['vuma.pack.size'].search(
            [('name', '=', 'Box of 37')], limit=1)
        self.assertTrue(pack)
        self.assertEqual(pack.units, 37.0)
        self.assertEqual(pack.default_uom_id, self.unit)
        variant = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == 'Box of 37')
        self.assertEqual(len(variant), 1)
        self.assertEqual(variant.vuma_units_per_pack, 37.0)

    def test_incompatible_pack_unit_still_saves_as_variant(self):
        item = self._create_item('Loose Item')
        item.write({'vuma_pack_line_ids': [
            self._pack_line('50ml', 50.0, self.ml),
        ]})
        line = item.vuma_pack_line_ids
        self.assertEqual(line.units_per_pack, 0.0)
        variant = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == '50ml')
        self.assertEqual(len(variant), 1)
        self.assertEqual(variant.vuma_units_per_pack, 0.0)

    def test_pack_name_is_unique_per_item(self):
        item = self._create_item('Duplicate Pack Item')
        with self.assertRaises(ValidationError):
            item.write({'vuma_pack_line_ids': [
                self._pack_line('6 Pack', 6.0),
                self._pack_line('6 Pack', 6.0),
            ]})

    def test_new_shared_pack_size_creates_its_attribute_value(self):
        pack = self.env['vuma.pack.size'].create({
            'name': '48 Pack',
            'code': 'pack_48',
            'units': 48.0,
            'default_uom_id': self.unit.id,
            'sequence': 45,
        })
        self.assertTrue(pack.attribute_value_id)
        self.assertEqual(pack.attribute_value_id.name, '48 Pack')

        item = self._create_item('48 Pack Item')
        item.write({'vuma_pack_line_ids': [
            self._pack_line('48 Pack', 48.0),
        ]})
        variant = item.product_variant_ids.filtered(
            lambda product: product.vuma_pack_size_id.name == '48 Pack')
        self.assertEqual(len(variant), 1)
        self.assertEqual(variant.vuma_units_per_pack, 48.0)

    def test_removing_a_pack_size_removes_its_variant(self):
        item = self._create_item()
        single_line = item.vuma_pack_line_ids.create({
            'template_id': item.id,
            'name': 'Single',
            'pack_qty': 1.0,
            'pack_uom_id': self.unit.id,
        })
        six_line = item.vuma_pack_line_ids.create({
            'template_id': item.id,
            'name': '6 Pack',
            'pack_qty': 6.0,
            'pack_uom_id': self.unit.id,
        })
        self.assertEqual(len(item.product_variant_ids), 2)

        six_line.unlink()
        remaining = item.product_variant_ids
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining.vuma_pack_size_id.name, 'Single')
        self.assertTrue(single_line.exists())
