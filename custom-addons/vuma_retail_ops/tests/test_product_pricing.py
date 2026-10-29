# -*- coding: utf-8 -*-
"""Spreadsheet pricing on the product profile.

Covers both halves of the feature: the figures that lean on each other
(Nett Cost, Markup %, Markup Value, Selling excl VAT, Selling incl VAT) and
the two of them that are written back into Odoo's own *Sales Price* and
*Cost* fields.
"""
from odoo import Command
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

    # ---------------------------------------------- weighted average costing

    def test_weighted_average_cost_weights_by_volume_not_simple_average(self):
        # 100 items at R1000 and 1 item at R1:
        # Total spent = 100,000 + 1 = R100,001 across 101 units -> R990.11
        # (never the unweighted average of R500.50).
        product = self._product('Bulk Item', tax=self.tax_excluded)
        product.write({
            'vuma_cost_line_ids': [
                Command.create({'quantity': 100.0, 'unit_price': 1000.0}),
                Command.create({'quantity': 1.0, 'unit_price': 1.0}),
            ],
            'vuma_markup_percent': 20.0,
        })
        self.assertTrue(product.vuma_gross_cost_driven)
        self.assertAlmostEqual(product.vuma_cost_total_qty, 101.0, places=2)
        self.assertAlmostEqual(
            product.vuma_cost_total_value, 100001.0, places=2)
        self.assertAlmostEqual(product.vuma_cost_low, 1.0, places=2)
        self.assertAlmostEqual(product.vuma_cost_high, 1000.0, places=2)
        self.assertAlmostEqual(product.vuma_weighted_cost, 990.11, places=2)
        self.assertAlmostEqual(product.vuma_gross_cost, 990.11, places=2)
        self.assertAlmostEqual(product.vuma_nett_cost, 990.11, places=2)
        self.assertAlmostEqual(product.standard_price, 990.11, places=2)
        # 20% markup on 990.11 -> selling ex VAT = 1188.132
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 1188.13, places=2)
        self.assertAlmostEqual(product.list_price, 1188.13, places=2)

    def test_adding_another_batch_updates_weighted_cost_and_keeps_pin(self):
        product = self._product('Cement Bag', tax=self.tax_included)
        product.write({
            'vuma_cost_line_ids': [
                Command.create({'quantity': 50.0, 'unit_price': 80.0}),
            ],
            'vuma_transport_cost': 10.0,
            'vuma_markup_percent': 25.0,
        })
        self.assertAlmostEqual(product.vuma_gross_cost, 80.0, places=2)
        self.assertAlmostEqual(product.vuma_nett_cost, 90.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 112.5, places=2)

        # Bring in another 50 units at R100 -> weighted average is R90.00,
        # plus R10 transport = R100 nett cost, 25% markup -> R125 ex VAT,
        # R143.75 incl VAT.
        product.write({
            'vuma_cost_line_ids': [
                Command.create({'quantity': 50.0, 'unit_price': 100.0}),
            ],
        })
        self.assertAlmostEqual(product.vuma_weighted_cost, 90.0, places=2)
        self.assertAlmostEqual(product.vuma_gross_cost, 90.0, places=2)
        self.assertAlmostEqual(product.vuma_nett_cost, 100.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 125.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 143.75, places=2)
        self.assertAlmostEqual(product.list_price, 143.75, places=2)
        self.assertAlmostEqual(product.standard_price, 100.0, places=2)

    def test_price_points_in_the_form_drive_gross_cost_and_selling_price(self):
        product = self._product('Timber Plank', tax=self.tax_included)
        with Form(product) as form:
            form.vuma_markup_percent = 50.0
            with form.vuma_cost_line_ids.new() as line1:
                line1.quantity = 80.0
                line1.unit_price = 100.0
            with form.vuma_cost_line_ids.new() as line2:
                line2.quantity = 20.0
                line2.unit_price = 150.0
            # (80*100 + 20*150) / 100 = 11,000 / 100 = 110.00
            self.assertAlmostEqual(form.vuma_weighted_cost, 110.0, places=2)
            self.assertAlmostEqual(form.vuma_gross_cost, 110.0, places=2)
            self.assertAlmostEqual(form.vuma_nett_cost, 110.0, places=2)
            self.assertAlmostEqual(form.vuma_selling_ex_vat, 165.0, places=2)
            self.assertAlmostEqual(
                form.vuma_selling_inc_vat, 189.75, places=2)
            form.save()
        self.assertAlmostEqual(product.vuma_gross_cost, 110.0, places=2)
        self.assertAlmostEqual(product.standard_price, 110.0, places=2)
        self.assertAlmostEqual(product.list_price, 189.75, places=2)

    # --------------------------------------------------------- VAT treatment

    def test_vat_treatment_marks_product_and_charges_correct_vat_on_order(self):
        self.company.account_price_include = 'tax_included'
        partner = self.env['res.partner'].create({'name': 'VAT Test Buyer'})
        standard_item = self._product(
            'Standard Item', vuma_vat_treatment='standard',
            vuma_gross_cost=100.0, vuma_selling_ex_vat=100.0)
        zero_item = self._product(
            'Zero Rated Maize', vuma_vat_treatment='zero_rated',
            vuma_gross_cost=100.0, vuma_selling_ex_vat=100.0)
        exempt_item = self._product(
            'Exempt Financial Fee', vuma_vat_treatment='exempt',
            vuma_gross_cost=100.0, vuma_selling_ex_vat=100.0)

        self.assertAlmostEqual(standard_item.vuma_vat_percent, 15.0, places=2)
        self.assertEqual(standard_item.vuma_vat_box, '[1]')
        self.assertAlmostEqual(zero_item.vuma_vat_percent, 0.0, places=2)
        self.assertEqual(zero_item.vuma_vat_box, '[2]')
        self.assertAlmostEqual(exempt_item.vuma_vat_percent, 0.0, places=2)
        self.assertEqual(exempt_item.vuma_vat_box, '[3]')
        self.assertNotEqual(zero_item.taxes_id, exempt_item.taxes_id)

        # Raising a sale order charges 15% VAT on the standard item and R0 VAT
        # on both the zero-rated and VAT-exempt items, while keeping their
        # distinct VAT201 taxes on the order lines.
        order = self.env['sale.order'].create({
            'partner_id': partner.id,
            'company_id': self.company.id,
            'order_line': [
                Command.create({
                    'product_id': standard_item.product_variant_id.id,
                    'product_uom_qty': 1.0,
                }),
                Command.create({
                    'product_id': zero_item.product_variant_id.id,
                    'product_uom_qty': 1.0,
                }),
                Command.create({
                    'product_id': exempt_item.product_variant_id.id,
                    'product_uom_qty': 1.0,
                }),
            ],
        })
        std_line, zero_line, exempt_line = order.order_line
        self.assertEqual(std_line.tax_ids, standard_item.taxes_id)
        self.assertAlmostEqual(std_line.price_tax, 15.0, places=2)
        self.assertAlmostEqual(std_line.price_subtotal, 100.0, places=2)
        self.assertAlmostEqual(std_line.price_total, 115.0, places=2)

        self.assertEqual(zero_line.tax_ids, zero_item.taxes_id)
        self.assertAlmostEqual(zero_line.price_tax, 0.0, places=2)
        self.assertAlmostEqual(zero_line.price_total, 100.0, places=2)

        self.assertEqual(exempt_line.tax_ids, exempt_item.taxes_id)
        self.assertAlmostEqual(exempt_line.price_tax, 0.0, places=2)
        self.assertAlmostEqual(exempt_line.price_total, 100.0, places=2)

    def test_switching_vat_treatment_in_form_updates_tax_and_prices(self):
        product = self._product(
            'Cooking Oil', vuma_vat_treatment='standard',
            vuma_gross_cost=100.0, vuma_markup_percent=20.0)
        self.assertAlmostEqual(product.vuma_vat_percent, 15.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 120.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 138.0, places=2)

        with Form(product) as form:
            form.vuma_vat_treatment = 'zero_rated'
            self.assertAlmostEqual(form.vuma_vat_percent, 0.0, places=2)
            self.assertAlmostEqual(form.vuma_selling_ex_vat, 120.0, places=2)
            self.assertAlmostEqual(form.vuma_selling_inc_vat, 120.0, places=2)
            form.save()
        self.assertEqual(product.vuma_vat_treatment, 'zero_rated')
        self.assertEqual(product.vuma_vat_box, '[2]')
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 120.0, places=2)
        self.assertAlmostEqual(product.list_price, 120.0, places=2)

    # ------------------------- settings costing methods & landed cost capture

    def test_settings_costing_methods_recalculate_all_goods(self):
        # 3 batches:
        #   2026-01-01: 100 units @ R1000
        #   2026-02-01:   1 unit  @ R100
        #   2026-03-01:   1 unit  @ R1
        product = self._product('Multi-Method Item', tax=self.tax_excluded)
        product.write({
            'vuma_cost_line_ids': [
                Command.create({
                    'date': '2026-01-01',
                    'quantity': 100.0,
                    'unit_price': 1000.0,
                }),
                Command.create({
                    'date': '2026-02-01',
                    'quantity': 1.0,
                    'unit_price': 100.0,
                }),
                Command.create({
                    'date': '2026-03-01',
                    'quantity': 1.0,
                    'unit_price': 1.0,
                }),
            ],
            'vuma_markup_percent': 10.0,
        })
        # 1. Default = Weighted Average: (100000 + 100 + 1) / 102 = 981.38
        self.assertAlmostEqual(product.vuma_gross_cost, 981.38, places=2)
        self.assertAlmostEqual(product.standard_price, 981.38, places=2)

        # 2. Switch Settings to Simple Average: (1000 + 100 + 1) / 3 = 367.00
        settings = self.env['res.config.settings'].create({
            'vuma_costing_method': 'simple_average',
        })
        settings.execute()
        self.assertAlmostEqual(product.vuma_gross_cost, 367.0, places=2)
        self.assertAlmostEqual(product.standard_price, 367.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 403.7, places=2)

        # 3. Switch Settings to Log Average (Geometric Mean):
        #    exp((ln(1000) + ln(100) + ln(1)) / 3) = (1000 * 100 * 1)^(1/3) = 46.42
        self.company.write({'vuma_costing_method': 'log_average'})
        self.assertAlmostEqual(product.vuma_gross_cost, 46.42, places=2)
        self.assertAlmostEqual(product.standard_price, 46.42, places=2)

        # 4. Switch Settings to FIFO (Oldest batch = 2026-01-01 @ R1000.00)
        self.company.write({'vuma_costing_method': 'fifo'})
        self.assertAlmostEqual(product.vuma_gross_cost, 1000.0, places=2)
        self.assertAlmostEqual(product.standard_price, 1000.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 1100.0, places=2)

        # 5. Switch Settings to LIFO (Latest batch = 2026-03-01 @ R1.00)
        self.company.write({'vuma_costing_method': 'lifo'})
        self.assertAlmostEqual(product.vuma_gross_cost, 1.0, places=2)
        self.assertAlmostEqual(product.standard_price, 1.0, places=2)

        # 6. Switch Settings to Median Cost (Middle of [1, 100, 1000] = R100.00)
        self.company.write({'vuma_costing_method': 'median_cost'})
        self.assertAlmostEqual(product.vuma_gross_cost, 100.0, places=2)

        # 7. Switch Settings to Highest Cost (R1000.00) and Lowest Cost (R1.00)
        self.company.write({'vuma_costing_method': 'highest_cost'})
        self.assertAlmostEqual(product.vuma_gross_cost, 1000.0, places=2)
        self.company.write({'vuma_costing_method': 'lowest_cost'})
        self.assertAlmostEqual(product.vuma_gross_cost, 1.0, places=2)

        # Restore default Weighted Average
        self.company.write({'vuma_costing_method': 'weighted_average'})
        self.assertAlmostEqual(product.vuma_gross_cost, 981.38, places=2)

    def test_all_landed_cost_components_captured_and_controlled_by_settings(self):
        product = self._product('Imported Generator', tax=self.tax_included)
        product.write({
            'vuma_gross_cost': 1000.0,
            'vuma_transport_cost': 50.0,
            'vuma_import_duty_percent': 10.0,  # -> R100.00 import duty
            'vuma_shipping_cost': 80.0,
            'vuma_clearance_cost': 30.0,
            'vuma_insurance_cost': 15.0,
            'vuma_packaging_cost': 10.0,
            'vuma_handling_cost': 5.0,
            'vuma_levy_cost': 4.0,
            'vuma_fx_bank_cost': 3.0,
            'vuma_other_landed_cost': 3.0,
            'vuma_markup_percent': 20.0,
        })
        # Import Duty Cost = 10% of 1000 = 100.0
        self.assertAlmostEqual(product.vuma_import_duty_cost, 100.0, places=2)
        # Additional landed costs = 50 + 100 + 80 + 30 + 15 + 10 + 5 + 4 + 3 + 3 = 300.0
        self.assertAlmostEqual(
            product.vuma_additional_landed_cost, 300.0, places=2)
        # Nett Cost = 1000 + 300 = 1300.0
        self.assertAlmostEqual(product.vuma_nett_cost, 1300.0, places=2)
        self.assertAlmostEqual(product.standard_price, 1300.0, places=2)
        # 20% markup on 1300 -> 1560 ex VAT, 1794 incl 15% VAT
        self.assertAlmostEqual(product.vuma_selling_ex_vat, 1560.0, places=2)
        self.assertAlmostEqual(product.vuma_selling_inc_vat, 1794.0, places=2)
        self.assertAlmostEqual(product.list_price, 1794.0, places=2)

        # Toggling Transport or Import Duty off in Settings excludes it from
        # Nett Cost while keeping the captured figures on the product intact.
        self.company.write({
            'vuma_cost_include_transport': False,
            'vuma_cost_include_import_duty': False,
        })
        self.assertAlmostEqual(product.vuma_transport_cost, 50.0, places=2)
        self.assertAlmostEqual(product.vuma_import_duty_cost, 100.0, places=2)
        self.assertAlmostEqual(
            product.vuma_additional_landed_cost, 150.0, places=2)
        self.assertAlmostEqual(product.vuma_nett_cost, 1150.0, places=2)
        self.assertAlmostEqual(product.standard_price, 1150.0, places=2)

        # Re-enabling them in Settings restores the full landed cost.
        self.company.write({
            'vuma_cost_include_transport': True,
            'vuma_cost_include_import_duty': True,
        })
        self.assertAlmostEqual(product.vuma_nett_cost, 1300.0, places=2)
        self.assertAlmostEqual(product.standard_price, 1300.0, places=2)
