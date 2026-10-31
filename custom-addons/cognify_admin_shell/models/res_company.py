# -*- coding: utf-8 -*-
"""Per-company colours for the Vuma administration shell."""

import re

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

# Initial colours for the two trading companies. They are deliberately
# different hues so a worker can tell at a glance which company they are in.
# Administrators can change them in Settings > Companies at any time.
DEFAULT_BACKGROUND_COLOURS = {
    "Trade Rite": "#2563EB",
    "Siyaya DC": "#EA580C",
}

_HEX_COLOUR = re.compile(r"^#[0-9a-fA-F]{6}$")


class ResCompany(models.Model):
    _inherit = "res.company"

    vuma_background_color = fields.Char(
        string="Vuma Background Colour",
        help="Background colour used by the Vuma administration shell while "
        "this company is active. Pick a colour that makes it obvious which "
        "company you are working in. Reload the page after changing it.",
    )

    @api.constrains("vuma_background_color")
    def _check_vuma_background_color(self):
        for company in self:
            colour = (company.vuma_background_color or "").strip()
            if colour and not _HEX_COLOUR.fullmatch(colour):
                raise ValidationError(
                    _(
                        "The Vuma background colour must be a six-digit "
                        "hexadecimal colour such as #2563EB."
                    )
                )
