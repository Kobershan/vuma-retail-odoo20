# -*- coding: utf-8 -*-
"""
Cross-Company Cash Collection.

A rep collects cash from a customer once; rows on the session are per-company
(Trade Rite / Siyaya DC / can hold both). Posting creates one real
``account.payment`` per row inside THAT company's cash journal, allocating
against any selected invoices of that company. No profile switch anywhere.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class VumaPaymentSession(models.Model):
    """A field cash-collection run that may cover both companies."""
    _name = 'vuma.payment.session'
    _description = 'Cross-Company Cash Collection'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(string='Reference', default='New', copy=False, readonly=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        domain=[('customer_rank', '>', 0)])
    date = fields.Date(
        string='Collection Date', default=fields.Date.context_today, required=True)
    user_id = fields.Many2one(
        'res.users', string='Collected By', default=lambda self: self.env.uid)
    note = fields.Text(string='Notes')
    state = fields.Selection([
        ('draft', 'Draft'), ('posted', 'Posted'), ('cancel', 'Cancelled'),
    ], default='draft', string='Status', tracking=True)

    line_ids = fields.One2many(
        'vuma.payment.session.line', 'session_id', string='Companies / Amounts')
    amount_total = fields.Monetary(
        string='Collected Total', compute='_compute_amount_total',
        currency_field='currency_tr_id', store=False)
    currency_tr_id = fields.Many2one(
        'res.currency', string='Currency',
        default=lambda self: self.env.company.currency_id)
    payment_ids = fields.Many2many(
        'account.payment', string='Posted Payments', readonly=True, copy=False)

    @api.depends('line_ids.amount')
    def _compute_amount_total(self):
        for session in self:
            session.amount_total = sum(session.line_ids.mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', 'New') == 'New':
                vals['name'] = self.env['ir.sequence'].sudo().next_by_code(
                    'vuma.payment.session') or 'New'
        return super().create(vals_list)

    def action_post(self):
        for session in self:
            if session.state != 'draft':
                continue
            if not session.line_ids or session.amount_total <= 0:
                raise UserError(_('Add at least one company row with an amount collected.'))
            # The active company scope is the boundary: a Siyaya DC amount may
            # only be posted while Siyaya DC is active, and a collection that
            # spans both companies needs both companies active.
            session.env.user._vuma_require_companies(
                session.line_ids.mapped('company_id'),
                _('post this cash collection'),
            )
            for line in session.line_ids:
                if line.amount <= 0:
                    continue
                ids = line._vuma_post_payment()
                payments = self.env['account.payment'].browse(ids).sudo()
                session.payment_ids |= payments
            session.state = 'posted'
            companies = {p.company_id.name for p in session.payment_ids}
            session.message_post(body=_(
                'Cash collection posted: %s payment(s) across companies: %s') % (
                len(session.payment_ids), ', '.join(sorted(companies))))
        return True

    def action_cancel(self):
        for session in self:
            session.payment_ids.filtered(lambda p: p.state != 'posted').unlink()
            session.state = 'cancel'
        return True

    def action_view_payments(self):
        self.ensure_one()
        return {
            'name': _('Posted Payments'),
            'type': 'ir.actions.act_window',
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.payment_ids.ids)],
            'target': 'current',
        }

    def action_open_credit_wizard(self):
        """From the same collection screen, jump to the crediting wizard."""
        self.ensure_one()
        self.env.user._vuma_require_both_trading_companies(
            _('credit invoices across both companies'))
        return {
            'name': _('Credit Invoices (Both Companies)'),
            'type': 'ir.actions.act_window',
            'res_model': 'vuma.partner.credit.wizard',
            'view_mode': 'form',
            'target': 'new',
            'context': {'default_partner_id': self.partner_id.id},
        }


class VumaPaymentSessionLine(models.Model):
    """Per-company collection row; one session can hold both companies."""
    _name = 'vuma.payment.session.line'
    _description = 'Cross-Company Cash Collection Line'
    _order = 'company_id, id'

    session_id = fields.Many2one(
        'vuma.payment.session', string='Session', required=True, ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company,
        help="Company this amount is collected for. It must be in the active "
             "company scope: switch to Siyaya DC to take a Siyaya DC payment, "
             "or to both companies to take a cross-company collection.")
    journal_id = fields.Many2one(
        'account.journal', string='Cash / Bank Journal',
        compute='_compute_journal', store=True, readonly=False)
    amount = fields.Monetary(
        string='Amount Collected', required=True, currency_field='currency_id')
    currency_id = fields.Many2one(
        related='company_id.currency_id', store=True, readonly=True)
    invoice_ids = fields.Many2many(
        'account.move', string='Invoices Settled (this company)',
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), "
               "('payment_state', 'in', ['not_paid', 'partial']), "
               "('company_id', '=', company_id)]", copy=False)
    invoices_amount_residual = fields.Float(
        string='Selected Invoices Due', compute='_compute_invoice_info', store=True)
    payment_ids_saved = fields.Text(copy=False)
    company_currency_id = fields.Many2one(
        related='company_id.currency_id', string='Company Currency')

    @api.depends('company_id')
    def _compute_journal(self):
        Journal = self.env['account.journal'].sudo()
        for line in self:
            company = line.company_id
            # Prefer the company's CASH journal (field collections), fall
            # back to any bank journal.
            journals = Journal.search([
                ('company_id', '=', company.id),
                ('type', '=', 'cash'),
            ], order='id asc', limit=1)
            if not journals:
                journals = Journal.search([
                    ('company_id', '=', company.id),
                    ('type', '=', 'bank'),
                ], order='id asc', limit=1)
            line.journal_id = journals

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._vuma_check_company_scope()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if {'company_id', 'amount', 'session_id'} & set(vals):
            self._vuma_check_company_scope()
        return res

    def _vuma_check_company_scope(self):
        """A row may only name a company the user is currently working in."""
        for line in self:
            if line.company_id:
                line.env.user._vuma_require_company(
                    line.company_id,
                    _('capture a payment for %(company)s',
                      company=line.company_id.display_name),
                )

    @api.depends('invoice_ids')
    def _compute_invoice_info(self):
        for line in self:
            line.invoices_amount_residual = sum(line.invoice_ids.mapped('amount_residual'))

    def _vuma_post_payment(self):
        """Create one inbound payment in this line's company.

        With invoices selected, drive the standard ``account.payment.register``
        wizard in the invoice's company context: it posts the payment and
        reconciles correctly (partial or full). Without invoices, post an open
        customer payment (advance / cash drop).

        :returns: list of payment ids created
        """
        self.ensure_one()
        company = self.company_id
        method_line = self._vuma_method_line(self.journal_id)
        created_ids = []

        if self.invoice_ids:
            invoices = self.invoice_ids.filtered(
                lambda m: m.company_id == company
                and m.payment_state in ['not_paid', 'partial'])
            if not invoices:
                self.payment_ids_saved = ''
                return []
            Register = self.env['account.payment.register'].with_company(
                company).sudo().with_context(
                active_model='account.move',
                active_ids=invoices.ids,
                vuma_mixed_basket=True,
            )
            wizard = Register.create({
                'amount': self.amount,
                'payment_date': self.session_id.date,
                'journal_id': self.journal_id.id,
                'payment_method_line_id': method_line.id if method_line else False,
                # Only in "edit" mode does the wizard honour `amount`. It
                # edits when the selected lines make a single batch AND
                # `group_payment` is set, and `group_payment` defaults to True
                # only when ONE invoice was picked. Left at its default, a row
                # settling two or more invoices falls back to one payment PER
                # INVOICE, each for that invoice's whole residual
                # (_create_payments -> _create_payment_vals_from_batch), so
                # what the rep actually collected is thrown away and the
                # customer is credited far more than they paid. Pinning it
                # keeps the cash honest: one payment of exactly the collected
                # amount, allocated oldest invoice first.
                'group_payment': True,
            })
            if not wizard.can_edit_wizard:
                # Several batches (mixed currencies/terms) cannot share one
                # payment. Refuse rather than silently post the full residual
                # of every invoice instead of the cash in hand.
                raise UserError(_(
                    'The invoices picked on this row do not share one payment '
                    'setup (for example mixed currencies), so the collected '
                    'cash cannot be allocated across them safely. Split the '
                    'row into one company/currency row per set of invoices.'))
            # Re-assert after create: `group_payment` is a stored computed
            # field, so recomputing `can_edit_wizard` can reset it.
            wizard.group_payment = True
            payments = wizard._create_payments()
            for pay in payments:
                pay.message_post(body=_('Linked to cross-company collection session %s')
                                 % self.session_id.name)
            created_ids = payments.ids
        else:
            payment = self.env['account.payment'].sudo().with_company(company).create({
                'payment_type': 'inbound',
                'partner_type': 'customer',
                'partner_id': self.session_id.partner_id.id,
                'amount': self.amount,
                'date': self.session_id.date,
                'journal_id': self.journal_id.id,
                'payment_method_line_id': method_line.id if method_line else False,
                'currency_id': company.currency_id.id,
                'company_id': company.id,
                'memo': _('Cross-company collection %s') % self.session_id.name,
            })
            payment.sudo().action_post()
            payment.message_post(body=_('Unallocated collection from session %s')
                                 % self.session_id.name)
            created_ids = [payment.id]
        self.payment_ids_saved = ','.join(str(i) for i in created_ids)
        return created_ids

    def _vuma_method_line(self, journal):
        if not journal:
            return False
        inbound = journal.sudo().inbound_payment_method_line_ids[:1]
        return inbound