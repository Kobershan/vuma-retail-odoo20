# -*- coding: utf-8 -*-
"""
Product Company-Domain widening.

THE core of the "see both companies when picking stock" behaviour.

In Odoo 20 the sale-order-line `product_id` Many2one carries
``check_company=True``. Its client-side dropdown domain is assembled in
``_description_domain`` as ``product.template._check_company_domain(...) + [sale_ok]``.
Widening ``_check_company_domain`` is therefore the ONLY reliable way to make
the product dropdown show both companies' products.
"""
from odoo import api, fields, models, _


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vuma_cross_company_sale = fields.Boolean(
        string='Available for Mixed Basket',
        default=True,
        help="If enabled, this product can be picked on mixed-basket quotes from other companies.",
    )

    def _vuma_mixed_basket_companies(self):
        """The two trading companies."""
        return self.env['res.company'].sudo().search([
            '|', ('name', '=', 'Trade Rite'), ('name', '=', 'Siyaya DC'),
        ])

    def _vuma_cross_company_active(self):
        """True when the current user should see products of both companies."""
        if self.env.context.get('vuma_mixed_basket'):
            return True
        if self.env.user.has_group('vuma_multi_tenant.group_vuma_mixed_basket_user'):
            return True
        return False

    def _check_company_domain(self, companies):
        if self._vuma_cross_company_active():
            both = self._vuma_mixed_basket_companies()
            if both:
                return super()._check_company_domain(both)
        return super()._check_company_domain(companies)


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _vuma_mixed_basket_companies(self):
        return self.env['res.company'].sudo().search([
            '|', ('name', '=', 'Trade Rite'), ('name', '=', 'Siyaya DC'),
        ])

    def _vuma_cross_company_active(self):
        if self.env.context.get('vuma_mixed_basket'):
            return True
        if self.env.user.has_group('vuma_multi_tenant.group_vuma_mixed_basket_user'):
            return True
        return False

    def _check_company_domain(self, companies):
        if self._vuma_cross_company_active():
            both = self._vuma_mixed_basket_companies()
            if both:
                return super()._check_company_domain(both)
        return super()._check_company_domain(companies)