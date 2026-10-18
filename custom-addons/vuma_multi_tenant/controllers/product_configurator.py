# -*- coding: utf-8 -*-
"""
Sale product configurator: keep BOTH trading companies readable.

Odoo's configurator narrows the request to a single company before it reads the
product being configured::

    if company_id:
        request.update_context(allowed_company_ids=[company_id])

``company_id`` there is the *quotation's* company. On a mixed-basket quote the
rep legitimately picks a product owned by the OTHER company, so the scope is
pinned to one company and the read is refused:

    Access Denied for operation: read on record ids: [5199], model: product.template
    Sorry, Administrator (id=2) doesn't have 'read' access to: Product
    ... you might be able to access the record by switching to the company: Siyaya DC

That narrowing is only meaningful for single-company shops. Here the whole
point of the quote is to span both trading companies, so the configured product
has to stay readable no matter which company the quote sits in. Pricing is left
untouched: ``company_id`` is still forwarded to the original method, it is only
the access scope that is kept as the rep set it.
"""
from odoo.http import request, route

from odoo.addons.sale.controllers.product_configurator import (
    SaleProductConfiguratorController,
)


class VumaSaleProductConfiguratorController(SaleProductConfiguratorController):
    @route(
        route="/sale/product_configurator/get_values",
        type="jsonrpc",
        auth="user",
        readonly=True,
    )
    def sale_product_configurator_get_values(
        self, product_template_id, quantity, so_date, **kwargs
    ):
        original = request.update_context

        def keep_company_scope(**ctx):
            # Drop ONLY the narrowing of allowed_company_ids; every other
            # context key the configurator sets (show_price, ...) is honoured.
            ctx.pop("allowed_company_ids", None)
            return original(**ctx)

        request.update_context = keep_company_scope
        try:
            return super().sale_product_configurator_get_values(
                product_template_id, quantity, so_date, **kwargs
            )
        finally:
            # Always put it back, even if the configured product blows up.
            request.update_context = original
