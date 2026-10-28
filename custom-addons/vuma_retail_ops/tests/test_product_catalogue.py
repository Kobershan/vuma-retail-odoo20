# -*- coding: utf-8 -*-
"""Product catalogue tabs: departments, recipes, components and labels."""
from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class VumaProductCatalogueTest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.company = cls.env.company

    def _product(self, name, price=10.0, barcode=False):
        return self.env['product.template'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'uom_id': self.unit.id,
            'list_price': price,
            'standard_price': price / 2.0,
            'barcode': barcode or False,
            'company_id': self.company.id,
        })

    def test_department_on_product_and_variant(self):
        department = self.env['vuma.department'].create({
            'name': 'Paint',
            'code': 'PAINT',
            'company_id': self.company.id,
        })
        product = self._product('Gloss Enamel')
        product.vuma_department_id = department
        self.assertEqual(product.vuma_department_id, department)
        self.assertEqual(product.product_variant_id.vuma_department_id,
                         department)
        self.assertEqual(department.product_count, 1)

    def test_recipes_and_components_are_two_directions(self):
        finished = self._product('Paint Tin')
        component = self._product('Paint Base')
        bom = self.env['mrp.bom'].create({
            'product_tmpl_id': finished.id,
            'product_qty': 1.0,
            'uom_id': self.unit.id,
            'company_id': self.company.id,
            'bom_line_ids': [Command.create({
                'product_id': component.product_variant_id.id,
                'product_qty': 3.0,
                'uom_id': self.unit.id,
                'vuma_component_label': 'Base compound',
            })],
        })
        line = bom.bom_line_ids

        self.assertEqual(finished.vuma_recipe_count, 1)
        self.assertIn(line, finished.vuma_recipe_line_ids)
        self.assertEqual(line.vuma_component_label, 'Base compound')

        self.assertIn(line, component.vuma_component_ids)
        self.assertEqual(component.vuma_used_in_product_count, 1)
        self.assertEqual(component.vuma_component_ids.product_qty, 3.0)
        self.assertEqual(
            component.vuma_component_ids.parent_product_tmpl_id, finished)

    def test_label_defaults_from_product(self):
        product = self._product('Hand Soap', price=24.99, barcode='6001234')
        label = self.env['vuma.product.label'].create({
            'template_id': product.id,
        })
        self.assertEqual(label.name, 'Hand Soap')
        self.assertEqual(label.price, 24.99)
        self.assertEqual(label.barcode, '6001234')
        self.assertEqual(label.label_type, 'shelf')
        self.assertEqual(product.vuma_label_count, 1)

    def test_add_default_label_for_base_and_pack(self):
        product = self._product('Bulk Paint', price=50.0)
        self.env['vuma.product.pack.line'].create({
            'template_id': product.id,
            'name': 'Single',
            'pack_qty': 1.0,
            'pack_uom_id': self.unit.id,
            'price': 50.0,
            'barcode': '6009999',
        })
        product.action_add_default_labels()
        self.assertEqual(product.vuma_label_count, 2)
        self.assertTrue(product.vuma_label_ids.filtered(
            lambda label: not label.pack_line_id))
        self.assertTrue(product.vuma_label_ids.filtered(
            lambda label: label.pack_line_id))

    def test_printing_marks_label_printed(self):
        product = self._product('Print Me')
        label = self.env['vuma.product.label'].create({
            'template_id': product.id,
        })
        action = product.action_print_labels()
        self.assertEqual(action['type'], 'ir.actions.report')

        report = self.env['report.vuma_retail_ops.report_vuma_product_label']
        report._get_report_values(label.ids)
        self.assertEqual(label.print_count, 1)
        self.assertTrue(label.last_printed)

    def test_label_report_renders(self):
        product = self._product('Shelf Label Product', price=12.5,
                                barcode='6005555')
        label = self.env['vuma.product.label'].create({
            'template_id': product.id,
            'notes': 'While stocks last',
        })
        html, _report_type = self.env['ir.actions.report']._render_qweb_html(
            'vuma_retail_ops.action_report_vuma_product_label', label.ids)
        text = html.decode()
        self.assertIn('Shelf Label Product', text)
        self.assertIn('6005555', text)
        self.assertIn('While stocks last', text)
