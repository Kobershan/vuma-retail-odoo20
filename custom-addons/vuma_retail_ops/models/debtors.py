# -*- coding: utf-8 -*-
"""Debtor control: ratings, statements, letters, forms and collections."""
import logging
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .access_control import vuma_require
from .approvals import vuma_has_group

_logger = logging.getLogger(__name__)


class VumaDebtorRating(models.Model):
    _name = 'vuma.debtor.rating'
    _description = 'Debtor Rating'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'rating', 'override_rating')

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    date = fields.Date(
        string='Rating Date', default=fields.Date.context_today, required=True,
        index=True)
    date_from = fields.Date(string='Period From')
    date_to = fields.Date(string='Period To')
    state = fields.Selection(
        [('draft', 'Draft'), ('confirmed', 'Confirmed')],
        string='Status', default='draft', required=True, tracking=True)
    avg_days_late = fields.Float(string='Average Days Late', readonly=True)
    overdue_ratio = fields.Float(string='Overdue %', readonly=True)
    utilisation = fields.Float(string='Credit Utilisation %', readonly=True)
    failed_payment_ratio = fields.Float(
        string='Failed Payment %', readonly=True)
    return_dispute_ratio = fields.Float(
        string='Returns / Disputes %', readonly=True)
    score = fields.Float(string='Score', readonly=True)
    rating = fields.Selection(
        [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')],
        string='Rating', readonly=True)
    override_rating = fields.Selection(
        [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')],
        string='Override Rating')
    override_reason = fields.Text(string='Override Reason')
    override_by_id = fields.Many2one(
        'res.users', string='Overridden By', readonly=True, copy=False)
    notes = fields.Text(string='Notes')
    confirmed_by_id = fields.Many2one(
        'res.users', string='Confirmed By', readonly=True, copy=False)
    confirmed_date = fields.Datetime(
        string='Confirmed On', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.debtor.rating') or _('New')
        return super().create(vals_list)

    def action_compute(self):
        for rec in self:
            rec._compute_rating()
        return True

    def _compute_rating(self):
        self.ensure_one()
        date_to = self.date_to or fields.Date.context_today(self)
        date_from = self.date_from or fields.Date.subtract(date_to, months=12)
        partner_ids = [self.partner_id.id] + self.partner_id.child_ids.ids
        invoices = self.env['account.move'].search([
            ('partner_id', 'in', partner_ids),
            ('company_id', '=', self.company_id.id),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ])
        total_invoiced = sum(invoices.mapped('amount_total'))
        total_residual = sum(invoices.mapped('amount_residual'))
        overdue = sum(invoices.filtered(
            lambda i: i.invoice_date_due
            and i.invoice_date_due < fields.Date.context_today(self)
        ).mapped('amount_residual'))
        self.overdue_ratio = (overdue / total_residual * 100.0) \
            if total_residual else 0.0
        days_late = []
        for invoice in invoices.filtered(lambda i: i.amount_residual == 0):
            reconciles = invoice.matched_debit_ids | invoice.matched_credit_ids
            dates = [r.max_date for r in reconciles if r.max_date]
            if dates and invoice.invoice_date_due:
                late = (max(dates).date() - invoice.invoice_date_due).days
                days_late.append(max(0, late))
        self.avg_days_late = (sum(days_late) / len(days_late)) if days_late else 0.0
        limit = self.partner_id.vuma_group_credit_limit or self.partner_id.credit_limit
        exposure = self.partner_id.vuma_group_exposure or self.partner_id.credit
        self.utilisation = (exposure / limit * 100.0) if limit else 0.0
        payments = self.env['account.payment'].search([
            ('partner_id', 'in', partner_ids),
            ('company_id', '=', self.company_id.id),
            ('date', '>=', date_from),
            ('date', '<=', date_to),
        ])
        failed = payments.filtered(lambda p: p.state == 'cancel')
        self.failed_payment_ratio = (
            len(failed) / len(payments) * 100.0) if payments else 0.0
        credits = self.env['account.move'].search([
            ('partner_id', 'in', partner_ids),
            ('company_id', '=', self.company_id.id),
            ('move_type', '=', 'out_refund'),
            ('state', '=', 'posted'),
            ('invoice_date', '>=', date_from),
            ('invoice_date', '<=', date_to),
        ])
        credit_total = sum(credits.mapped('amount_total'))
        self.return_dispute_ratio = (credit_total / total_invoiced * 100.0) \
            if total_invoiced else 0.0
        penalty = (
            min(self.avg_days_late, 30.0) / 30.0 * 40.0
            + min(self.overdue_ratio, 100.0) / 100.0 * 25.0
            + min(self.utilisation, 100.0) / 100.0 * 15.0
            + min(self.failed_payment_ratio, 100.0) / 100.0 * 10.0
            + min(self.return_dispute_ratio, 100.0) / 100.0 * 10.0
        )
        self.score = max(0.0, min(100.0, 100.0 - penalty))
        if self.score >= 85:
            self.rating = 'a'
        elif self.score >= 70:
            self.rating = 'b'
        elif self.score >= 50:
            self.rating = 'c'
        else:
            self.rating = 'd'

    def action_confirm(self):
        for rec in self:
            if not vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_debtors',
                    'vuma_retail_ops.group_vuma_finance_manager',
                    'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
                raise AccessError(_('Only the debtors team may confirm a rating.'))
            if rec.override_rating and not rec.override_reason:
                raise UserError(_('Give a reason for the rating override.'))
            if rec.override_rating and not vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                    'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
                raise AccessError(_(
                    'Only the financial manager or owner may override a debtor '
                    'rating.'))
            rec.write({
                'state': 'confirmed',
                'confirmed_by_id': self.env.user.id,
                'confirmed_date': fields.Datetime.now(),
                'override_by_id': self.env.user.id if rec.override_rating else False,
            })
            self.env['vuma.audit.log'].log(rec, 'approve')
        return True

    @api.model
    def _cron_monthly_ratings(self):
        """Prepare one draft rating per active debtor at the start of a month.

        The debtors team reviews and confirms it.  Drafts are deliberately not
        confirmed automatically: the monthly review is a human control.
        """
        today = fields.Date.context_today(self)
        month_start = today.replace(day=1)
        partners = self.env['res.partner'].search([
            ('customer_rank', '>', 0),
            ('active', '=', True),
        ])
        for partner in partners:
            existing = self.search([
                ('partner_id', '=', partner.id),
                ('date', '>=', month_start),
            ], limit=1)
            if existing:
                continue
            rating = self.create({
                'partner_id': partner.id,
                'company_id': partner.company_id.id or self.env.company.id,
                'date': today,
            })
            rating.action_compute()
        return True


class VumaStatementWizard(models.TransientModel):
    _name = 'vuma.statement.wizard'
    _description = 'Debtor Statement Wizard'

    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        domain="[('customer_rank','>',0)]")
    date_from = fields.Date(
        string='From', required=True,
        default=lambda self: date(fields.Date.context_today(self).year, 1, 1))
    date_to = fields.Date(
        string='To', required=True, default=fields.Date.context_today)
    company_ids = fields.Many2many(
        'res.company', string='Companies', required=True,
        default=lambda self: self.env.companies.ids)
    include_zero = fields.Boolean(string='Include Zero Balances')
    only_overdue = fields.Boolean(string='Only Overdue Items')

    def _get_statement_lines(self):
        self.ensure_one()
        partner_ids = [self.partner_id.id] + self.partner_id.child_ids.ids
        domain = [
            ('partner_id', 'in', partner_ids),
            ('company_id', 'in', self.company_ids.ids),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('date', '<=', self.date_to),
        ]
        lines = self.env['account.move.line'].search(
            domain, order='company_id, date, id')
        opening = self.env['account.move.line'].search([
            ('partner_id', 'in', partner_ids),
            ('company_id', 'in', self.company_ids.ids),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('date', '<', self.date_from),
        ])
        opening_balance = sum(opening.mapped('balance'))
        rows = []
        balance = opening_balance
        for line in lines:
            if line.date < self.date_from:
                continue
            if self.only_overdue and (not line.date_maturity
                                      or line.date_maturity >= fields.Date.context_today(self)):
                continue
            balance += line.balance
            rows.append({
                'date': line.date,
                'reference': line.move_id.name,
                'description': line.name or line.move_id.ref or '',
                'due_date': line.date_maturity,
                'debit': line.balance if line.balance > 0 else 0.0,
                'credit': -line.balance if line.balance < 0 else 0.0,
                'balance': balance,
                'company': line.company_id.name,
            })
        return {
            'opening': opening_balance,
            'lines': rows,
            'closing': balance,
            'partner': self.partner_id,
            'company_names': ', '.join(self.company_ids.mapped('name')),
        }

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_debtor_statement').report_action(self)


class VumaDunningLetter(models.Model):
    _name = 'vuma.dunning.letter'
    _description = 'Debtor Letter'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    date = fields.Date(
        string='Letter Date', default=fields.Date.context_today, required=True)
    letter_type = fields.Selection(
        [('reminder', 'Payment Reminder'), ('final', 'Final Demand'),
         ('hold', 'Credit Hold Notice'), ('legal', 'Legal Handover'),
         ('ack', 'Payment Acknowledgement')],
        string='Letter Type', required=True, default='reminder')
    amount_overdue = fields.Monetary(
        string='Amount Overdue', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('sent', 'Sent'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, tracking=True)
    body = fields.Html(string='Letter Body')
    notes = fields.Text(string='Internal Notes')
    sent_date = fields.Datetime(string='Sent On', readonly=True, copy=False)
    sent_by_id = fields.Many2one(
        'res.users', string='Sent By', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.dunning.letter') or _('New')
        return super().create(vals_list)

    @api.onchange('partner_id', 'letter_type')
    def _onchange_partner(self):
        if self.partner_id:
            self.amount_overdue = self.partner_id.vuma_group_overdue
            self.body = self._default_body()

    def _default_body(self):
        label = dict(self._fields['letter_type'].selection).get(
            self.letter_type, 'Payment Reminder')
        return _(
            '<p>Dear %(name)s,</p>'
            '<p>Our records show an overdue balance of R %(amount).2f on your '
            'account.</p><p>This is a %(label)s. Please settle the amount or '
            'contact our debtors department to make arrangements.</p>'
            '<p>Kind regards,<br/>%(company)s</p>',
            name=self.partner_id.name or '',
            amount=self.amount_overdue or 0.0,
            label=label,
            company=self.company_id.name or '',
        )

    def action_generate_body(self):
        for letter in self:
            letter.body = letter._default_body()
        return True

    def action_mark_sent(self):
        for letter in self:
            letter.write({
                'state': 'sent',
                'sent_date': fields.Datetime.now(),
                'sent_by_id': self.env.user.id,
            })
            letter.partner_id.message_post(body=_(
                '%(type)s %(name)s sent to the customer.',
                type=dict(letter._fields['letter_type'].selection)[letter.letter_type],
                name=letter.name))
            self.env['vuma.audit.log'].log(letter, 'post', note='Letter marked sent')
        return True

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_dunning_letter').report_action(self)

    def _get_overdue_lines(self):
        self.ensure_one()
        partner_ids = [self.partner_id.id] + self.partner_id.child_ids.ids
        lines = self.env['account.move.line'].search([
            ('partner_id', 'in', partner_ids),
            ('company_id', '=', self.company_id.id),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('date_maturity', '<', fields.Date.context_today(self)),
            ('amount_residual', '!=', 0),
        ], order='date_maturity, id')
        return [{
            'reference': line.move_id.name,
            'date': line.date,
            'due_date': line.date_maturity,
            'amount': abs(line.amount_residual),
        } for line in lines]


class VumaDebtorForm(models.Model):
    _name = 'vuma.debtor.form'
    _description = 'Debtor Form'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    form_type = fields.Selection(
        [('credit_application', 'Credit Application'),
         ('surety', 'Surety'), ('debit_order', 'Debit Order Mandate'),
         ('bank_change', 'Change of Banking Details'),
         ('address_change', 'Change of Address'),
         ('popia', 'POPIA Consent')],
        string='Form Type', required=True, default='credit_application')
    date = fields.Date(
        string='Received On', default=fields.Date.context_today, required=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'),
         ('approved', 'Approved'), ('rejected', 'Rejected')],
        string='Status', default='draft', required=True, tracking=True)
    attachment = fields.Binary(string='Signed Form', attachment=True)
    attachment_filename = fields.Char(string='Filename')
    requested_by_id = fields.Many2one(
        'res.users', string='Captured By', default=lambda self: self.env.user)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    second_approved_by_id = fields.Many2one(
        'res.users', string='Second Approved By', readonly=True, copy=False)
    second_approved_date = fields.Datetime(
        string='Second Approved On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason')
    requested_credit_limit = fields.Monetary(
        string='Requested Credit Limit', currency_field='currency_id',
        help='The approved limit applies to the customer in Trade Rite and '
             'Siyaya DC together.')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    bank_name = fields.Char(string='Bank')
    bank_account = fields.Char(string='Account Number')
    branch_code = fields.Char(string='Branch Code')
    account_holder = fields.Char(string='Account Holder')
    requested_street = fields.Char(string='Street')
    requested_city = fields.Char(string='City')
    requested_zip = fields.Char(string='Postal Code')
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.debtor.form') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        for form in self:
            if not form.attachment:
                raise UserError(_(
                    'Attach the signed %s before submitting it.',
                    dict(form._fields['form_type'].selection).get(form.form_type)))
            if form.form_type == 'credit_application' \
                    and form.requested_credit_limit < 0:
                raise UserError(_('The requested credit limit cannot be negative.'))
        self.write({'state': 'submitted'})
        return True

    def _credit_roles_required(self):
        self.ensure_one()
        limit = self.requested_credit_limit or 0.0
        if limit <= 25000.0:
            return {'finance'}
        if limit <= 100000.0:
            return {'owner'}
        return {'owner', 'finance'}

    def _user_credit_roles(self, user):
        roles = set()
        if user.has_group('vuma_retail_ops.group_vuma_owner'):
            roles.add('owner')
        if user.has_group('vuma_retail_ops.group_vuma_finance_manager'):
            roles.add('finance')
        return roles

    def _apply_approved_form(self):
        self.ensure_one()
        if self.form_type == 'credit_application':
            self.partner_id.credit_limit = self.requested_credit_limit
        elif self.form_type == 'bank_change' and self.bank_account:
            bank = self.env['res.partner.bank'].search([
                ('acc_number', '=', self.bank_account),
                ('partner_id', '=', self.partner_id.id),
            ], limit=1)
            vals = {
                'acc_number': self.bank_account,
                'partner_id': self.partner_id.id,
                'acc_holder_name': self.account_holder or self.partner_id.name,
            }
            if self.bank_name:
                bank_rec = self.env['res.bank'].search(
                    [('name', 'ilike', self.bank_name)], limit=1)
                if bank_rec:
                    vals['bank_id'] = bank_rec.id
            if bank:
                bank.write(vals)
            else:
                self.env['res.partner.bank'].create(vals)
        elif self.form_type == 'address_change':
            self.partner_id.write({
                'street': self.requested_street,
                'city': self.requested_city,
                'zip': self.requested_zip,
            })
        elif self.form_type == 'popia':
            self.partner_id.write({
                'vuma_popia_consent': True,
                'vuma_popia_consent_date': fields.Date.context_today(self),
            })

    def action_approve(self):
        vuma_require(self.env, 'action.approve',
                     _('Debtor forms are approved by finance or the owner.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Debtor forms are approved by finance or the owner.'))
        for form in self:
            if form.state != 'submitted':
                raise UserError(_('Only a submitted form can be approved.'))
            required = (form._credit_roles_required()
                        if form.form_type == 'credit_application' else set())
            if required:
                user_roles = form._user_credit_roles(self.env.user)
                if not user_roles.intersection(required):
                    raise AccessError(_(
                        'Your role cannot approve a credit limit of R%.2f.',
                        form.requested_credit_limit))
                if len(required) > 1:
                    first = form.approved_by_id
                    if not first:
                        form.write({
                            'approved_by_id': self.env.user.id,
                            'approved_date': fields.Datetime.now(),
                        })
                        self.env['vuma.audit.log'].log(
                            form.partner_id, 'write',
                            note='First approval of debtor form %s' % form.name)
                        continue
                    if first == self.env.user:
                        raise UserError(_(
                            'The second approval must be by a different user.'))
                    combined = (form._user_credit_roles(first)
                                | form._user_credit_roles(self.env.user))
                    if not required.issubset(combined):
                        raise AccessError(_(
                            'This credit limit needs both the owner and the '
                            'financial manager to approve it.'))
                    form.second_approved_by_id = self.env.user.id
                    form.second_approved_date = fields.Datetime.now()
                elif not form.approved_by_id:
                    form.write({
                        'approved_by_id': self.env.user.id,
                        'approved_date': fields.Datetime.now(),
                    })
            elif not form.approved_by_id:
                form.write({
                    'approved_by_id': self.env.user.id,
                    'approved_date': fields.Datetime.now(),
                })
            form._apply_approved_form()
            form.state = 'approved'
            self.env['vuma.audit.log'].log(form.partner_id, 'write',
                                           note='Debtor form %s' % form.name)
        return True

    def action_reject(self):
        self.state = 'rejected'
        return True


class VumaCollectionTask(models.Model):
    _name = 'vuma.collection.task'
    _description = 'Collection Worklist Task'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'promised_date, id'
    _vuma_audit_fields = ('state', 'promised_date', 'promised_amount')

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    invoice_id = fields.Many2one(
        'account.move', string='Invoice', index=True,
        domain="[('move_type','=','out_invoice')]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    amount_due = fields.Monetary(
        string='Amount Due', currency_field='currency_id')
    promised_date = fields.Date(string='Promised Date')
    promised_amount = fields.Monetary(
        string='Promised Amount', currency_field='currency_id')
    owner_id = fields.Many2one(
        'res.users', string='Owner', default=lambda self: self.env.user)
    state = fields.Selection(
        [('open', 'Open'), ('promised', 'Promised'), ('kept', 'Promise Kept'),
         ('broken', 'Promise Broken'), ('cancelled', 'Cancelled')],
        string='Status', default='open', required=True, tracking=True)
    last_contact = fields.Datetime(string='Last Contact')
    next_action = fields.Char(string='Next Action')
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.collection.task') or _('New')
        return super().create(vals_list)

    @api.onchange('invoice_id')
    def _onchange_invoice(self):
        if self.invoice_id:
            self.partner_id = self.invoice_id.partner_id
            self.amount_due = self.invoice_id.amount_residual
            self.company_id = self.invoice_id.company_id

    def action_promise(self):
        self.write({
            'state': 'promised',
            'last_contact': fields.Datetime.now(),
        })
        return True

    def action_kept(self):
        self.write({'state': 'kept'})
        return True

    def action_broken(self):
        self.write({'state': 'broken'})
        return True

    def action_cancel(self):
        self.state = 'cancelled'
        return True

    @api.model
    def _cron_check_promises(self):
        today = fields.Date.context_today(self)
        tasks = self.search([
            ('state', '=', 'promised'),
            ('promised_date', '<', today),
        ])
        for task in tasks:
            task.state = 'broken'
            task.message_post(body=_('Promise to pay was not kept.'))
            task.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=today,
                summary=_('Broken promise: %s', task.partner_id.name),
                user_id=task.owner_id.id or self.env.user.id,
            )


class VumaCashflowForecast(models.Model):
    _name = 'vuma.cashflow.forecast'
    _description = '13-Week Cashflow Forecast'
    _auto = False
    _log_access = False
    _order = 'week_start, direction, source'

    week_start = fields.Date(string='Week Starting', readonly=True)
    direction = fields.Selection(
        [('in', 'Inflow'), ('out', 'Outflow')], string='Direction',
        readonly=True)
    source = fields.Char(string='Source', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Partner', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    amount = fields.Monetary(
        string='Amount', readonly=True, currency_field='currency_id')
    reference = fields.Char(string='Reference', readonly=True)
    due_date = fields.Date(string='Due Date', readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, 'vuma_cashflow_forecast')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW vuma_cashflow_forecast AS (
                WITH weeks AS (
                    SELECT generate_series(
                        date_trunc('week', CURRENT_DATE)::date,
                        (date_trunc('week', CURRENT_DATE) + interval '12 weeks')::date,
                        interval '1 week'
                    )::date AS week_start
                ),
                inflows AS (
                    SELECT w.week_start, 'in' AS direction, 'Customer receipts' AS source,
                           am.partner_id, am.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = am.company_id) AS currency_id,
                           SUM(am.amount_residual) AS amount,
                           MIN(am.name) AS reference,
                           MIN(am.invoice_date_due) AS due_date
                    FROM weeks w
                    JOIN account_move am ON am.invoice_date_due >= w.week_start
                         AND am.invoice_date_due < w.week_start + 7
                    WHERE am.move_type = 'out_invoice'
                      AND am.state = 'posted'
                      AND am.amount_residual > 0
                    GROUP BY w.week_start, am.partner_id, am.company_id
                ),
                outflows AS (
                    SELECT w.week_start, 'out' AS direction, 'Supplier payments' AS source,
                           am.partner_id, am.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = am.company_id) AS currency_id,
                           SUM(am.amount_residual) AS amount,
                           MIN(am.name) AS reference,
                           MIN(am.invoice_date_due) AS due_date
                    FROM weeks w
                    JOIN account_move am ON am.invoice_date_due >= w.week_start
                         AND am.invoice_date_due < w.week_start + 7
                    WHERE am.move_type = 'in_invoice'
                      AND am.state = 'posted'
                      AND am.amount_residual > 0
                    GROUP BY w.week_start, am.partner_id, am.company_id
                )
                SELECT row_number() OVER () AS id, q.*
                FROM (
                    SELECT * FROM inflows
                    UNION ALL
                    SELECT * FROM outflows
                ) q
            )
        """)


class VumaRepPerformance(models.Model):
    _name = 'vuma.rep.performance'
    _description = 'Sales Representative Performance'
    _auto = False
    _log_access = False
    _order = 'month desc, sales_amount desc'

    user_id = fields.Many2one('res.users', string='Sales Representative', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    month = fields.Date(string='Month', readonly=True)
    order_count = fields.Integer(string='Orders', readonly=True)
    customer_count = fields.Integer(string='Customers', readonly=True)
    sales_amount = fields.Monetary(
        string='Sales', readonly=True, currency_field='currency_id')
    invoice_amount = fields.Monetary(
        string='Invoiced', readonly=True, currency_field='currency_id')
    collected_amount = fields.Monetary(
        string='Collected', readonly=True, currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, 'vuma_rep_performance')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW vuma_rep_performance AS (
                SELECT
                    row_number() OVER () AS id,
                    so.user_id AS user_id,
                    so.company_id AS company_id,
                    date_trunc('month', so.date_order)::date AS month,
                    COUNT(DISTINCT so.id) AS order_count,
                    COUNT(DISTINCT so.partner_id) AS customer_count,
                    SUM(so.amount_total) AS sales_amount,
                    (SELECT c.currency_id FROM res_company c WHERE c.id = so.company_id) AS currency_id,
                    SUM(inv.invoice_amount) AS invoice_amount,
                    SUM(inv.collected_amount) AS collected_amount
                FROM sale_order so
                LEFT JOIN LATERAL (
                    SELECT COALESCE(SUM(am.amount_total), 0) AS invoice_amount,
                           COALESCE(SUM(am.amount_total - am.amount_residual), 0) AS collected_amount
                      FROM account_move am
                     WHERE am.move_type = 'out_invoice' AND am.state = 'posted'
                       AND am.id IN (
                            SELECT aml.move_id
                              FROM account_move_line aml
                              JOIN sale_order_line_invoice_rel soli ON soli.invoice_line_id = aml.id
                              JOIN sale_order_line sol ON sol.id = soli.order_line_id
                             WHERE sol.order_id = so.id
                       )
                ) inv ON TRUE
                WHERE so.state = 'sale'
                GROUP BY so.user_id, so.company_id, date_trunc('month', so.date_order)
            )
        """)


class ResPartnerStatementSchedule(models.Model):
    _inherit = 'res.partner'

    vuma_statement_auto = fields.Boolean(
        string='Send Statements Automatically', default=True,
        help="Email a consolidated statement on the next statement date when "
             "the account has a balance.")
    vuma_statement_next_date = fields.Date(
        string='Next Statement Date', default=lambda self: self._default_statement_date(),
        help="The next date on which an automatic statement is due. The "
             "statement covers the previous calendar month. If the chosen day "
             "does not exist in a month, the last day of that month is used.")
    vuma_statement_last_sent = fields.Date(
        string='Last Statement Sent', readonly=True, copy=False)

    @api.model
    def _default_statement_date(self):
        today = fields.Date.context_today(self)
        return today.replace(day=1) + relativedelta(months=1)

    @api.model
    def _vuma_statement_period(self, today=None):
        """The period covered by a statement sent on ``today``.

        Statements always report the previous complete calendar month, which
        matches the monthly statement the debtors team already sends.
        """
        today = today or fields.Date.context_today(self)
        date_to = today.replace(day=1) - timedelta(days=1)
        date_from = date_to.replace(day=1)
        return date_from, date_to

    def _vuma_next_statement_date(self, current=None):
        self.ensure_one()
        current = current or fields.Date.context_today(self)
        return current + relativedelta(months=1)

    def _vuma_has_statement_balance(self, date_to):
        self.ensure_one()
        partner_ids = [self.id] + self.child_ids.ids
        lines = self.env['account.move.line'].search([
            ('partner_id', 'in', partner_ids),
            ('account_id.account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('date', '<=', date_to),
        ])
        return sum(lines.mapped('balance')) > 0.005

    def _vuma_render_statement(self, date_from, date_to):
        """Build the PDF statement and attach it to the customer."""
        self.ensure_one()
        companies = self.env['res.company'].search([])
        report = self.env.ref(
            'vuma_retail_ops.action_report_debtor_statement')
        wizard = self.env['vuma.statement.wizard'].create({
            'partner_id': self.id,
            'date_from': date_from,
            'date_to': date_to,
            'company_ids': [Command.set(companies.ids)],
        })
        pdf, _report_type = report._render_qweb_pdf(
            'vuma_retail_ops.action_report_debtor_statement', wizard.ids)
        return self.env['ir.attachment'].create({
            'name': 'Statement - %s - %s.pdf' % (self.name, date_to),
            # Odoo 20 ignores the old ``datas`` alias; ``raw`` stores the PDF.
            'raw': pdf,
            'mimetype': 'application/pdf',
            'res_model': 'res.partner',
            'res_id': self.id,
        })

    def _vuma_send_statement(self, date_from, date_to, force=False):
        """Email one statement. Returns True when a mail was queued."""
        self.ensure_one()
        if not force and not self._vuma_has_statement_balance(date_to):
            return False
        if not self.email:
            self.env['vuma.notification'].notify(
                self,
                ['vuma_retail_ops.group_vuma_debtors',
                 'vuma_retail_ops.group_vuma_finance_manager'],
                _('Statement ready for customer without email'),
                _('%(partner)s has no email address. Statement to %(date)s '
                  'cannot be emailed.', partner=self.name, date=date_to),
            )
            return False
        attachment = self._vuma_render_statement(date_from, date_to)
        self.env['mail.mail'].sudo().create({
            'subject': 'Vuma statement — %s' % date_to,
            'body_html': (
                '<p>Dear %s,</p><p>Attached is your consolidated '
                'statement to %s.</p><p>Kind regards,<br/>Vuma Retail</p>'
                % (self.name, date_to)),
            'email_to': self.email,
            'attachment_ids': [Command.link(attachment.id)],
            'model': 'res.partner',
            'res_id': self.id,
        })
        self.message_post(body=_(
            'Statement for %(date_from)s to %(date_to)s emailed to %(email)s.',
            date_from=date_from, date_to=date_to, email=self.email))
        return True

    def action_vuma_send_statement(self):
        """Send the previous month's statement now, regardless of balance."""
        today = fields.Date.context_today(self)
        date_from, date_to = self._vuma_statement_period(today)
        sent = 0
        for partner in self:
            if partner._vuma_send_statement(date_from, date_to, force=True):
                partner.vuma_statement_last_sent = today
                sent += 1
        if not sent:
            raise UserError(_(
                'The statement could not be sent. Check that the customer '
                'has an email address.'))
        return True

    @api.model
    def _cron_send_due_statements(self):
        """Send statements whose next statement date is today or earlier."""
        today = fields.Date.context_today(self)
        date_from, date_to = self._vuma_statement_period(today)
        partners = self.search([
            ('active', '=', True),
            ('customer_rank', '>', 0),
            ('vuma_statement_auto', '=', True),
            ('vuma_statement_next_date', '<=', today),
        ])
        sent = 0
        for partner in partners:
            try:
                if partner._vuma_send_statement(date_from, date_to):
                    partner.vuma_statement_last_sent = today
                    sent += 1
                # Advance even when the account has no balance or no email:
                # otherwise the same customer would be retried every day and
                # the debtors team would receive a daily notification.
                partner.vuma_statement_next_date = \
                    partner._vuma_next_statement_date(
                        partner.vuma_statement_next_date or today)
            except Exception:
                # Leave the date in place so a temporary PDF or mail problem
                # is retried on the next cron run.
                _logger.exception(
                    'Could not send statement to customer %s (%s)',
                    partner.display_name, partner.id)
        return sent

    @api.model
    def _cron_monthly_statements(self):
        """Backward-compatible entry point for the old monthly cron."""
        return self._cron_send_due_statements()
