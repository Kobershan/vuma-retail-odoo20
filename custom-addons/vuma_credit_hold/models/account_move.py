# -*- coding: utf-8 -*-
"""Invoice and credit-note side of the credit hold.

The quote is not the only way to extend credit to a customer: posting an
invoice, issuing a credit note or cancelling an overdue document can all move
the account. These guards keep the held customer's accounting documents under
the same administrator-only rule while leaving payment reconciliation open so
the debt can actually be cleared.

Payments are deliberately NOT blocked. A customer on hold must be able to pay.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

CUSTOMER_MOVE_TYPES = ('out_invoice', 'out_refund')

# Fields that can change whether the account is overdue or how much is owed.
# Payment reconciliation writes other fields (matched ids, amount_residual)
# and must stay allowed.
PROTECTED_MOVE_FIELDS = {
    'state', 'move_type', 'partner_id', 'invoice_date', 'invoice_date_due',
    'line_ids', 'amount_total', 'amount_untaxed', 'amount_tax',
}


class AccountMove(models.Model):
    _inherit = 'account.move'

    vuma_partner_credit_hold = fields.Boolean(
        related='commercial_partner_id.vuma_credit_hold',
        string='Credit Hold',
        readonly=True,
        help="The customer's account is on credit hold.",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _vuma_credit_hold_admin(self):
        return self.env.user.has_group('base.group_system')

    def _vuma_hold_moves(self):
        return self.filtered(
            lambda move: move.move_type in CUSTOMER_MOVE_TYPES
            and move.partner_id
            and move.commercial_partner_id._vuma_is_on_credit_hold()
        )

    def _vuma_require_hold_admin(self, action):
        if self.env.context.get('vuma_credit_hold_internal'):
            return
        for move in self._vuma_hold_moves():
            if not self._vuma_credit_hold_admin():
                raise UserError(
                    self.env['sale.order']._vuma_credit_hold_message(
                        move.commercial_partner_id, action))

    def _vuma_clear_hold_after_change(self):
        """Release the one-quote marker when documents settle the account."""
        partners = self.filtered(
            lambda move: move.move_type in CUSTOMER_MOVE_TYPES
        ).mapped('commercial_partner_id')
        if not partners:
            return
        partners._vuma_clear_hold_quote_if_settled()
        partners.invalidate_recordset([
            'vuma_credit_hold',
            'vuma_overdue_amount',
            'vuma_overdue_invoice_count',
            'vuma_oldest_due_date',
            'vuma_hold_quote_used',
        ])

    # ------------------------------------------------------------------
    # ORM entry points
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        if not self.env.context.get('vuma_credit_hold_internal'):
            for move in moves:
                if move.move_type not in CUSTOMER_MOVE_TYPES:
                    continue
                if (move.partner_id
                        and move.commercial_partner_id._vuma_is_on_credit_hold()
                        and not self._vuma_credit_hold_admin()):
                    raise UserError(
                        self.env['sale.order']._vuma_credit_hold_message(
                            move.commercial_partner_id,
                            _('create a customer invoice or credit note')))
            moves._vuma_clear_hold_after_change()
        return moves

    def write(self, vals):
        if (not self.env.context.get('vuma_credit_hold_internal')
                and not self._vuma_credit_hold_admin()
                and PROTECTED_MOVE_FIELDS.intersection(vals)):
            self._vuma_require_hold_admin(
                _('change this customer invoice or credit note'))
        res = super().write(vals)
        if {'state', 'payment_state', 'amount_residual', 'invoice_date_due',
                'partner_id', 'move_type'}.intersection(vals):
            self._vuma_clear_hold_after_change()
        return res

    def action_post(self):
        self._vuma_require_hold_admin(
            _('post this customer invoice or credit note'))
        res = super().action_post()
        self._vuma_clear_hold_after_change()
        return res

    def button_cancel(self):
        self._vuma_require_hold_admin(
            _('cancel this customer invoice or credit note'))
        return super().button_cancel()

    def button_draft(self):
        self._vuma_require_hold_admin(
            _('reset this customer invoice or credit note to draft'))
        return super().button_draft()

    def unlink(self):
        self._vuma_require_hold_admin(
            _('delete this customer invoice or credit note'))
        return super().unlink()


class AccountPartialReconcile(models.Model):
    _inherit = 'account.partial.reconcile'

    def _vuma_partners_to_refresh(self):
        moves = (
            self.mapped('debit_move_id.move_id')
            | self.mapped('credit_move_id.move_id')
        )
        return moves.filtered(
            lambda move: move.move_type in CUSTOMER_MOVE_TYPES
        ).mapped('commercial_partner_id')

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        partners = records._vuma_partners_to_refresh()
        if partners:
            partners._vuma_clear_hold_quote_if_settled()
            partners.invalidate_recordset([
                'vuma_credit_hold',
                'vuma_overdue_amount',
                'vuma_overdue_invoice_count',
                'vuma_oldest_due_date',
                'vuma_hold_quote_used',
            ])
        return records

    def unlink(self):
        partners = self._vuma_partners_to_refresh()
        res = super().unlink()
        if partners:
            partners._vuma_clear_hold_quote_if_settled()
            partners.invalidate_recordset([
                'vuma_credit_hold',
                'vuma_overdue_amount',
                'vuma_overdue_invoice_count',
                'vuma_oldest_due_date',
                'vuma_hold_quote_used',
            ])
        return res
