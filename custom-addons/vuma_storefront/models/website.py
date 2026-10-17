# -*- coding: utf-8 -*-
"""Store front listing and the catalogue the visitor is allowed to read."""
from odoo import api, models
from odoo.fields import Domain


class Website(models.Model):
    _inherit = 'website'

    def sale_product_domain(self):
        """The shop lists every company's catalogue, not only its own.

        Odoo 20 pins a website's shop to the website's company
        (``company_id in [False, website.company_id.id]``). Vuma Retail trades
        through Trade Rite and Siyaya DC as well, and the online shop is the
        window of the whole business: stock, price and availability all come
        from the company that owns the product, so a visitor must be able to
        see all of them side by side.

        The rest of the method mirrors ``website_sale``'s on purpose -- same
        saleable, website and published clauses, only the company clause
        differs -- because there is no hook to widen just that one clause.
        Keep an eye on the base method when Odoo is upgraded.

        :rtype: Domain
        """
        website = self or self.env.website
        if self.env.user._is_internal():
            user_domain = Domain.TRUE
        else:
            user_domain = [
                ('is_published', '=', True),
                ('service_tracking', 'in', self.env['product.template']._get_saleable_tracking_types()),
            ]
        companies = self.env['res.company'].sudo().search([]).ids
        company_domain = [('company_id', 'in', [False, *companies])]
        return Domain.AND([
            website._product_domain(),
            website.website_domain(),
            user_domain,
            company_domain,
        ])

    def _get_product_available_qty(self, product, **kwargs):
        """Count availability across every company's shelves.

        Odoo measures availability against the companies active in the
        environment (``env.companies``), which for a visitor is the website's
        own company alone. A Trade Rite or Siyaya DC product would therefore
        read as empty: the shop would stamp it *Sold out*, say
        ``OutOfStock`` to search engines, and the cart would refuse it, all
        while the warehouse holds hundreds. Widening the context to every
        company gives availability the same reach as the product domain
        above.

        :param product: product.product record
        :param dict kwargs: passed through to ``_get_free_qty``
        :return: available quantity
        :rtype: float
        """
        companies = self.env['res.company'].sudo().search([]).ids
        return (
            product
            .sudo()
            .with_context(allowed_company_ids=companies)
            ._get_free_qty(**kwargs)
        )

    def _prevent_product_sale(self, product_or_template, is_zero_price_product):
        """Another company's product is not sold through the cart.

        This is Odoo's *Hide Add To Cart* lever, so the store front reuses
        its whole outcome for products the cart cannot accept: the product
        page trades the button for the native *Contact Us* action and the
        shop tiles do the same, instead of offering a button that could only
        end in the company-crossover error. The regular *Hide Add To Cart*
        setting, when enabled, keeps its own behaviour.

        :param product.template|product.product product_or_template: the
            product whose sale is being considered
        :param bool is_zero_price_product: whether the combination is free;
            only the setting's own rule uses it
        :return: whether the sale is prevented
        :rtype: bool
        """
        return (
            not product_or_template._vuma_storefront_is_orderable()
            or super()._prevent_product_sale(product_or_template, is_zero_price_product)
        )

    @api.model
    def _vuma_storefront_open_product_rule(self):
        """Make every product of ``sale_product_domain`` readable by visitors.

        The shop domain above lists every company's products, but a public
        visitor's read is also gated by ``product.product_comp_rule``, the
        global multi-company restriction shipped by the `product` module. By
        default it only admits the companies the visitor has active, which on
        the website is the website's company alone: listing a Trade Rite
        product would raise an AccessError instead of showing it. The rule is
        widened here so the two halves stay in step.

        This runs from ``data/storefront_data.xml`` on install and on every
        update; the explicit company list is refreshed on each run, so
        companies created later join the store front as soon as the module is
        next updated.

        :return: the rewritten `ir.access` rule (empty if `product` is not
            installed, which cannot happen while `website_sale` is).
        :rtype: ir.access
        """
        rule = self.env.ref('product.product_comp_rule', raise_if_not_found=False)
        if not rule:
            return rule
        companies = self.env['res.company'].sudo().search([]).ids
        wanted = (
            "['|', '|', ('company_id', '=', False), "
            "('company_id', 'parent_of', company_ids), "
            "('company_id', 'in', %s)]" % companies
        )
        rule_sudo = rule.sudo()
        if rule_sudo.domain != wanted:
            rule_sudo.domain = wanted
        return rule
