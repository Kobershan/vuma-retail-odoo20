# -*- coding: utf-8 -*-
"""Install helpers for the Vuma administration shell."""

from .models.res_company import DEFAULT_BACKGROUND_COLOURS


def post_init_hook(env):
    """Give existing trading companies a sensible starting colour.

    Fresh databases usually create Trade Rite and Siyaya DC later (from
    ``vuma_multi_tenant``), so this only fills them in when they already
    exist. Existing databases are covered by the 1.2.0 migration.
    """
    companies = env["res.company"].search([
        ("name", "in", list(DEFAULT_BACKGROUND_COLOURS)),
    ])
    for company in companies:
        if not company.vuma_background_color:
            company.vuma_background_color = DEFAULT_BACKGROUND_COLOURS[company.name]
