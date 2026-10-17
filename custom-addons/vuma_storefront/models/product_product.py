# -*- coding: utf-8 -*-
"""The cart guard for products of another company."""
from odoo import models


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _vuma_storefront_is_orderable(self):
        """See ``product.template._vuma_storefront_is_orderable``."""
        self.ensure_one()
        return self.product_tmpl_id._vuma_storefront_is_orderable()

    def _is_add_to_cart_allowed(self):
        """Keep another company's product out of the cart server-side.

        The store front hides the button for these products and offers the
        *Contact Us* call to action instead; this guard makes the rule hold
        for direct RPC calls too, exactly as Odoo's *Hide Add To Cart*
        feature does for the products it covers.
        """
        return super()._is_add_to_cart_allowed() and self._vuma_storefront_is_orderable()
