# -*- coding: utf-8 -*-
"""Accounting support: fixed assets, budgets, tax pack and exception list."""
import base64
import csv
import io
from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .approvals import vuma_has_group


class VumaFixedAsset(models.Model):
    _name = 'vuma.fixed.asset'
    _description = 'Fixed Asset'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'purchase_date desc, id desc'
    _vuma_audit_fields = ('state', 'cost', 'useful_life_months')

    name = fields.Char(string='Asset', required=True, tracking=True)
    code = fields.Char(string='Asset Code', index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    asset_type = fields.Selection(
        [('vehicle', 'Vehicle'), ('fridge', 'Fridge / Cooler'),
         ('racking', 'Racking / Shelving'), ('it', 'IT Equipment'),
         ('office', 'Office Equipment'), ('other', 'Other')],
        string='Type', required=True, default='other')
    purchase_date = fields.Date(
        string='Purchase Date', default=fields.Date.context_today, required=True)
    cost = fields.Monetary(
        string='Cost', currency_field='currency_id', required=True)
    residual_value = fields.Monetary(
        string='Residual Value', currency_field='currency_id')
    useful_life_months = fields.Integer(
        string='Useful Life (months)', default=60, required=True)
    monthly_depreciation = fields.Monetary(
        string='Monthly Depreciation', compute='_compute_monthly',
        store=True, currency_field='currency_id')
    book_value = fields.Monetary(
        string='Book Value', compute='_compute_book_value',
        currency_field='currency_id')
    state = fields.Selection(
        [('draft', 'Draft'), ('running', 'Running'),
         ('disposed', 'Disposed'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, tracking=True)
    supplier_id = fields.Many2one(
        'res.partner', string='Supplier',
        domain="[('supplier_rank', '>', 0)]")
    invoice_id = fields.Many2one('account.move', string='Purchase Invoice')
    location = fields.Char(string='Location')
    serial = fields.Char(string='Serial Number')
    journal_id = fields.Many2one(
        'account.journal', string='Journal', required=True,
        domain="[('type','=','general'),('company_id','=',company_id)]")
    asset_account_id = fields.Many2one(
        'account.account', string='Asset Account', required=True,
        domain="[('account_type','=','asset_fixed')]")
    accumulated_account_id = fields.Many2one(
        'account.account', string='Accumulated Depreciation Account',
        required=True,
        domain="[('account_type','=','asset_fixed')]")
    expense_account_id = fields.Many2one(
        'account.account', string='Depreciation Expense Account', required=True,
        domain="[('account_type','=','expense')]")
    notes = fields.Text(string='Notes')
    depreciation_line_ids = fields.One2many(
        'vuma.depreciation.line', 'asset_id', string='Depreciation Schedule',
        copy=False)
    posted_amount = fields.Monetary(
        string='Depreciated', compute='_compute_book_value',
        currency_field='currency_id')

    @api.depends('cost', 'residual_value', 'useful_life_months')
    def _compute_monthly(self):
        for asset in self:
            months = asset.useful_life_months or 0
            asset.monthly_depreciation = (
                ((asset.cost or 0.0) - (asset.residual_value or 0.0)) / months
            ) if months > 0 else 0.0

    @api.depends('cost', 'depreciation_line_ids.amount',
                 'depreciation_line_ids.state')
    def _compute_book_value(self):
        for asset in self:
            posted = sum(asset.depreciation_line_ids.filtered(
                lambda l: l.state == 'posted').mapped('amount'))
            asset.posted_amount = posted
            asset.book_value = (asset.cost or 0.0) - posted

    @api.constrains('cost', 'residual_value', 'useful_life_months')
    def _check_values(self):
        for asset in self:
            if asset.cost < 0 or asset.residual_value < 0:
                raise ValidationError(_('Asset values cannot be negative.'))
            if asset.residual_value > asset.cost:
                raise ValidationError(_('Residual value cannot exceed cost.'))
            if asset.useful_life_months <= 0:
                raise ValidationError(_('Useful life must be positive.'))

    def action_activate(self):
        for asset in self:
            if asset.state != 'draft':
                raise UserError(_('Only a draft asset can be activated.'))
            asset.depreciation_line_ids.unlink()
            months = asset.useful_life_months
            amount = asset.monthly_depreciation
            accumulated = 0.0
            lines = []
            for month in range(1, months + 1):
                accumulated += amount
                book_value = asset.cost - accumulated
                if month == months:
                    book_value = asset.residual_value
                lines.append(Command.create({
                    'date': asset.purchase_date + relativedelta(months=month),
                    'amount': amount,
                    'accumulated': accumulated,
                    'book_value': book_value,
                }))
            asset.write({
                'depreciation_line_ids': lines,
                'state': 'running',
            })
            self.env['vuma.audit.log'].log(asset, 'approve', note='Asset activated')
        return True

    def action_post_depreciation(self):
        for asset in self:
            if asset.state != 'running':
                raise UserError(_('Only a running asset can be depreciated.'))
            lines = asset.depreciation_line_ids.filtered(
                lambda l: l.state == 'draft'
                and l.date <= fields.Date.context_today(self))
            for line in lines:
                line.action_post()
        return True

    def action_dispose(self):
        self.write({'state': 'disposed'})
        self.env['vuma.audit.log'].log(self, 'override', note='Asset disposed')
        return True


class VumaDepreciationLine(models.Model):
    _name = 'vuma.depreciation.line'
    _description = 'Depreciation Line'
    _order = 'asset_id, date'

    asset_id = fields.Many2one(
        'vuma.fixed.asset', required=True, ondelete='cascade', index=True)
    date = fields.Date(string='Date', required=True)
    amount = fields.Monetary(
        string='Depreciation', currency_field='currency_id')
    accumulated = fields.Monetary(
        string='Accumulated', currency_field='currency_id')
    book_value = fields.Monetary(
        string='Book Value', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='asset_id.currency_id', store=True, readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted')],
        string='Status', default='draft', required=True)
    move_id = fields.Many2one(
        'account.move', string='Journal Entry', readonly=True, copy=False)

    def action_post(self):
        for line in self:
            if line.state == 'posted':
                continue
            asset = line.asset_id
            move = self.env['account.move'].with_company(asset.company_id).create({
                'move_type': 'entry',
                'date': line.date,
                'ref': _('Depreciation %(asset)s %(date)s',
                         asset=asset.name, date=line.date),
                'company_id': asset.company_id.id,
                'journal_id': asset.journal_id.id,
                'line_ids': [
                    Command.create({
                        'name': _('Depreciation %s', asset.name),
                        'account_id': asset.expense_account_id.id,
                        'debit': line.amount,
                        'credit': 0.0,
                    }),
                    Command.create({
                        'name': _('Accumulated depreciation %s', asset.name),
                        'account_id': asset.accumulated_account_id.id,
                        'debit': 0.0,
                        'credit': line.amount,
                    }),
                ],
            })
            move.action_post()
            line.write({'state': 'posted', 'move_id': move.id})
        return True


class VumaBudget(models.Model):
    _name = 'vuma.budget'
    _description = 'Budget'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date_from desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(string='Budget', required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('active', 'Active'), ('closed', 'Closed')],
        string='Status', default='draft', required=True, tracking=True)
    line_ids = fields.One2many(
        'vuma.budget.line', 'budget_id', string='Budget Lines', copy=True)
    total_budget = fields.Monetary(
        string='Budget', compute='_compute_totals', store=True,
        currency_field='currency_id')
    total_actual = fields.Monetary(
        string='Actual', compute='_compute_totals', store=True,
        currency_field='currency_id')
    variance = fields.Monetary(
        string='Variance', compute='_compute_totals', store=True,
        currency_field='currency_id')
    notes = fields.Text(string='Notes')

    @api.depends('line_ids.budget_amount', 'line_ids.actual_amount')
    def _compute_totals(self):
        for budget in self:
            budget.total_budget = sum(budget.line_ids.mapped('budget_amount'))
            budget.total_actual = sum(budget.line_ids.mapped('actual_amount'))
            budget.variance = budget.total_budget - budget.total_actual

    def action_activate(self):
        self.write({'state': 'active'})
        return True

    def action_refresh(self):
        self.line_ids._compute_actual()
        return True

    def action_close(self):
        self.write({'state': 'closed'})
        return True


class VumaBudgetLine(models.Model):
    _name = 'vuma.budget.line'
    _description = 'Budget Line'
    _order = 'budget_id, account_id'

    budget_id = fields.Many2one(
        'vuma.budget', required=True, ondelete='cascade', index=True)
    account_id = fields.Many2one(
        'account.account', string='Account', required=True,
        domain="[('account_type','in',('income','expense','income_other','expense_direct_cost'))]")
    description = fields.Char(string='Description')
    town = fields.Char(string='Town')
    vehicle = fields.Char(string='Vehicle')
    budget_amount = fields.Monetary(
        string='Budget', currency_field='currency_id')
    actual_amount = fields.Monetary(
        string='Actual', compute='_compute_actual', store=True,
        currency_field='currency_id')
    variance = fields.Monetary(
        string='Variance', compute='_compute_actual', store=True,
        currency_field='currency_id')
    currency_id = fields.Many2one(
        related='budget_id.currency_id', store=True, readonly=True)

    @api.depends('budget_id.date_from', 'budget_id.date_to', 'account_id',
                 'budget_amount', 'town')
    def _compute_actual(self):
        for line in self:
            if not (line.budget_id.date_from and line.budget_id.date_to
                    and line.account_id):
                line.actual_amount = 0.0
                line.variance = line.budget_amount
                continue
            domain = [
                ('account_id', '=', line.account_id.id),
                ('company_id', '=', line.budget_id.company_id.id),
                ('parent_state', '=', 'posted'),
                ('date', '>=', line.budget_id.date_from),
                ('date', '<=', line.budget_id.date_to),
            ]
            if line.town:
                domain.append(('partner_id.city', 'ilike', line.town))
            lines = self.env['account.move.line'].search(domain)
            line.actual_amount = sum(lines.mapped('balance'))
            line.variance = line.budget_amount - line.actual_amount


class VumaTaxPack(models.TransientModel):
    _name = 'vuma.tax.pack'
    _description = 'VAT Pack'

    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    line_ids = fields.One2many(
        'vuma.tax.pack.line', 'pack_id', string='Tax Lines')
    output_base = fields.Monetary(
        string='Output Base', currency_field='currency_id')
    output_tax = fields.Monetary(
        string='Output VAT', currency_field='currency_id')
    input_base = fields.Monetary(
        string='Input Base', currency_field='currency_id')
    input_tax = fields.Monetary(
        string='Input VAT', currency_field='currency_id')
    net_due = fields.Monetary(
        string='Net VAT Due', compute='_compute_net', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)

    @api.depends('output_tax', 'input_tax')
    def _compute_net(self):
        for pack in self:
            pack.net_due = (pack.output_tax or 0.0) - (pack.input_tax or 0.0)

    def action_compute(self):
        self.ensure_one()
        self.line_ids.unlink()
        taxes = {}
        lines = self.env['account.move.line'].search([
            ('move_id.company_id', '=', self.company_id.id),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', self.date_from),
            ('move_id.invoice_date', '<=', self.date_to),
            ('tax_line_id', '!=', False),
        ])
        output_base = output_tax = input_base = input_tax = 0.0
        for line in lines:
            move_type = line.move_id.move_type
            if move_type not in ('out_invoice', 'out_refund',
                                 'in_invoice', 'in_refund'):
                continue
            direction = 'output' if move_type in ('out_invoice', 'out_refund') \
                else 'input'
            key = (direction, line.tax_line_id)
            entry = taxes.setdefault(key, {'base': 0.0, 'tax': 0.0})
            entry['base'] += line.tax_base_amount or 0.0
            entry['tax'] += line.balance
        for (direction, tax), values in taxes.items():
            self.env['vuma.tax.pack.line'].create({
                'pack_id': self.id,
                'direction': direction,
                'tax_id': tax.id,
                'name': tax.name,
                'base_amount': values['base'],
                'tax_amount': values['tax'],
            })
            if direction == 'output':
                output_base += values['base']
                output_tax += values['tax']
            else:
                input_base += values['base']
                input_tax += values['tax']
        self.write({
            'output_base': output_base,
            'output_tax': output_tax,
            'input_base': input_base,
            'input_tax': input_tax,
        })
        return True

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_tax_pack').report_action(self)


class VumaTaxPackLine(models.TransientModel):
    _name = 'vuma.tax.pack.line'
    _description = 'VAT Pack Line'

    pack_id = fields.Many2one('vuma.tax.pack', required=True, ondelete='cascade')
    direction = fields.Selection(
        [('output', 'Output'), ('input', 'Input')], required=True)
    tax_id = fields.Many2one('account.tax', string='Tax')
    name = fields.Char(string='Tax Name')
    base_amount = fields.Monetary(
        string='Base', currency_field='currency_id')
    tax_amount = fields.Monetary(
        string='VAT', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='pack_id.currency_id', readonly=True)



class VumaTaxPeriod(models.Model):
    _name = 'vuma.tax.period'
    _description = 'VAT Period'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date_from desc, id desc'
    _vuma_audit_fields = ('state', 'lock_date')

    name = fields.Char(
        string='Period', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company, index=True)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True, index=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('prepared', 'Prepared'),
         ('reviewed', 'Reviewed'), ('submitted', 'Submitted')],
        string='Status', default='draft', required=True, tracking=True)
    line_ids = fields.One2many(
        'vuma.tax.period.line', 'period_id', string='Tax Lines', copy=True)
    output_base = fields.Monetary(
        string='Output Base', currency_field='currency_id')
    output_tax = fields.Monetary(
        string='Output VAT', currency_field='currency_id')
    input_base = fields.Monetary(
        string='Input Base', currency_field='currency_id')
    input_tax = fields.Monetary(
        string='Input VAT', currency_field='currency_id')
    net_due = fields.Monetary(
        string='Net VAT Due', compute='_compute_net',
        currency_field='currency_id', store=True)
    prepared_by_id = fields.Many2one(
        'res.users', string='Prepared By', readonly=True, copy=False)
    prepared_date = fields.Datetime(
        string='Prepared On', readonly=True, copy=False)
    reviewed_by_id = fields.Many2one(
        'res.users', string='Reviewed By', readonly=True, copy=False)
    reviewed_date = fields.Datetime(
        string='Reviewed On', readonly=True, copy=False)
    submitted_by_id = fields.Many2one(
        'res.users', string='Submitted By', readonly=True, copy=False)
    submitted_date = fields.Datetime(
        string='Submitted On', readonly=True, copy=False)
    lock_date = fields.Date(
        string='Lock Date', readonly=True, copy=False,
        help='Company tax lock date set when this return was submitted.')
    review_notes = fields.Text(string='Review Notes')

    @api.depends('output_tax', 'input_tax')
    def _compute_net(self):
        for period in self:
            period.net_due = (period.output_tax or 0.0) - (period.input_tax or 0.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New') and vals.get('date_from'):
                vals['name'] = 'VAT %s' % str(vals['date_from'])[:7]
        return super().create(vals_list)

    def action_compute(self):
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('VAT packs are prepared by finance.'))
        for period in self:
            if period.state == 'submitted':
                raise UserError(_('A submitted VAT period is locked.'))
            period.line_ids.unlink()
            lines = self.env['account.move.line'].search([
                ('move_id.company_id', '=', period.company_id.id),
                ('move_id.state', '=', 'posted'),
                ('move_id.invoice_date', '>=', period.date_from),
                ('move_id.invoice_date', '<=', period.date_to),
                ('tax_line_id', '!=', False),
            ])
            taxes = {}
            for line in lines:
                move_type = line.move_id.move_type
                if move_type not in ('out_invoice', 'out_refund',
                                     'in_invoice', 'in_refund'):
                    continue
                direction = 'output' if move_type in (
                    'out_invoice', 'out_refund') else 'input'
                key = (direction, line.tax_line_id)
                entry = taxes.setdefault(key, {'base': 0.0, 'tax': 0.0})
                entry['base'] += line.tax_base_amount or 0.0
                entry['tax'] += line.balance
            output_base = output_tax = input_base = input_tax = 0.0
            for (direction, tax), values in taxes.items():
                self.env['vuma.tax.period.line'].create({
                    'period_id': period.id,
                    'direction': direction,
                    'tax_id': tax.id,
                    'name': tax.name,
                    'base_amount': values['base'],
                    'tax_amount': values['tax'],
                })
                if direction == 'output':
                    output_base += values['base']
                    output_tax += values['tax']
                else:
                    input_base += values['base']
                    input_tax += values['tax']
            period.write({
                'output_base': output_base,
                'output_tax': output_tax,
                'input_base': input_base,
                'input_tax': input_tax,
                'state': 'prepared',
                'prepared_by_id': self.env.user.id,
                'prepared_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(period, 'approve', note='VAT prepared')
        return True

    def action_review(self):
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('VAT packs are reviewed by finance or the owner.'))
        for period in self:
            if period.state != 'prepared':
                raise UserError(_('Only a prepared VAT period can be reviewed.'))
            if period.prepared_by_id == self.env.user:
                raise UserError(_(
                    'The preparer cannot review their own VAT period.'))
            period.write({
                'state': 'reviewed',
                'reviewed_by_id': self.env.user.id,
                'reviewed_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(period, 'approve', note='VAT reviewed')
        return True

    def action_submit(self):
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('VAT returns are submitted by finance or the owner.'))
        for period in self:
            if period.state != 'reviewed':
                raise UserError(_('Review the VAT period before submitting it.'))
            if period.reviewed_by_id == period.prepared_by_id:
                raise UserError(_(
                    'The preparer and reviewer must be different people.'))
            period.write({
                'state': 'submitted',
                'submitted_by_id': self.env.user.id,
                'submitted_date': fields.Datetime.now(),
                'lock_date': period.date_to,
            })
            period.company_id.sudo().tax_lock_date = period.date_to
            self.env['vuma.audit.log'].log(
                period, 'post', note='VAT submitted; period locked')
        return True

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_tax_period').report_action(self)


class VumaTaxPeriodLine(models.Model):
    _name = 'vuma.tax.period.line'
    _description = 'VAT Period Line'

    period_id = fields.Many2one(
        'vuma.tax.period', required=True, ondelete='cascade', index=True)
    direction = fields.Selection(
        [('output', 'Output'), ('input', 'Input')], required=True)
    tax_id = fields.Many2one('account.tax', string='Tax')
    name = fields.Char(string='Tax Name')
    base_amount = fields.Monetary(
        string='Base', currency_field='currency_id')
    tax_amount = fields.Monetary(
        string='VAT', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='period_id.currency_id', readonly=True)



class VumaException(models.Model):
    _name = 'vuma.exception'
    _description = 'Operations Exception'
    _auto = False
    _log_access = False
    _order = 'date desc, amount desc'

    type = fields.Selection(
        [('overdue', 'Overdue Invoice'), ('pod', 'POD Missing'),
         ('claim', 'Supplier Claim'), ('cash', 'Cash Exception'),
         ('credit', 'Credit Hold'), ('promise', 'Broken Promise')],
        string='Type', readonly=True)
    name = fields.Char(string='Exception', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Partner', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True)
    amount = fields.Monetary(
        string='Amount', readonly=True, currency_field='currency_id')
    date = fields.Date(string='Date', readonly=True)
    reference = fields.Char(string='Reference', readonly=True)
    source_model = fields.Char(string='Source Model', readonly=True)
    source_id = fields.Integer(string='Source ID', readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, 'vuma_exception')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW vuma_exception AS (
                SELECT row_number() OVER () AS id, q.* FROM (
                    SELECT 'overdue' AS type,
                           'Overdue invoice ' || am.name AS name,
                           am.partner_id, am.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = am.company_id) AS currency_id,
                           am.amount_residual AS amount,
                           am.invoice_date_due AS date,
                           am.name AS reference,
                           'account.move' AS source_model, am.id AS source_id
                    FROM account_move am
                    WHERE am.move_type = 'out_invoice' AND am.state = 'posted'
                      AND am.amount_residual > 0
                      AND am.invoice_date_due < CURRENT_DATE
                    UNION ALL
                    SELECT 'pod', 'POD missing for ' || sp.name,
                           sp.partner_id, sp.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = sp.company_id),
                           0, DATE(sp.date_done), sp.name, 'stock.picking', sp.id
                    FROM stock_picking sp
                    JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                    WHERE spt.code = 'outgoing'
                      AND sp.state = 'done'
                      AND sp.vuma_pod_state = 'pending'
                    UNION ALL
                    SELECT 'claim', 'Supplier claim ' || sc.name,
                           sc.supplier_id, sc.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = sc.company_id),
                           sc.claimed_amount, sc.date, sc.name,
                           'vuma.supplier.claim', sc.id
                    FROM vuma_supplier_claim sc
                    WHERE sc.state IN ('submitted', 'approved')
                    UNION ALL
                    SELECT 'cash', 'Cash exception ' || ce.name,
                           NULL, ce.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = ce.company_id),
                           ce.amount, ce.date, ce.name,
                           'vuma.cash.exception', ce.id
                    FROM vuma_cash_exception ce
                    WHERE ce.state = 'open'
                    UNION ALL
                    SELECT 'credit', 'Customer on credit hold: ' || rp.name,
                           rp.id, rp.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = rp.company_id),
                           (SELECT COALESCE(SUM(am.amount_residual), 0)
                              FROM account_move am
                             WHERE am.partner_id = rp.id
                               AND am.move_type = 'out_invoice'
                               AND am.state = 'posted'
                               AND am.amount_residual > 0
                               AND am.invoice_date_due < CURRENT_DATE),
                           CURRENT_DATE, rp.name,
                           'res.partner', rp.id
                    FROM res_partner rp
                    WHERE EXISTS (
                        SELECT 1 FROM account_move am
                         WHERE am.partner_id = rp.id
                           AND am.move_type = 'out_invoice'
                           AND am.state = 'posted'
                           AND am.amount_residual > 0
                           AND am.invoice_date_due < CURRENT_DATE
                    )
                    UNION ALL
                    SELECT 'promise', 'Broken promise: ' || ct.name,
                           ct.partner_id, ct.company_id,
                           (SELECT c.currency_id FROM res_company c WHERE c.id = ct.company_id),
                           ct.amount_due, ct.promised_date, ct.name,
                           'vuma.collection.task', ct.id
                    FROM vuma_collection_task ct
                    WHERE ct.state = 'broken'
                ) q
            )
        """)


    @api.model
    def _cron_daily_digest(self):
        """Send the morning exception digest to operations and finance."""
        exception_types = dict(self._fields['type'].selection)
        exceptions = self.search([])
        counts = {}
        for exception in exceptions:
            counts[exception.type] = counts.get(exception.type, 0) + 1
        rows = ''.join(
            '<tr><td>%s</td><td>%s</td></tr>' % (
                exception_types.get(key, key), value)
            for key, value in sorted(counts.items()))
        body = (
            '<h3>Vuma daily exception digest</h3>'
            '<p>%s open exception(s).</p>'
            '<table border="1" cellpadding="4" cellspacing="0">'
            '<tr><th>Type</th><th>Count</th></tr>%s</table>'
            '<p><a href="/odoo/action-vuma_retail_ops.action_exception">Open the exception list</a></p>'
            % (len(exceptions), rows or '<tr><td colspan="2">None</td></tr>'))
        users = self.env['vuma.notification']._group_users([
            'vuma_retail_ops.group_vuma_ops_manager',
            'vuma_retail_ops.group_vuma_finance_manager',
            'vuma_retail_ops.group_vuma_owner',
        ])
        emails = [email for email in users.mapped('email') if email]
        if emails:
            self.env['mail.mail'].sudo().create({
                'subject': 'Vuma daily exception digest',
                'body_html': body,
                'email_to': ','.join(emails),
            })
        if exceptions:
            self.env['vuma.notification'].notify(
                exceptions[:1],
                ['vuma_retail_ops.group_vuma_ops_manager',
                 'vuma_retail_ops.group_vuma_finance_manager',
                 'vuma_retail_ops.group_vuma_owner'],
                _('Vuma daily exception digest'),
                _('%(count)s open exception(s). Open the exception list for detail.', count=len(exceptions)),
            )
        return len(exceptions)


class VumaTaxLedgerWizard(models.TransientModel):
    _name = 'vuma.tax.ledger'
    _description = 'VAT Ledger Reconciliation'

    name = fields.Char(string='Reference', default='VAT Ledger')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    direction = fields.Selection(
        [('both', 'Sales and purchases'), ('sale', 'Sales only'),
         ('purchase', 'Purchases only')],
        string='Documents', required=True, default='both')
    tax_id = fields.Many2one('account.tax', string='Tax')
    period_id = fields.Many2one(
        'vuma.tax.period', string='Compare to VAT Period')
    line_ids = fields.One2many(
        'vuma.tax.ledger.line', 'wizard_id', string='Tax Lines')
    output_base = fields.Monetary(
        string='Output Base', currency_field='currency_id')
    output_tax = fields.Monetary(
        string='Output VAT', currency_field='currency_id')
    input_base = fields.Monetary(
        string='Input Base', currency_field='currency_id')
    input_tax = fields.Monetary(
        string='Input VAT', currency_field='currency_id')
    net_due = fields.Monetary(
        string='Ledger Net Due', compute='_compute_reconciliation',
        currency_field='currency_id', store=True)
    period_net = fields.Monetary(
        string='VAT Period Net', compute='_compute_reconciliation',
        currency_field='currency_id', store=True)
    difference = fields.Monetary(
        string='Difference', compute='_compute_reconciliation',
        currency_field='currency_id', store=True)

    @api.depends('output_tax', 'input_tax', 'period_id.net_due')
    def _compute_reconciliation(self):
        for wizard in self:
            wizard.net_due = (wizard.output_tax or 0.0) - (wizard.input_tax or 0.0)
            wizard.period_net = wizard.period_id.net_due or 0.0
            wizard.difference = wizard.net_due - wizard.period_net

    def action_compute(self):
        self.ensure_one()
        self.line_ids.unlink()
        domain = [
            ('move_id.company_id', '=', self.company_id.id),
            ('move_id.state', '=', 'posted'),
            ('move_id.invoice_date', '>=', self.date_from),
            ('move_id.invoice_date', '<=', self.date_to),
            ('tax_line_id', '!=', False),
        ]
        if self.direction == 'sale':
            domain.append(('move_id.move_type', 'in', ('out_invoice', 'out_refund')))
        elif self.direction == 'purchase':
            domain.append(('move_id.move_type', 'in', ('in_invoice', 'in_refund')))
        if self.tax_id:
            domain.append(('tax_line_id', '=', self.tax_id.id))
        lines = self.env['account.move.line'].search(
            domain, order='date, id')
        output_base = output_tax = input_base = input_tax = 0.0
        for line in lines:
            move_type = line.move_id.move_type
            direction = 'output' if move_type in (
                'out_invoice', 'out_refund') else 'input'
            self.env['vuma.tax.ledger.line'].create({
                'wizard_id': self.id,
                'direction': direction,
                'date': line.move_id.invoice_date,
                'move_name': line.move_id.name,
                'partner_id': line.move_id.partner_id.id,
                'tax_id': line.tax_line_id.id,
                'base_amount': line.tax_base_amount or 0.0,
                'tax_amount': line.balance,
                'document_total': line.move_id.amount_total,
                'move_type': move_type,
            })
            if direction == 'output':
                output_base += line.tax_base_amount or 0.0
                output_tax += line.balance
            else:
                input_base += line.tax_base_amount or 0.0
                input_tax += line.balance
        self.write({
            'output_base': output_base,
            'output_tax': output_tax,
            'input_base': input_base,
            'input_tax': input_tax,
        })
        return True

    def action_print(self):
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_tax_ledger').report_action(self)


class VumaTaxLedgerLine(models.TransientModel):
    _name = 'vuma.tax.ledger.line'
    _description = 'VAT Ledger Line'
    _order = 'date, move_name, id'

    wizard_id = fields.Many2one(
        'vuma.tax.ledger', required=True, ondelete='cascade')
    direction = fields.Selection(
        [('output', 'Output'), ('input', 'Input')], required=True)
    date = fields.Date(string='Date')
    move_name = fields.Char(string='Document')
    move_type = fields.Char(string='Document Type')
    partner_id = fields.Many2one('res.partner', string='Partner')
    tax_id = fields.Many2one('account.tax', string='Tax')
    base_amount = fields.Monetary(
        string='Base', currency_field='currency_id')
    tax_amount = fields.Monetary(
        string='VAT', currency_field='currency_id')
    document_total = fields.Monetary(
        string='Document Total', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='wizard_id.currency_id', readonly=True)


class VumaBankImport(models.TransientModel):
    _name = 'vuma.bank.import'
    _description = 'Bank Statement CSV Import'

    journal_id = fields.Many2one(
        'account.journal', string='Bank / Cash Journal', required=True,
        domain="[('type', 'in', ('bank', 'cash'))]")
    file = fields.Binary(string='CSV File', required=True)
    filename = fields.Char(string='Filename')
    delimiter = fields.Char(string='Delimiter', default=',', required=True)
    notes = fields.Text(
        string='Expected Columns',
        default='date,reference,amount,partner,description',
        readonly=True)

    def action_import(self):
        self.ensure_one()
        raw = bytes(self.file or b'').decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(raw), delimiter=self.delimiter or ',')
        if not reader.fieldnames:
            raise UserError(_('The CSV file has no header row.'))
        header_map = {name.strip().lower(): name for name in reader.fieldnames}
        required = {'date', 'amount'}
        if not required.issubset(header_map):
            raise UserError(_('The CSV must contain date and amount columns.'))
        count = 0
        errors = []
        for row_number, row in enumerate(reader, start=2):
            try:
                date_value = fields.Date.to_date(
                    (row.get(header_map['date']) or '').strip())
                if not date_value:
                    raise ValueError(_('missing date'))
                amount = float(
                    (row.get(header_map['amount']) or '0').replace(' ', '')
                    .replace(',', ''))
                reference = (row.get(header_map.get('reference', '')) or '').strip() or '/'
                partner_name = (row.get(header_map.get('partner', '')) or '').strip()
                partner = self.env['res.partner'].search(
                    [('name', '=ilike', partner_name)], limit=1) if partner_name else self.env['res.partner']
                self.env['account.bank.statement.line'].create({
                    'journal_id': self.journal_id.id,
                    'date': date_value,
                    'payment_ref': reference,
                    'ref': (row.get(header_map.get('description', '')) or '')[:100],
                    'partner_id': partner.id or False,
                    'amount': amount,
                })
                count += 1
            except Exception as exc:
                errors.append(_('Row %(row)s: %(error)s', row=row_number, error=exc))
        if errors:
            raise UserError(_(
                'Imported %(count)s lines. The following rows failed:\n%(errors)s',
                count=count, errors='\n'.join(errors)))
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Bank import'),
                'message': _('%s statement lines imported.', count),
                'type': 'success',
                'sticky': False,
            },
        }
