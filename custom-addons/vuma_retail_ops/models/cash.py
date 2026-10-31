# -*- coding: utf-8 -*-
"""Cash office: receipts, trip floats, cash-up sessions and exceptions."""
from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .access_control import vuma_require
from .approvals import vuma_has_group


class ResCompanyCash(models.Model):
    _inherit = 'res.company'

    vuma_cash_clearing_account_id = fields.Many2one(
        'account.account', string='Cash Clearing Account',
        help='Account used when cash is handed in without a customer invoice.')
    vuma_stock_variance_threshold = fields.Monetary(
        string='Stock Variance Approval Threshold', default=1000.0,
        currency_field='currency_id')


class VumaCashReceipt(models.Model):
    _name = 'vuma.cash.receipt'
    _description = 'Cash Office Receipt'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'amount')

    name = fields.Char(
        string='Receipt', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Date', default=fields.Date.context_today, required=True,
        index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    receipt_type = fields.Selection(
        [('customer', 'Customer Payment'), ('rep_return', 'Rep Cash Return'),
         ('driver_return', 'Driver Cash Return'), ('other', 'Other')],
        string='Type', required=True, default='customer')
    partner_id = fields.Many2one(
        'res.partner', string='Customer', index=True,
        domain="[('customer_rank','>',0)]")
    employee_id = fields.Many2one(
        'res.users', string='Employee / Driver')
    amount = fields.Monetary(
        string='Amount', currency_field='currency_id', required=True)
    journal_id = fields.Many2one(
        'account.journal', string='Cash / Bank Journal', required=True,
        domain="[('type','in',('cash','bank')),('company_id','=',company_id)]")
    payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Payment Method',
        domain="[('journal_id','=',journal_id)]")
    invoice_ids = fields.Many2many(
        'account.move', 'vuma_cash_receipt_invoice_rel', 'receipt_id',
        'invoice_id', string='Invoices',
        domain="[('move_type','=','out_invoice'),('state','=','posted')]")
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, tracking=True)
    payment_id = fields.Many2one(
        'account.payment', string='Payment', readonly=True, copy=False)
    move_id = fields.Many2one(
        'account.move', string='Journal Entry', readonly=True, copy=False)
    remittance_id = fields.Many2one(
        'cognify.remittance', string='Remittance', index=True)
    float_id = fields.Many2one(
        'vuma.trip.float', string='Trip Float', index=True)
    received_by_id = fields.Many2one(
        'res.users', string='Received By', default=lambda self: self.env.user,
        required=True)
    reference = fields.Char(string='Reference')
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.cash.receipt') or _('New')
        return super().create(vals_list)

    def action_post(self):
        for receipt in self:
            if receipt.state != 'draft':
                raise UserError(_('This receipt is already posted.'))
            if receipt.amount <= 0:
                raise UserError(_('The amount must be positive.'))
            if receipt.receipt_type == 'customer' and receipt.invoice_ids:
                if not receipt.partner_id:
                    raise UserError(_('Choose the customer who paid.'))
                wizard = self.env['account.payment.register'].with_context(
                    active_model='account.move',
                    active_ids=receipt.invoice_ids.ids,
                ).create({
                    'payment_date': receipt.date,
                    'journal_id': receipt.journal_id.id,
                    'payment_method_line_id': receipt.payment_method_line_id.id or False,
                    'amount': receipt.amount,
                    'group_payment': True,
                })
                result = wizard.action_create_payments()
                payment = self.env['account.payment']
                if isinstance(result, dict) and result.get('res_id'):
                    payment = self.env['account.payment'].browse(result['res_id'])
                receipt.payment_id = payment[:1].id or False
                receipt.move_id = payment.move_id.id if payment else False
            else:
                if not receipt.partner_id:
                    clearing = receipt.company_id.vuma_cash_clearing_account_id \
                        or receipt.journal_id.suspense_account_id
                    if not clearing:
                        raise UserError(_(
                            'Configure a cash clearing account on the company '
                            'before posting a non-customer receipt.'))
                    move = self.env['account.move'].with_company(
                        receipt.company_id).create({
                        'move_type': 'entry',
                        'date': receipt.date,
                        'ref': receipt.name,
                        'company_id': receipt.company_id.id,
                        'line_ids': [
                            Command.create({
                                'name': receipt.name,
                                'account_id': receipt.journal_id.default_account_id.id,
                                'debit': receipt.amount,
                                'credit': 0.0,
                            }),
                            Command.create({
                                'name': receipt.name,
                                'account_id': clearing.id,
                                'debit': 0.0,
                                'credit': receipt.amount,
                            }),
                        ],
                    })
                    move.action_post()
                    receipt.move_id = move.id
                else:
                    payment = self.env['account.payment'].with_company(
                        receipt.company_id).create({
                        'payment_type': 'inbound',
                        'partner_type': 'customer',
                        'partner_id': receipt.partner_id.id,
                        'amount': receipt.amount,
                        'journal_id': receipt.journal_id.id,
                        'payment_method_line_id': receipt.payment_method_line_id.id or False,
                        'date': receipt.date,
                        'memo': receipt.name,
                    })
                    payment.action_post()
                    receipt.payment_id = payment.id
                    receipt.move_id = payment.move_id.id
            receipt.state = 'posted'
            receipt.message_post(body=_('Receipt posted: R %.2f', receipt.amount))
            self.env['vuma.audit.log'].log(receipt, 'post')
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled'})
        return True


class VumaTripFloat(models.Model):
    _name = 'vuma.trip.float'
    _description = 'Trip Float'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'amount_issued', 'amount_spent', 'amount_returned')

    name = fields.Char(
        string='Float', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Issued On', default=fields.Date.context_today, required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    employee_id = fields.Many2one(
        'res.users', string='Driver / Rep', required=True, index=True)
    trip_id = fields.Many2one(
        'vuma.delivery.trip', string='Trip', index=True)
    amount_issued = fields.Monetary(
        string='Issued', currency_field='currency_id', required=True)
    amount_spent = fields.Monetary(
        string='Spent', currency_field='currency_id')
    amount_returned = fields.Monetary(
        string='Returned', currency_field='currency_id')
    amount_outstanding = fields.Monetary(
        string='Outstanding', compute='_compute_outstanding', store=True,
        currency_field='currency_id')
    state = fields.Selection(
        [('draft', 'Draft'), ('issued', 'Issued'),
         ('reconciling', 'Reconciling'), ('closed', 'Closed'),
         ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, tracking=True)
    issued_by_id = fields.Many2one(
        'res.users', string='Issued By', readonly=True, copy=False)
    closed_date = fields.Datetime(string='Closed On', readonly=True, copy=False)
    cash_receipt_ids = fields.One2many(
        'vuma.cash.receipt', 'float_id', string='Cash Returns')
    notes = fields.Text(string='Notes')

    @api.depends('amount_issued', 'amount_spent', 'amount_returned')
    def _compute_outstanding(self):
        for rec in self:
            rec.amount_outstanding = (rec.amount_issued or 0.0) \
                - (rec.amount_spent or 0.0) - (rec.amount_returned or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.trip.float') or _('New')
        return super().create(vals_list)

    def action_issue(self):
        for rec in self:
            if rec.state != 'draft':
                raise UserError(_('This float has already been issued.'))
            rec.write({
                'state': 'issued',
                'issued_by_id': self.env.user.id,
            })
            self.env['vuma.audit.log'].log(rec, 'post', note='Float issued')
        return True

    def action_reconcile(self):
        for rec in self:
            if rec.trip_id:
                rec.amount_spent = sum(rec.trip_id.expense_ids.filtered(
                    lambda e: e.state == 'verified').mapped('amount'))
            rec.amount_returned = sum(rec.cash_receipt_ids.filtered(
                lambda r: r.state == 'posted').mapped('amount'))
            rec.state = 'reconciling'
        return True

    def action_close(self):
        vuma_require(self.env, 'action.close_cash',
                     _('Closing a trip float is a cash office action.'))
        for rec in self:
            rec.action_reconcile()
            if abs(rec.amount_outstanding) > 0.01:
                self.env['vuma.cash.exception'].create({
                    'float_id': rec.id,
                    'trip_id': rec.trip_id.id or False,
                    'exception_type': 'shortage' if rec.amount_outstanding > 0 else 'overage',
                    'amount': abs(rec.amount_outstanding),
                    'description': _('Float %s is out by R %.2f',
                                     rec.name, rec.amount_outstanding),
                    'company_id': rec.company_id.id,
                })
            rec.write({
                'state': 'closed',
                'closed_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(rec, 'post', note='Float closed')
        return True


class VumaCashSession(models.Model):
    _name = 'vuma.cash.session'
    _description = 'Cash Office Session'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'cash_counted', 'variance')

    name = fields.Char(
        string='Session', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Date', default=fields.Date.context_today, required=True,
        index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    cashier_id = fields.Many2one(
        'res.users', string='Cashier', default=lambda self: self.env.user,
        required=True)
    journal_id = fields.Many2one(
        'account.journal', string='Cash Journal', required=True,
        domain="[('type','=','cash'),('company_id','=',company_id)]")
    opening_float = fields.Monetary(
        string='Opening Float', currency_field='currency_id')
    receipts_total = fields.Monetary(
        string='Receipts', compute='_compute_totals', store=True,
        currency_field='currency_id')
    payments_total = fields.Monetary(
        string='Payments Out', compute='_compute_totals', store=True,
        currency_field='currency_id')
    expected_cash = fields.Monetary(
        string='Expected Cash', compute='_compute_totals', store=True,
        currency_field='currency_id')
    cash_counted = fields.Monetary(
        string='Cash Counted', compute='_compute_counted', store=True,
        readonly=False, currency_field='currency_id')
    bank_deposit = fields.Monetary(
        string='Banked', currency_field='currency_id')
    variance = fields.Monetary(
        string='Variance', compute='_compute_totals', store=True,
        currency_field='currency_id')
    state = fields.Selection(
        [('open', 'Open'), ('counted', 'Counted'), ('closed', 'Closed')],
        string='Status', default='open', required=True, tracking=True)
    line_ids = fields.One2many(
        'vuma.cash.session.line', 'session_id', string='Cash Count')
    notes = fields.Text(string='Notes')
    closed_by_id = fields.Many2one(
        'res.users', string='Closed By', readonly=True, copy=False)
    closed_date = fields.Datetime(string='Closed On', readonly=True, copy=False)

    @api.depends('journal_id', 'date', 'opening_float', 'line_ids.subtotal',
                 'cash_counted', 'bank_deposit')
    def _compute_totals(self):
        for session in self:
            payments = self.env['account.payment'].search([
                ('journal_id', '=', session.journal_id.id),
                ('date', '=', session.date),
                ('state', '=', 'posted'),
            ])
            session.receipts_total = sum(payments.filtered(
                lambda p: p.payment_type == 'inbound').mapped('amount'))
            session.payments_total = sum(payments.filtered(
                lambda p: p.payment_type == 'outbound').mapped('amount'))
            session.expected_cash = (session.opening_float or 0.0) \
                + session.receipts_total - session.payments_total
            session.variance = (session.cash_counted or 0.0) - session.expected_cash

    @api.depends('line_ids.subtotal')
    def _compute_counted(self):
        for session in self:
            if session.line_ids:
                session.cash_counted = sum(session.line_ids.mapped('subtotal'))
            elif not session.cash_counted:
                session.cash_counted = 0.0

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.cash.session') or _('New')
        return super().create(vals_list)

    def action_load_denominations(self):
        denominations = [
            ('R200 note', 200.0), ('R100 note', 100.0), ('R50 note', 50.0),
            ('R20 note', 20.0), ('R10 note', 10.0), ('R5 coin', 5.0),
            ('R2 coin', 2.0), ('R1 coin', 1.0), ('50c coin', 0.5),
            ('20c coin', 0.2), ('10c coin', 0.1),
        ]
        for session in self:
            session.line_ids.unlink()
            session.line_ids = [Command.create({
                'label': label, 'denomination': value, 'quantity': 0,
            }) for label, value in denominations]
        return True

    def action_count(self):
        self.write({'state': 'counted'})
        return True

    def action_close(self):
        vuma_require(self.env, 'action.close_cash',
                     _('Closing the cash session is a cash office action.'))
        for session in self:
            if session.state == 'closed':
                raise UserError(_('This session is already closed.'))
            if abs(session.variance) > 0.01:
                self.env['vuma.cash.exception'].create({
                    'session_id': session.id,
                    'exception_type': 'shortage' if session.variance < 0 else 'overage',
                    'amount': abs(session.variance),
                    'description': _('Cash-up variance on %s', session.name),
                    'company_id': session.company_id.id,
                })
            session.write({
                'state': 'closed',
                'closed_by_id': self.env.user.id,
                'closed_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(
                session, 'post', note='Variance %.2f' % session.variance)
        return True


class VumaCashSessionLine(models.Model):
    _name = 'vuma.cash.session.line'
    _description = 'Cash Count Line'
    _order = 'session_id, -denomination'

    session_id = fields.Many2one(
        'vuma.cash.session', required=True, ondelete='cascade', index=True)
    label = fields.Char(string='Denomination', required=True)
    denomination = fields.Monetary(
        string='Value', currency_field='currency_id', required=True)
    currency_id = fields.Many2one(
        related='session_id.currency_id', store=True, readonly=True)
    quantity = fields.Integer(string='Count', default=0)
    subtotal = fields.Monetary(
        string='Subtotal', compute='_compute_subtotal', store=True,
        currency_field='currency_id')

    @api.depends('denomination', 'quantity')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (line.denomination or 0.0) * (line.quantity or 0)


class VumaCashException(models.Model):
    _name = 'vuma.cash.exception'
    _description = 'Cash Exception'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'amount', 'resolution')

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Date', default=fields.Date.context_today, required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    exception_type = fields.Selection(
        [('shortage', 'Shortage'), ('overage', 'Overage'),
         ('missing_receipt', 'Missing Receipt'),
         ('late_remittance', 'Late Remittance'),
         ('unallocated', 'Unallocated Payment'), ('other', 'Other')],
        string='Type', required=True, default='shortage')
    amount = fields.Monetary(
        string='Amount', currency_field='currency_id', required=True)
    description = fields.Text(string='Description')
    session_id = fields.Many2one('vuma.cash.session', string='Cash Session')
    float_id = fields.Many2one('vuma.trip.float', string='Trip Float')
    trip_id = fields.Many2one('vuma.delivery.trip', string='Trip')
    remittance_id = fields.Many2one('cognify.remittance', string='Remittance')
    owner_id = fields.Many2one(
        'res.users', string='Owner', default=lambda self: self.env.user)
    state = fields.Selection(
        [('open', 'Open'), ('resolved', 'Resolved'), ('written_off', 'Written Off')],
        string='Status', default='open', required=True, tracking=True)
    resolution = fields.Text(string='Resolution')
    resolved_by_id = fields.Many2one(
        'res.users', string='Resolved By', readonly=True, copy=False)
    resolved_date = fields.Datetime(
        string='Resolved On', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.cash.exception') or _('New')
        return super().create(vals_list)

    def action_resolve(self):
        self.write({
            'state': 'resolved',
            'resolved_by_id': self.env.user.id,
            'resolved_date': fields.Datetime.now(),
        })
        return True

    def action_write_off(self):
        vuma_require(self.env, 'action.write_off',
                     _('Write-off is a finance manager decision.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Write-off is a finance manager decision.'))
        self.write({
            'state': 'written_off',
            'resolved_by_id': self.env.user.id,
            'resolved_date': fields.Datetime.now(),
        })
        self.env['vuma.audit.log'].log(self, 'override', note='Cash exception written off')
        return True


class CognifyRemittanceCash(models.Model):
    _inherit = 'cognify.remittance'

    trip_id = fields.Many2one('vuma.delivery.trip', string='Trip', index=True)
    float_id = fields.Many2one('vuma.trip.float', string='Trip Float', index=True)
    cash_receipt_id = fields.Many2one(
        'vuma.cash.receipt', string='Cash Office Receipt', index=True)
    cash_session_id = fields.Many2one(
        'vuma.cash.session', string='Cash Session', index=True)


class AccountPaymentCash(models.Model):
    _inherit = 'account.payment'

    vuma_cash_receipt_id = fields.Many2one(
        'vuma.cash.receipt', string='Cash Receipt', readonly=True, copy=False)
