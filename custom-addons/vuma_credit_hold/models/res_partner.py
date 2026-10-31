# -*- coding: utf-8 -*-
"""Credit-hold state on the customer account.

The hold is derived from the customer's posted, unpaid customer documents. It
is not a manually maintained flag: an account cannot be "forgotten" off hold,
and paying the debt releases it without anyone having to remember to do so.

A customer is on hold when the **net** overdue balance of the commercial
entity is positive. Overdue invoices are counted from their payment-term lines,
so an instalment that is due today does not put the account on hold while a
different instalment of the same invoice is already late. Posted credit notes
reduce the balance instead of increasing it (their receivable line carries a
negative residual).
"""
from odoo import _, api, fields, models
from odoo.exceptions import UserError

# Cent-level tolerance: rounding noise must not put an account on hold.
HOLD_EPSILON = 0.005


class ResPartner(models.Model):
    _inherit = 'res.partner'

    vuma_credit_hold = fields.Boolean(
        string='Credit Hold',
        compute='_compute_vuma_credit_hold',
        search='_search_vuma_credit_hold',
        help="The customer's commercial account has an overdue balance. "
             "Only an administrator may create one quotation while the hold "
             "is active, and no other sales activity is allowed.",
    )
    vuma_overdue_amount = fields.Monetary(
        string='Overdue',
        compute='_compute_vuma_credit_hold',
        currency_field='currency_id',
        help="Net overdue balance on posted customer invoices and credit "
             "notes, in the customer's company currency.",
    )
    vuma_overdue_invoice_count = fields.Integer(
        string='Overdue Invoices',
        compute='_compute_vuma_credit_hold',
        help="Number of posted customer invoices with at least one overdue "
             "instalment.",
    )
    vuma_oldest_due_date = fields.Date(
        string='Oldest Overdue Due Date',
        compute='_compute_vuma_credit_hold',
        help="Due date of the oldest unpaid instalment on the account.",
    )
    vuma_hold_quote_id = fields.Many2one(
        'sale.order',
        string='Credit-Hold Quotation',
        readonly=True,
        copy=False,
        index=True,
        ondelete='set null',
        help="The one quotation an administrator was allowed to create while "
             "this account was on credit hold. It is cleared automatically "
             "when the account is paid and up to date.",
    )
    vuma_hold_quote_used = fields.Boolean(
        string='Credit-Hold Quotation Used',
        readonly=True,
        copy=False,
        help="The one-quotation allowance for the current credit hold has "
             "already been used. It is cleared automatically when the account "
             "is paid and up to date.",
    )

    # ------------------------------------------------------------------
    # Overdue calculation
    # ------------------------------------------------------------------
    @api.model
    def _vuma_overdue_stats(self, commercial_partner_ids):
        """Overdue totals keyed by commercial partner id.

        Returns ``{partner_id: (net_amount, invoice_count, oldest_due)}``.
        The query runs before the ORM's raw SQL because payment reconciliation
        can leave ``amount_residual`` waiting to be flushed.
        """
        ids = tuple(sorted(set(commercial_partner_ids)))
        if not ids:
            return {}

        self.env['account.move'].flush_model([
            'move_type', 'state', 'commercial_partner_id',
        ])
        self.env['account.move.line'].flush_model([
            'move_id', 'display_type', 'date_maturity', 'amount_residual',
        ])
        self.env.cr.execute("""
            SELECT
                am.commercial_partner_id,
                ROUND(SUM(aml.amount_residual), 2)                  AS net,
                COUNT(DISTINCT am.id) FILTER (
                    WHERE aml.amount_residual > 0)                  AS invoice_count,
                MIN(aml.date_maturity) FILTER (
                    WHERE aml.amount_residual > 0)                  AS oldest_due
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE am.move_type IN ('out_invoice', 'out_refund')
              AND am.state = 'posted'
              AND aml.display_type = 'payment_term'
              AND aml.date_maturity < CURRENT_DATE
              AND aml.amount_residual != 0
              AND am.commercial_partner_id IN %s
            GROUP BY am.commercial_partner_id
        """, [ids])
        return {
            partner_id: (net or 0.0, invoice_count or 0, oldest_due)
            for partner_id, net, invoice_count, oldest_due
            in self.env.cr.fetchall()
        }

    def _vuma_overdue_snapshot(self):
        """(amount, count, oldest_due) for this partner's commercial account."""
        self.ensure_one()
        commercial = self.commercial_partner_id
        net, count, oldest = self._vuma_overdue_stats(
            [commercial.id]).get(commercial.id, (0.0, 0, False))
        return max(net, 0.0), count, oldest

    def _vuma_is_on_credit_hold(self):
        self.ensure_one()
        return self._vuma_overdue_snapshot()[0] > HOLD_EPSILON

    def _vuma_overdue_ids(self):
        """Commercial partner ids whose net overdue balance is positive."""
        self.env['account.move'].flush_model([
            'move_type', 'state', 'commercial_partner_id',
        ])
        self.env['account.move.line'].flush_model([
            'move_id', 'display_type', 'date_maturity', 'amount_residual',
        ])
        self.env.cr.execute("""
            SELECT am.commercial_partner_id
            FROM account_move_line aml
            JOIN account_move am ON am.id = aml.move_id
            WHERE am.move_type IN ('out_invoice', 'out_refund')
              AND am.state = 'posted'
              AND aml.display_type = 'payment_term'
              AND aml.date_maturity < CURRENT_DATE
              AND aml.amount_residual != 0
            GROUP BY am.commercial_partner_id
            HAVING ROUND(SUM(aml.amount_residual), 2) > %s
        """, [HOLD_EPSILON])
        return [row[0] for row in self.env.cr.fetchall()]

    @api.depends('commercial_partner_id')
    def _compute_vuma_credit_hold(self):
        stats = self._vuma_overdue_stats(self.commercial_partner_id.ids)
        for partner in self:
            commercial_id = partner.commercial_partner_id.id
            net, count, oldest = stats.get(
                commercial_id, (0.0, 0, False))
            amount = max(net, 0.0)
            partner.vuma_overdue_amount = amount
            partner.vuma_overdue_invoice_count = count
            partner.vuma_oldest_due_date = oldest
            partner.vuma_credit_hold = amount > HOLD_EPSILON

    def _search_vuma_credit_hold(self, operator, value):
        if operator not in ('=', '!='):
            raise UserError(_(
                "The credit-hold filter only supports 'is' and 'is not'."))
        value = bool(value)
        held_ids = self._vuma_overdue_ids()
        # Filter on the commercial account, not on the partner alone: a
        # delivery address shares the hold of the company it belongs to.
        if (operator == '=') == value:
            return [('commercial_partner_id', 'in', held_ids)]
        return [('commercial_partner_id', 'not in', held_ids)]

    # ------------------------------------------------------------------
    # Releasing the allowance
    # ------------------------------------------------------------------
    def _vuma_clear_hold_quote_if_settled(self):
        """Clear the one-quotation marker once the account is up to date.

        Called whenever invoices, credit notes or reconciliation change. Only
        accounts that actually have a marker are queried, so the normal sales
        flow pays nothing for this check.
        """
        partners = self.mapped('commercial_partner_id')
        # The boolean is authoritative: the quotation may have been deleted,
        # and the allowance must still stay used until the debt is settled.
        held = partners.filtered(
            lambda partner: partner.vuma_hold_quote_used
            or partner.vuma_hold_quote_id
        )
        if not held:
            return
        stats = self._vuma_overdue_stats(held.ids)
        for partner in held:
            net, _count, _oldest = stats.get(partner.id, (0.0, 0, False))
            if net <= HOLD_EPSILON:
                partner.sudo().write({
                    'vuma_hold_quote_id': False,
                    'vuma_hold_quote_used': False,
                })

    @api.model
    def _cron_clear_credit_hold_quotes(self):
        """Safety net for balances settled outside a reconciliation write."""
        partners = self.sudo().search([
            '|',
            ('vuma_hold_quote_used', '=', True),
            ('vuma_hold_quote_id', '!=', False),
        ])
        partners._vuma_clear_hold_quote_if_settled()

    # ------------------------------------------------------------------
    # UI helper
    # ------------------------------------------------------------------
    def action_view_vuma_overdue_invoices(self):
        self.ensure_one()
        commercial = self.commercial_partner_id
        return {
            'name': _('Overdue Invoices — %s', commercial.display_name),
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [
                ('commercial_partner_id', '=', commercial.id),
                ('move_type', 'in', ('out_invoice', 'out_refund')),
                ('state', '=', 'posted'),
                ('invoice_date_due', '<', fields.Date.context_today(self)),
                ('amount_residual', '!=', 0),
            ],
            'context': {'create': False},
        }
