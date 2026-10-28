# -*- coding: utf-8 -*-
"""Spreadsheet pricing on the product profile.

Covers both halves of the feature: the figures that lean on each other
(Nett Cost, Markup %, Markup Value, Selling excl VAT, Selling incl VAT) and
the two of them that are written back into Odoo's own *Sales Price* and
*Cost* fields.
"""
from odoo.tests import Form, TransactionCase, tagged


@tagged('post_install', '-at_install')
class VumaProductPricingTest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.unit = cls.env.ref('uom.product_uom_unit')
        cls.company = cls.env.company
        # The fallback used when a product carries no tax at all, pinned so
        # the expectations below do not depend on how the demo data left it.
        cls.company.account_price_include = 'tax_excluded'
        cls.tax_excluded = cls._tax('Vuma Test VAT Excl', 'tax_excluded')
        cls.tax_included = cls._tax('Vuma Test VAT Incl', 'tax_included')

    @classmethod
    def _tax(cls, name, price_include_override):
        return cls.env['account.tax'].create({
            'name': name,
            'amount': 15.0,
            'amount_type': 'percent',
            'type_tax_use': 'sale',
            'company_id': cls.company.id,
            'price_include_override': price_include_override,
        })

    def _product(self, name, tax=None, **vals):
        values = {
            'name': name,
            'type': 'consu',
            'uom_id': self.unit.id,
            'company_id': self.company.id,
        }
        if tax is not None:
            values['taxes_id'] = [(6, 0, tax.ids)]
        values.update(vals)
        return self.env['product.template'].create(values)

    # ------------------------------------------------------------ the costs

    def test_nett_cost_is_gross_plus_transport(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_transport_cost': 7.5,
        })
        self.assertAlmostEqual(product.vuma_nett_cost, 107.5, places=2)
        self.assertAlmostEqual(product.vuma_gross_cost, 100.0, places=2)
        self.assertAlmostEqual(product.vuma_transport_cost, 7.5, places=2)

    def test_transport_inside_the_cost_stays_zero(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({'vuma_gross_cost': 64.0})
        self.assertAlmostEqual(product.vuma_nett_cost, 64.0, places=2)

    def test_vat_percent_comes_from_the_taxes(self):
        product = self._product('Box', tax=self.tax_included)
        self.assertAlmostEqual(product.vuma_vat_percent, 15.0, places=2)
        product.write({'taxes_id': [(5, 0, 0)]})
        self.assertAlmostEqual(product.vuma_vat_percent, 0.0, places=2)

    # ------------------------------------------------- each of the four pins

    def test_markup_percent_fills_the_rest(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        self.assertEqual(product.vuma_pricing_driver, 'vuma_markup_percent')
        self.assertAlmostEqual(product.vuma_markup_value, 30.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 130.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 149.5, places=2)

    def test_markup_value_fills_the_rest(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_value': 25.0,
        })
        self.assertEqual(product.vuma_pricing_driver, 'vuma_markup_value')
        self.assertAlmostEqual(product.vuma_markup_percent, 25.0, places=4)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 125.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 143.75, places=2)

    def test_selling_excl_vat_fills_the_rest(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_selling_ex_vat': 150.0,
        })
        self.assertEqual(product.vuma_pricing_driver, 'vuma_selling_ex_vat')
        self.assertAlmostEqual(product.vuma_markup_value, 50.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_percent, 50.0, places=4)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 172.5, places=2)

    def test_selling_incl_vat_fills_the_rest(self):
        product = self._product('Box', tax=self.tax_included)
        product.write({
            'vuma_gross_cost': 80.0,
            'vuma_selling_inc_vat': 115.0,
        })
        self.assertEqual(product.vuma_pricing_driver, 'vuma_selling_inc_vat')
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 100.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_value, 20.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_percent, 25.0, places=4)

    def test_the_pin_survives_a_cost_change(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        product.write({'vuma_gross_cost': 200.0})
        self.assertAlmostEqual(product.vuma_markup_percent, 30.0, places=4)
        self.assertAlmostEqual(product.vuma_markup_value, 60.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 260.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 299.0, places=2)

    def test_a_pinned_price_survives_a_cost_change(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 80.0,
            'vuma_selling_ex_vat': 150.0,
        })
        product.write({'vuma_gross_cost': 120.0})
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 150.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_value, 30.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_percent, 25.0, places=4)

    # ------------------------------------------------------ Odoo's own price

    def test_sales_price_holds_the_excl_vat_figure_for_excl_taxes(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        self.assertAlmostEqual(product.list_price, 130.0, places=2)
        self.assertAlmostEqual(product.standard_price, 100.0, places=2)

    def test_sales_price_holds_the_incl_vat_figure_for_incl_taxes(self):
        product = self._product('Box', tax=self.tax_included)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        self.assertAlmostEqual(product.list_price, 149.5, places=2)
        self.assertAlmostEqual(product.standard_price, 100.0, places=2)

    def test_switching_the_tax_moves_the_sales_price(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        self.assertAlmostEqual(product.list_price, 130.0, places=2)

        product.write({'taxes_id': [(6, 0, self.tax_included.ids)]})
        self.assertAlmostEqual(product.vuma_vat_percent, 15.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 149.5, places=2)
        self.assertAlmostEqual(product.list_price, 149.5, places=2)

    def test_a_price_arriving_with_a_cost_is_the_pin(self):
        # An import sends the cost and the shelf price together: the price
        # must not be overwritten by the cost worked out from markup zero.
        product = self._product(
            'Box', tax=self.tax_excluded,
            vuma_gross_cost=100.0, list_price=250.0)
        self.assertAlmostEqual(product.list_price, 250.0, places=2)
        self.assertEqual(product.vuma_pricing_driver, 'vuma_selling_ex_vat')
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 250.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_value, 150.0, places=2)
        self.assertAlmostEqual(product.standard_price, 100.0, places=2)

    def test_editing_the_sales_price_drags_the_tab_along(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        product.write({'list_price': 200.0})
        self.assertEqual(product.vuma_pricing_driver, 'vuma_selling_ex_vat')
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 200.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_value, 100.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_percent, 100.0, places=4)

    def test_a_price_on_a_costless_item_grows_no_pricing_tab(self):
        product = self._product('Box', tax=self.tax_excluded, list_price=99.0)
        self.assertAlmostEqual(product.list_price, 99.0, places=2)
        self.assertAlmostEqual(product.vuma_markup_percent, 0.0, places=4)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 0.0, places=2)

    # ----------------------------------------------------------- the form

    def test_typing_a_price_fills_the_form(self):
        product = self._product('Box', tax=self.tax_included)
        product.write({
            'vuma_gross_cost': 80.0,
            'vuma_markup_percent': 30.0,
        })
        with Form(product) as form:
            form.vuma_selling_inc_vat = 115.0
            self.assertEqual(
                form.vuma_pricing_driver, 'vuma_selling_inc_vat')
            self.assertAlmostEqual(form.vuma_selling_ex_vat, 100.0, places=2)
            self.assertAlmostEqual(form.vuma_markup_value, 20.0, places=2)
            self.assertAlmostEqual(form.vuma_markup_percent, 25.0, places=4)

    def test_typing_a_markup_fills_the_form(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({'vuma_gross_cost': 100.0})
        with Form(product) as form:
            form.vuma_markup_percent = 40.0
            self.assertEqual(form.vuma_pricing_driver, 'vuma_markup_percent')
            self.assertAlmostEqual(form.vuma_markup_value, 40.0, places=2)
            self.assertAlmostEqual(form.vuma_selling_ex_vat, 140.0, places=2)
            self.assertAlmostEqual(form.vuma_selling_inc_vat, 161.0, places=2)

    def test_changing_the_cost_in_the_form_keeps_the_pin(self):
        product = self._product('Box', tax=self.tax_excluded)
        product.write({
            'vuma_gross_cost': 100.0,
            'vuma_markup_percent': 30.0,
        })
        with Form(product) as form:
            form.vuma_gross_cost = 200.0
            self.assertAlmostEqual(form.vuma_nett_cost, 200.0, places=2)
            self.assertAlmostEqual(form.vuma_markup_percent, 30.0, places=4)
            self.assertAlmostEqual(form.vuma_markup_value, 60.0, places=2)
            self.assertAlmostEqual(form.vuma_selling_ex_vat, 260.0, places=2)
            self.assertAlmostEqual(
                form.vuma_selling_inc_vat, 299.0, places=2)

    def test_saving_the_form_writes_the_prices_back(self):
        product = self._product('Box', tax=self.tax_included)
        with Form(product) as form:
            form.vuma_gross_cost = 80.0
            form.vuma_selling_inc_vat = 115.0
            form.save()
        product.invalidate_recordset()
        self.assertAlmostEqual(product.vuma_nett_cost, 80.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 100.0, places=2)
        self.assertAlmostEqual(product.list_price, 115.0, places=2)
        self.assertAlmostEqual(product.standard_price, 80.0, places=2)
