# -*- coding: utf-8 -*-
"""A truthful answer when the cart is asked for a catalogue-only product.

The product page and the shop grid do not offer *Add to Cart* for another
company's goods -- they render the native *Contact Us* action instead -- but
a page opened before that rule went live (or a hand-made request) can still
ask. Odoo's default refusal, "The given product does not exist therefore it
cannot be added to cart", would be a false alarm in front of a customer: the
product exists, it is simply not sold through the cart. Answer with the
catalogue-only notice so the visitor knows to speak to the shop.
"""
from odoo.addons.website_sale.controllers.cart import Cart


class Cart(Cart):
    def _get_product_user_error(self, product):
        if product and not product._vuma_storefront_is_orderable():
            return self.env._(
                "%s is sold in our shops, not online. Please contact us to order it.",
                product.display_name,
            )
        return super()._get_product_user_error(product)
