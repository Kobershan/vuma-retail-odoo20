# -*- coding: utf-8 -*-
"""The store front lists what is in inventory, at the selling price.

These are end-to-end checks of the policy over the *public* user: a product
must be findable through the shop's own domain, with the public user's read
access, so a mistake in either half fails here rather than as a 500 in front
of a customer.

The stock movements are the same ones the warehouse produces: writing
``qty_available`` runs Odoo's inventory adjustment, which posts a real stock
move, so the publication switch is tested through the same invalidation chain
a receipt or a delivery would use.
"""
from odoo.exceptions import UserError
from odoo.tests import tagged

from odoo.addons.vuma_storefront.controllers.cart import Cart as StoreFrontCart
from odoo.addons.website_sale.tests.common import WebsiteSaleCommon

MODULE = 'vuma_storefront'


@tagged('post_install', '-at_install', MODULE)
class TestStoreFrontInventory(WebsiteSaleCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.public_user = cls.website.user_id
        cls.public_templates = cls.env['product.template'].with_user(cls.public_user)
        cls.storefront_cart = StoreFrontCart()

    def _public_shop_templates(self):
        """What an anonymous visitor's shop search returns.

        The domain is built as the public user (that is how the shop
        controller calls it), and the search runs as the public user, so
        both the domain and the access rules are exercised.
        """
        domain = self.website.with_user(self.public_user).sale_product_domain()
        return self.public_templates.search(domain)

    def test_in_stock_product_is_listed(self):
        """A sellable product that receives stock is published by the move."""
        product = self._create_product(
            name='Storefront Widget',
            is_storable=True,
            allow_out_of_stock_order=False,
            list_price=42.0,
            qty_available=4,
            is_published=False,
        )
        self.env.invalidate_all()
        template = product.product_tmpl_id
        self.assertTrue(template.is_published)
        self.assertIn(template, self._public_shop_templates())

    def test_out_of_stock_product_is_hidden_until_restocked(self):
        """Draining the stock removes it from the shop; a receipt brings it back."""
        product = self._create_product(
            name='Storefront Cement',
            is_storable=True,
            allow_out_of_stock_order=False,
            qty_available=5,
        )
        template = product.product_tmpl_id
        self.env.invalidate_all()
        self.assertTrue(template.is_published)

        product.qty_available = 0
        self.env.invalidate_all()
        self.assertFalse(template.is_published)
        self.assertNotIn(template, self._public_shop_templates())

        product.qty_available = 2
        self.env.invalidate_all()
        self.assertTrue(template.is_published)
        self.assertIn(template, self._public_shop_templates())

    def test_sister_company_catalogue_is_listed_and_readable(self):
        """A product of another company shows up for the visitor too.

        The company is created *after* the module was installed, so this
        exercises the refresh the module data performs on update, and the
        read access the public user needs for a company outside the
        website's own. Availability travels the same path: the visitor's
        session only holds the website company, yet the sister's own stock
        must still count.
        """
        sister = self.env['res.company'].sudo().create({'name': 'Storefront Sister DC'})
        product = self.env['product.product'].sudo().with_context(
            allowed_company_ids=[sister.id],
        ).create({
            'name': 'Storefront Sister Blocks',
            'type': 'consu',
            'is_storable': True,
            'list_price': 99.0,
            'company_id': sister.id,
            'sale_ok': True,
            'allow_out_of_stock_order': False,
            'qty_available': 6,
            'is_published': True,
        })
        template = product.product_tmpl_id

        # Same call as data/storefront_data.xml makes on install and update.
        self.env['website']._vuma_storefront_open_product_rule()

        self.assertIn(template, self._public_shop_templates())

        public_variant = product.product_variant_id.with_user(self.public_user)
        website = self.website.with_user(self.public_user)
        self.assertEqual(website._get_product_available_qty(public_variant), 6.0)
        self.assertFalse(
            public_variant
            .with_context(website_id=self.website.id)
            ._is_sold_out(),
        )

    def test_adoption_under_another_company_still_sees_the_shelf(self):
        """Stock decides publication, whichever company made the change.

        Quantities are computed with the companies active in the session
        that triggers the recomputation. Adopting a stocked sister product
        from a session that only holds the website's company must still see
        the sister's shelves; otherwise the automation would hide an item
        that is sitting in its warehouse.
        """
        sister = self.env['res.company'].sudo().create({'name': 'Storefront Sister Depot'})
        product = self.env['product.product'].sudo().with_context(
            allowed_company_ids=[sister.id],
        ).create({
            'name': 'Storefront Sister Steel',
            'type': 'consu',
            'is_storable': True,
            'list_price': 285.0,
            'company_id': sister.id,
            'sale_ok': True,
            'allow_out_of_stock_order': True,
            'qty_available': 299,
            'is_published': False,
        })
        template = product.product_tmpl_id

        narrow = template.sudo().with_context(
            allowed_company_ids=[self.env.company.id],
        )
        narrow.write({'allow_out_of_stock_order': False})
        self.env.invalidate_all()
        self.assertTrue(template.is_published)

    def test_sell_when_out_of_stock_is_left_alone(self):
        """The escape hatch: a flagged product stays visible while empty."""
        product = self._create_product(
            name='Storefront Preorder',
            is_storable=True,
            allow_out_of_stock_order=True,
            qty_available=2,
            is_published=True,
        )
        template = product.product_tmpl_id
        self.env.invalidate_all()
        self.assertTrue(template.is_published)

        product.qty_available = 0
        self.env.invalidate_all()
        self.assertTrue(template.is_published)

    def test_sister_product_is_catalogue_only(self):
        """A sister product shows, but no cart button offers it.

        Odoo refuses an order line with another company's product, so the
        shop presents the native *Contact Us* action -- which is what the
        *Hide Add To Cart* machinery renders -- instead of a button whose
        every click could only end in the company-crossover error.
        """
        sister = self.env['res.company'].sudo().create({
            'name': 'Storefront Sister Traders',
        })
        product = self.env['product.product'].sudo().with_context(
            allowed_company_ids=[sister.id],
        ).create({
            'name': 'Storefront Sister Timbers',
            'type': 'consu',
            'is_storable': True,
            'list_price': 45.0,
            'company_id': sister.id,
            'sale_ok': True,
            'allow_out_of_stock_order': False,
            'qty_available': 12,
            'is_published': True,
        })
        public_variant = product.product_variant_id.with_user(self.public_user)
        website = self.website.with_user(self.public_user)

        self.assertFalse(public_variant._vuma_storefront_is_orderable())
        self.assertTrue(website._prevent_product_sale(public_variant, False))
        self.assertFalse(public_variant._is_add_to_cart_allowed())
        self.assertFalse(public_variant._website_show_quick_add())

    def test_cart_refusal_names_the_shops_for_a_catalogue_only_product(self):
        """A stale page that still asks the cart gets the truth.

        The button is hidden for another company's goods, but a tab opened
        before that rule can still ask the cart for one -- and a hand-made
        request always can. The refusal must say the item is sold in the
        shops, not repeat Odoo's "the product does not exist", which would
        be false in front of a customer.
        """
        sister = self.env['res.company'].sudo().create({
            'name': 'Storefront Sister Refusals',
        })
        product = self.env['product.product'].sudo().with_context(
            allowed_company_ids=[sister.id],
        ).create({
            'name': 'Storefront Sister Refusal Blocks',
            'type': 'consu',
            'is_storable': True,
            'list_price': 17.0,
            'company_id': sister.id,
            'sale_ok': True,
            'allow_out_of_stock_order': False,
            'qty_available': 9,
            'is_published': True,
        })

        with self.assertRaises(UserError) as refusal, self.mock_request():
            self.storefront_cart.add_to_cart(
                product_template_id=product.product_tmpl_id.id,
                product_id=product.id,
                quantity=1,
            )
        message = str(refusal.exception)
        self.assertIn('sold in our shops', message)
        self.assertIn(product.display_name, message)
        self.assertNotIn('does not exist', message)

    def test_missing_product_keeps_odoo_refusal(self):
        """A product id that really is gone still says so."""
        with self.mock_request():
            message = self.storefront_cart._get_product_user_error(
                self.env['product.product'],
            )
        self.assertIn('does not exist', message)

    def test_shared_product_remains_buyable(self):
        """A shared product (no company) keeps its cart button."""
        product = self._create_product(
            name='Storefront Shared Paint',
            is_storable=True,
            list_price=99.9,
            qty_available=3,
        )
        public_variant = product.product_variant_id.with_user(self.public_user)
        website = self.website.with_user(self.public_user)

        self.assertTrue(public_variant._vuma_storefront_is_orderable())
        self.assertFalse(website._prevent_product_sale(public_variant, False))
        self.assertTrue(public_variant._is_add_to_cart_allowed())
        self.assertTrue(public_variant._website_show_quick_add())

    def test_shop_quotes_the_selling_price(self):
        """With no website price list, the quote is the product's Sales Price."""
        product = self._create_product(
            name='Storefront Priced',
            is_storable=True,
            list_price=42.0,
            taxes_id=[],
            qty_available=1,
        )
        template = product.product_tmpl_id
        prices = template._get_sales_prices(
            self.env['product.pricelist'],
            self.env['account.fiscal.position'],
            self.website,
        )
        self.assertEqual(prices[template.id]['raw_pricelist_price'], 42.0)
        self.assertEqual(prices[template.id]['price_reduce'], 42.0)
