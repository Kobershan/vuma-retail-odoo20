# -*- coding: utf-8 -*-
"""account.move.line extensions.

Keeps the stock-source company AND the exact stock location on customer
invoice lines created from mixed-basket sale orders, so the accountant can
see which company supplied each line -- and which warehouse the stock came
from -- even after invoicing.
"""
from odoo import fields, models


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    vuma_source_company_id = fields.Many2one(
        'res.company', string='Stock Source Company', copy=False,
        index=True,
        help="Company whose product/stock this line came from (mixed basket).")
    vuma_source_location_id = fields.Many2one(
        'stock.location', string='Stock Source Location', copy=False,
        help="Stock location the invoiced goods were drawn from.")