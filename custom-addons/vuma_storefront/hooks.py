# -*- coding: utf-8 -*-
"""Install-time adoption of the catalogue already on the shelves.

Odoo's automation (the *Unpublish Out-of-Stock Products* website setting)
only starts managing a product from the moment one of the fields it watches
changes. Everything that predates this module would therefore keep sitting at
its current value -- which, in this database, is "not published". The hook
walks the catalogue once and puts the existing products under the automation.
"""


def vuma_storefront_post_init(env):
    """Adopt every sellable storable product, then publish what is in stock.

    ``allow_out_of_stock_order`` is the field Odoo watches to decide whether
    it may hide a product when it runs out. Setting it to False is both the
    adoption itself (from now on stock changes manage the product's
    publication) and the write that triggers the publication compute, so an
    in-stock product becomes visible immediately.

    Products deliberately flagged *Sell when Out-of-Stock* are left alone:
    for those, being visible while empty is the point.

    :param env: superuser environment of the post-init hook.
    """
    # Stock is computed against the companies active in the environment
    # (`env.companies`). The shop fronts every company, so adopt -- and let
    # the publication compute price availability -- with all of them active.
    # The hook environment is already the superuser's; only the context has
    # to be widened.
    companies = env['res.company'].search([])
    env = env(context=dict(env.context, allowed_company_ids=companies.ids))

    templates = env['product.template'].search([
        ('sale_ok', '=', True),
        ('is_storable', '=', True),
        ('allow_out_of_stock_order', '=', True),
    ])
    templates.write({'allow_out_of_stock_order': False})

    # The write above already recomputes `is_published` for these products.
    # The explicit publish keeps the promise the description makes even if a
    # database arrives with the website feature switched off: in-stock items
    # are visible from the first page render.
    in_stock = templates.filtered(
        lambda template: any(variant.free_qty > 0 for variant in template.product_variant_ids)
    )
    in_stock.write({'is_published': True})
