# -*- coding: utf-8 -*-
"""Sales-side enforcement of the credit hold.

The rule is deliberately enforced in the ORM rather than only in the views:

* a non-administrator cannot create, change, send, confirm, cancel or delete a
  quotation/order for a held customer;
* an administrator may create exactly one quotation while the hold is active;
* that one quotation (and the company orders it splits into, if it is a mixed
  basket) is the only sales order that may be touched until the debt is paid;
* order lines follow the same rule, so the restriction cannot be bypassed by
  writing a line directly.
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    vuma_partner_credit_hold = fields.Boolean(
        related='partner_id.vuma_credit_hold',
        string='Credit Hold',
        readonly=True,
        help="The customer's account is on credit hold.",
    )
    vuma_partner_overdue_amount = fields.Monetary(
        related='partner_id.vuma_overdue_amount',
        string='Overdue',
        currency_field='currency_id',
        readonly=True,
    )
    vuma_partner_overdue_invoice_count = fields.Integer(
        related='partner_id.vuma_overdue_invoice_count',
        string='Overdue Invoices',
        readonly=True,
    )
    vuma_hold_quote_used = fields.Boolean(
        related='partner_id.vuma_hold_quote_used',
        string='Credit-Hold Quotation Used',
        readonly=True,
    )
    vuma_hold_quote_exception = fields.Boolean(
        string='Credit-Hold Quotation',
        readonly=True,
        copy=False,
        index=True,
        help="This is the one quotation allowed for a customer on credit "
             "hold. It is set automatically when an administrator creates or "
             "claims the quotation.",
    )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _vuma_credit_hold_admin(self):
        """Administrators are the Odoo 'Settings' administrators."""
        return self.env.user.has_group('base.group_system')

    @api.model
    def _vuma_credit_hold_message(self, partner, action, quote=None,
                                  quote_used=False):
        """Actionable refusal message for a held commercial account."""
        partner = partner.commercial_partner_id
        amount, count, oldest = partner._vuma_overdue_snapshot()
        currency = partner.currency_id
        amount_text = (
            f"{currency.symbol or ''} {amount:,.2f}".strip()
            if currency else f"{amount:,.2f}"
        )
        oldest_text = (
            oldest.strftime('%d %b %Y') if oldest else _('unknown')
        )
        if quote_used or quote:
            if quote:
                reference = _(
                    "(%(quote)s)", quote=quote.name or quote.display_name)
            else:
                reference = _("(it has since been deleted or cancelled)")
            return _(
                "%(partner)s is on credit hold. The one quotation allowed "
                "while the account is on hold has already been used "
                "%(reference)s.\n\n"
                "The account can only be quoted again after the overdue "
                "balance of %(amount)s is paid and the account is up to date.",
                partner=partner.display_name,
                reference=reference,
                amount=amount_text,
            )
        return _(
            "%(partner)s is on credit hold: %(count)s overdue invoice(s) "
            "totalling %(amount)s, oldest due %(oldest)s.\n\n"
            "Only an administrator can create the one quotation allowed for "
            "an account on hold. You cannot %(action)s.\n\n"
            "Payments can still be recorded. Once the overdue balance is "
            "paid and the account is up to date, the hold is released.",
            partner=partner.display_name,
            count=count,
            amount=amount_text,
            oldest=oldest_text,
            action=action,
        )

    def _vuma_is_held_order(self):
        self.ensure_one()
        return bool(
            self.partner_id
            and self.partner_id.commercial_partner_id._vuma_is_on_credit_hold()
        )

    @api.model
    def _vuma_lock_hold_partner(self, partner):
        """Lock the commercial partner row and return its allowance state."""
        # Make sure a write made earlier in this transaction (claiming or
        # clearing the allowance) is visible to the row lock query.
        self.env['res.partner'].flush_model([
            'vuma_hold_quote_id', 'vuma_hold_quote_used',
        ])
        self.env.cr.execute(
            "SELECT vuma_hold_quote_id, vuma_hold_quote_used "
            "FROM res_partner WHERE id = %s FOR UPDATE",
            [partner.id],
        )
        row = self.env.cr.fetchone()
        return (row[0], bool(row[1])) if row else (False, False)

    @api.model
    def _vuma_precheck_hold_partner(self, partner, action):
        """Refuse a create/claim before a held account's order is written.

        Returns True when the partner is on hold and the administrator still
        has the one-quotation allowance.
        """
        commercial = partner.commercial_partner_id
        if not commercial._vuma_is_on_credit_hold():
            commercial._vuma_clear_hold_quote_if_settled()
            return False
        if not self._vuma_credit_hold_admin():
            raise UserError(
                self._vuma_credit_hold_message(commercial, action))
        quote_id, used = self._vuma_lock_hold_partner(commercial)
        if used:
            quote = self.env['sale.order'].browse(quote_id)
            raise UserError(
                self._vuma_credit_hold_message(
                    commercial, action, quote=quote, quote_used=True))
        return True

    @api.model
    def _vuma_claim_hold_quote(self, order):
        """Record ``order`` as the one quotation allowed for its held account."""
        commercial = order.partner_id.commercial_partner_id
        if not commercial or not commercial._vuma_is_on_credit_hold():
            return False
        quote_id, used = self._vuma_lock_hold_partner(commercial)
        if (used or quote_id) and quote_id != order.id:
            quote = self.env['sale.order'].browse(quote_id)
            raise UserError(self._vuma_credit_hold_message(
                commercial, _('use this quotation'), quote=quote,
                quote_used=True))
        commercial.sudo().write({
            'vuma_hold_quote_id': order.id,
            'vuma_hold_quote_used': True,
        })
        order.with_context(vuma_credit_hold_internal=True).sudo().write({
            'vuma_hold_quote_exception': True,
        })
        return True

    def _vuma_check_order_access(self, action, claim=False):
        """Raise unless the current user may touch every order in ``self``."""
        if self.env.context.get('vuma_credit_hold_internal'):
            return
        for order in self:
            partner = order.partner_id
            if not partner:
                continue
            commercial = partner.commercial_partner_id
            if not commercial._vuma_is_on_credit_hold():
                commercial._vuma_clear_hold_quote_if_settled()
                continue
            if not self._vuma_credit_hold_admin():
                raise UserError(
                    self._vuma_credit_hold_message(commercial, action))
            quote = commercial.vuma_hold_quote_id
            if quote == order:
                continue
            # A mixed basket splits into one order per company. Those split
            # orders are part of the same single quotation.
            if (quote and order.vuma_hold_quote_exception
                    and order.vuma_original_mixed_quote_id == quote):
                continue
            if quote:
                raise UserError(self._vuma_credit_hold_message(
                    commercial, action, quote=quote, quote_used=True))
            if commercial.vuma_hold_quote_used:
                # The quotation may have been deleted or cancelled; the
                # allowance is still spent for this hold episode.
                raise UserError(self._vuma_credit_hold_message(
                    commercial, action, quote_used=True))
            if claim:
                self._vuma_claim_hold_quote(order)
                continue
            raise UserError(_(
                "This customer is on credit hold. Use the 'Use as "
                "Credit-Hold Quotation' button on the draft you want to keep "
                "before you %(action)s.",
                action=action,
            ))

    def _vuma_require_hold_admin(self, action):
        """Non-administrators may not cancel/delete a held customer's order."""
        if self.env.context.get('vuma_credit_hold_internal'):
            return
        for order in self:
            commercial = order.partner_id.commercial_partner_id
            if commercial and commercial._vuma_is_on_credit_hold():
                if not self._vuma_credit_hold_admin():
                    raise UserError(
                        self._vuma_credit_hold_message(commercial, action))

    # ------------------------------------------------------------------
    # ORM entry points
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        # Odoo creates order lines inside sale.order.create(), before the new
        # order can be marked as the held account's exception. Pre-check the
        # partner and run the create with the internal flag so line creation
        # does not refuse the administrator's one allowed quotation.
        held = False
        if not self.env.context.get('vuma_credit_hold_internal'):
            for vals in vals_list:
                partner_id = vals.get('partner_id')
                if not partner_id:
                    continue
                partner = self.env['res.partner'].browse(partner_id)
                if self._vuma_precheck_hold_partner(
                        partner, _('create a quotation')):
                    held = True

        order_env = self.with_context(
            vuma_credit_hold_internal=True) if held else self
        orders = super(SaleOrder, order_env).create(vals_list)

        if not self.env.context.get('vuma_credit_hold_internal'):
            for order in orders:
                commercial = order.partner_id.commercial_partner_id
                if commercial._vuma_is_on_credit_hold():
                    if not self._vuma_credit_hold_admin():
                        raise UserError(self._vuma_credit_hold_message(
                            commercial, _('create a quotation')))
                    self._vuma_claim_hold_quote(order)
            # Do not leak the private creation flag into the recordset the
            # caller receives: a later write in the same transaction must still
            # be checked against the credit hold.
            return orders.with_context(vuma_credit_hold_internal=False)
        return orders

    def write(self, vals):
        if not self.env.context.get('vuma_credit_hold_internal'):
            new_partner = (
                self.env['res.partner'].browse(vals['partner_id'])
                if vals.get('partner_id') else None
            )
            current_commercial = self.mapped(
                'partner_id.commercial_partner_id')
            if new_partner:
                new_commercial = new_partner.commercial_partner_id
                if (new_commercial not in current_commercial
                        and new_commercial._vuma_is_on_credit_hold()):
                    raise UserError(_(
                        "You cannot move an existing quotation to a customer "
                        "on credit hold. Create the one quotation allowed for "
                        "that customer instead."))
                for order in self:
                    if (order.vuma_hold_quote_exception
                            and new_partner != order.partner_id):
                        raise UserError(_(
                            "The customer cannot be changed on the one "
                            "credit-hold quotation."))
            self._vuma_check_order_access(_('change this sales order'))
        return super().write(vals)

    def action_confirm(self):
        self._vuma_check_order_access(
            _('confirm this quotation'), claim=True)
        return super().action_confirm()

    def action_quotation_send(self):
        self._vuma_check_order_access(
            _('send this quotation'), claim=True)
        return super().action_quotation_send()

    def action_cancel(self):
        self._vuma_require_hold_admin(_('cancel this sales order'))
        return super(
            SaleOrder,
            self.with_context(vuma_credit_hold_internal=True),
        ).action_cancel()

    def action_draft(self):
        self._vuma_require_hold_admin(_('reset this sales order to draft'))
        return super(
            SaleOrder,
            self.with_context(vuma_credit_hold_internal=True),
        ).action_draft()

    def unlink(self):
        self._vuma_require_hold_admin(_('delete this sales order'))
        return super().unlink()

    # ------------------------------------------------------------------
    # Admin controls
    # ------------------------------------------------------------------
    def action_vuma_claim_hold_quote(self):
        """Let an administrator use an existing draft as the one quotation."""
        self.ensure_one()
        if not self._vuma_credit_hold_admin():
            raise UserError(_(
                "Only an administrator can choose the credit-hold quotation."))
        if self.state != 'draft':
            raise UserError(_(
                "Only a draft quotation can be used as the credit-hold "
                "quotation."))
        commercial = self.partner_id.commercial_partner_id
        if not commercial._vuma_is_on_credit_hold():
            raise UserError(_(
                "This customer is not on credit hold, so no special "
                "quotation is needed."))
        self._vuma_claim_hold_quote(self)
        return {'type': 'ir.actions.client', 'tag': 'reload'}

    # ------------------------------------------------------------------
    # Mixed-basket split: the split company orders are part of the one quote
    # ------------------------------------------------------------------
    def _create_company_quote(self, company, lines):
        order = super(
            SaleOrder,
            self.with_context(vuma_credit_hold_internal=True),
        )._create_company_quote(company, lines)
        if order.partner_id.commercial_partner_id._vuma_is_on_credit_hold():
            # Link the split order to the mixed quote immediately: the parent
            # writes this field again after confirmation, and the write must
            # already recognise the split as part of the one quotation.
            order.with_context(
                vuma_credit_hold_internal=True).sudo().write({
                    'vuma_hold_quote_exception': True,
                    'vuma_original_mixed_quote_id': self.id,
                })
        return order.with_context(vuma_credit_hold_internal=False)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        if not self.env.context.get('vuma_credit_hold_internal'):
            lines.order_id._vuma_check_order_access(
                _('add or change lines on this quotation'))
        return lines

    def write(self, vals):
        before = self.order_id
        res = super().write(vals)
        after = self.order_id
        if not self.env.context.get('vuma_credit_hold_internal'):
            (before | after)._vuma_check_order_access(
                _('add or change lines on this quotation'))
        return res

    def unlink(self):
        if not self.env.context.get('vuma_credit_hold_internal'):
            self.order_id._vuma_check_order_access(
                _('remove lines from this quotation'))
        return super().unlink()
