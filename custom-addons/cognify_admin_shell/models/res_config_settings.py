# -*- coding: utf-8 -*-
"""Expose the shell colour in the Settings search."""

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    vuma_background_color = fields.Char(
        related="company_id.vuma_background_color",
        readonly=False,
        string="Company Background Colour",
        help="Colour painted behind the main screen while this company is "
        "active. The sidebar always stays black. Reload the page after "
        "changing it.",
    )
