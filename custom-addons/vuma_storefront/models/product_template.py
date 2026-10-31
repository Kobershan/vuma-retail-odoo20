# -*- coding: utf-8 -*-
"""Shop publication and the catalogue-only rule for other companies' goods."""
from odoo import models


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _vuma_storefront_is_orderable(self):
        """May the website cart sell this product?

        The cart's sale order belongs to the website's company and Odoo
        refuses an order line carrying another company's product ("no
        company crossover is allowed"). Shared products (no company) and the
        website company's own products can be sold online; Trade Rite and
        Siyaya DC goods are shown in the shop as the group's catalogue --
        with their selling price and stock badge -- but ordered through
        staff, not the cart.

        :return: whether this product can join the website cart
        :rtype: bool
        """
        self.ensure_one()
        website = self.env.website
        website_company = website.company_id or self.env.user.company_id
        return not self.company_id or self.company_id == website_company

    def _website_show_quick_add(self, product=None):
        """The grid's quick-add button follows the same rule as the cart."""
        self.ensure_one()
        return (
            self._vuma_storefront_is_orderable()
            and super()._website_show_quick_add(product)
        )

    def _compute_is_published(self):
        """Judge stock with every company active.

        Odoo's automation unpublishes a product when no variant has free
        quantity left, and republishes it when stock comes back. The
        quantity is computed against the companies active in the environment
        that triggers the recomputation (``env.companies``), which is right
        for a receipt or delivery made by the owning company's own session,
        but wrong for the store front's promise the moment the change is
        made from a session where the owning company is not active: a Trade
        Rite product touched from a Siyaya-only session would read as
        empty and vanish from the shop while the warehouse is full.

        The shop fronts the whole business, so the decision is always made
        with every company active -- the same rule the installation hook
        adopts the catalogue with. Superuser rights are taken because the
        compute is also triggered by visitors' page views; publication is a
        system decision, not the visitor's write.

        :return: None
        """
        companies = self.env['res.company'].sudo().search([]).ids
        super(ProductTemplate, self.sudo().with_context(
            allowed_company_ids=companies,
        ))._compute_is_published()
