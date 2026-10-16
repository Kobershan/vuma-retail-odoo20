# -*- coding: utf-8 -*-
"""
Res Users Extensions

Adds multi-tenant helper fields for users.
"""
from odoo import api, fields, models, _


class ResUsers(models.Model):
    _inherit = 'res.users'

    vuma_mixed_basket_access = fields.Boolean(
        string='Mixed Basket Access',
        default=False,
        help="Allow user to work across Trade Rite & Siyaya DC without switching companies.",
    )

    vuma_default_mixed_basket_company_id = fields.Many2one(
        'res.company',
        string='Default Mixed Basket Company',
        help="Default company context when creating mixed basket quotes",
    )

    @api.onchange('vuma_mixed_basket_access')
    def _onchange_vuma_mixed_basket_access(self):
        if self.vuma_mixed_basket_access:
            self.sudo().vuma_join_mixed_basket_group()

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        if any(vals.get('vuma_mixed_basket_access') for vals in vals_list):
            users.vuma_join_mixed_basket_group()
        return users

    def write(self, vals):
        res = super().write(vals)
        if vals.get('vuma_mixed_basket_access'):
            self.vuma_join_mixed_basket_group()
        return res

    def vuma_join_mixed_basket_group(self):
        """Give the user the Mixed Basket group (and all its implications)."""
        group = self.env.ref('vuma_multi_tenant.group_vuma_mixed_basket_user', False)
        companies = self.env['res.company'].sudo().search([
            '|', ('name', '=', 'Trade Rite'), ('name', '=', 'Siyaya DC')])
        for user in self:
            if group and group not in user.sudo().group_ids:
                user.sudo().group_ids |= group
            if companies:
                # Both trading companies must be allowed for the user so
                # records of either company can be read/created without
                # switching profile.
                user.sudo().company_ids |= companies