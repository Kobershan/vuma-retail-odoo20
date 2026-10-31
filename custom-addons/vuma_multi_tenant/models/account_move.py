# -*- coding: utf-8 -*-
"""account.move.line extensions.

Keeps the stock-source company AND the exact stock location on customer
invoice lines created from mixed-basket sale orders, so the accountant can
see which company supplied each line -- and which warehouse the stock came
from -- even after invoicing.

Also guards invoice capture against the active company scope: a user working
in Trade Rite must switch to Siyaya DC before creating or posting a Siyaya DC
invoice. Cross-company wizards use ``with_company`` internally, which adds the
target company to the active scope, so their legitimate multi-company work is
not blocked.
"""
from odoo import _, api, fields, models


class AccountMove(models.Model):
    _inherit = 'account.move'

    @api.model
    def _vuma_scope_companies_from_vals(self, vals):
        """Companies a move will belong to, including an implicit journal one.

        ``company_id`` is not always in the create values: Odoo can compute it
        from ``journal_id``. Checking only the explicit key would let an RPC
        create a Siyaya DC journal entry while Trade Rite is the active scope.
        """
        companies = self.env['res.company']
        if vals.get('company_id'):
            companies |= self.env['res.company'].browse(vals['company_id'])
        if vals.get('journal_id'):
            companies |= self.env['account.journal'].browse(
                vals['journal_id']).company_id
        return companies

    @api.model_create_multi
    def create(self, vals_list):
        companies = self.env['res.company']
        for vals in vals_list:
            companies |= self._vuma_scope_companies_from_vals(vals)
        if companies:
            self.env.user._vuma_require_companies(
                companies,
                _('capture this invoice or journal entry'),
            )
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('company_id'):
            self.env.user._vuma_require_company(
                self.env['res.company'].browse(vals['company_id']),
                _('move this invoice or journal entry to another company'),
            )
        return super().write(vals)

    def action_post(self):
        self.env.user._vuma_require_companies(
            self.mapped('company_id'),
            _('post this invoice or journal entry'),
        )
        return super().action_post()


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    vuma_source_company_id = fields.Many2one(
        'res.company', string='Stock Source Company', copy=False,
        index=True,
        help="Company whose product/stock this line came from (mixed basket).")
    vuma_source_location_id = fields.Many2one(
        'stock.location', string='Stock Source Location', copy=False,
        help="Stock location the invoiced goods were drawn from.")
