# -*- coding: utf-8 -*-
"""Set the initial Vuma colours on existing trading companies."""

DEFAULT_BACKGROUND_COLOURS = {
    "Trade Rite": "#2563EB",
    "Siyaya DC": "#EA580C",
}


def migrate(cr, version):
    if not version:
        return
    for company_name, colour in DEFAULT_BACKGROUND_COLOURS.items():
        cr.execute(
            """
            UPDATE res_company
               SET vuma_background_color = %s
             WHERE name = %s
               AND (vuma_background_color IS NULL OR vuma_background_color = '')
            """,
            (colour, company_name),
        )
