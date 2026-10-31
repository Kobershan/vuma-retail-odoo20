# -*- coding: utf-8 -*-
"""Stop goods leaving for a customer whose account is on credit hold.

A credit hold that only stops quotations is easy to walk around: an existing
delivery can still be validated and the customer receives the goods without a
new order. Outgoing transfers for a held account therefore need administrator
approval too. Incoming returns from the customer are left alone -- refusing
goods back would only make the account harder to settle.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    vuma_partner_credit_hold = fields.Boolean(
        related='partner_id.vuma_credit_hold',
        string='Credit Hold',
        readonly=True,
    )

    def _vuma_held_outgoing(self):
        return self.filtered(
            lambda picking: picking.picking_type_code == 'outgoing'
            and picking.partner_id
            and picking.partner_id.commercial_partner_id._vuma_is_on_credit_hold()
        )

    def _vuma_require_hold_admin(self, action):
        if self.env.context.get('vuma_credit_hold_internal'):
            return
        if self.env.user.has_group('base.group_system'):
            return
        held = self._vuma_held_outgoing()
        if held:
            raise UserError(
                self.env['sale.order']._vuma_credit_hold_message(
                    held[:1].partner_id.commercial_partner_id, action))

    @api.model_create_multi
    def create(self, vals_list):
        pickings = super().create(vals_list)
        pickings._vuma_require_hold_admin(_('create this delivery'))
        return pickings

    def write(self, vals):
        protected = {
            'state', 'partner_id', 'picking_type_id', 'location_id',
            'location_dest_id', 'move_ids', 'move_line_ids',
        }
        if protected.intersection(vals):
            self._vuma_require_hold_admin(_('change this delivery'))
        return super().write(vals)

    def button_validate(self):
        self._vuma_require_hold_admin(_('validate this delivery'))
        return super().button_validate()

    def action_cancel(self):
        self._vuma_require_hold_admin(_('cancel this delivery'))
        return super().action_cancel()

    def unlink(self):
        self._vuma_require_hold_admin(_('delete this delivery'))
        return super().unlink()
