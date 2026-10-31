# -*- coding: utf-8 -*-
"""Procurement control: comparison, claims, agreements, rebates and assets."""
from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .access_control import vuma_require
from .approvals import vuma_has_group


class VumaQuoteComparison(models.Model):
    _name = 'vuma.quote.comparison'
    _description = 'Supplier Quote Comparison'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Comparison Date', default=fields.Date.context_today,
        required=True, index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True, index=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    buyer_id = fields.Many2one(
        'res.users', string='Buyer', default=lambda self: self.env.user,
        required=True)
    rfq_id = fields.Many2one(
        'purchase.order', string='Source RFQ', domain="[('state','in',('draft','sent'))]")
    state = fields.Selection(
        [('draft', 'Draft'), ('comparing', 'Comparing'),
         ('awarded', 'Awarded'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', tracking=True, required=True)
    note = fields.Text(string='Notes')
    line_ids = fields.One2many(
        'vuma.quote.comparison.line', 'comparison_id', string='Quotes',
        copy=True)
    line_count = fields.Integer(compute='_compute_counts')
    supplier_count = fields.Integer(compute='_compute_counts')
    awarded_count = fields.Integer(compute='_compute_counts')
    best_total = fields.Monetary(
        string='Best Awarded Total', compute='_compute_counts',
        currency_field='currency_id')

    @api.depends('line_ids', 'line_ids.is_awarded', 'line_ids.subtotal')
    def _compute_counts(self):
        for comp in self:
            comp.line_count = len(comp.line_ids)
            comp.supplier_count = len(comp.line_ids.supplier_id)
            awarded = comp.line_ids.filtered('is_awarded')
            comp.awarded_count = len(awarded)
            comp.best_total = sum(awarded.mapped('subtotal'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.quote.comparison') or _('New')
        return super().create(vals_list)

    def action_start(self):
        self.write({'state': 'comparing'})
        return True

    def action_award(self):
        for comp in self:
            awarded = comp.line_ids.filtered('is_awarded')
            if not awarded:
                raise UserError(_('Select at least one quote line to award.'))
            if len(awarded.mapped('supplier_id')) > 1 \
                    and not (comp.award_reason or '').strip():
                raise UserError(_(
                    'Record why the award is split across more than one supplier.'))
            comp.state = 'awarded'
            self.env['vuma.audit.log'].log(comp, 'approve', note='Quote awarded')
        return True

    def action_create_orders(self):
        """Create one purchase order per awarded supplier."""
        orders = self.env['purchase.order']
        for comp in self:
            if comp.state != 'awarded':
                raise UserError(_('Award the comparison before creating orders.'))
            awarded = comp.line_ids.filtered('is_awarded')
            if not awarded:
                raise UserError(_('Award at least one line first.'))
            by_supplier = {}
            for line in awarded:
                by_supplier.setdefault(line.supplier_id.id, self.env[
                    'vuma.quote.comparison.line'])
                by_supplier[line.supplier_id.id] |= line
            for supplier_id, lines in by_supplier.items():
                supplier = self.env['res.partner'].browse(supplier_id)
                order = self.env['purchase.order'].create({
                    'partner_id': supplier.id,
                    'company_id': comp.company_id.id,
                    'date_order': fields.Datetime.now(),
                    'origin': comp.name,
                    'order_line': [Command.create({
                        'product_id': line.product_id.id,
                        'name': line.description or line.product_id.display_name,
                        'product_qty': line.quantity,
                        'uom_id': line.uom_id.id,
                        'price_unit': line.unit_price,
                        'date_planned': fields.Datetime.now(),
                    }) for line in lines],
                })
                orders |= order
                self.env['vuma.audit.log'].log(
                    order, 'create', note='Created from %s' % comp.name)
        if not orders:
            raise UserError(_('No purchase orders were created.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Purchase Orders from Comparison'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
        }


class VumaQuoteComparisonLine(models.Model):
    _name = 'vuma.quote.comparison.line'
    _description = 'Supplier Quote Comparison Line'
    _order = 'comparison_id, product_id, unit_price, id'

    comparison_id = fields.Many2one(
        'vuma.quote.comparison', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, ondelete='restrict')
    description = fields.Char(string='Description')
    quantity = fields.Float(
        string='Quantity', default=1.0, digits='Product Unit of Measure',
        required=True)
    uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    supplier_id = fields.Many2one(
        'res.partner', string='Supplier', required=True,
        domain="[('supplier_rank','>',0)]")
    unit_price = fields.Monetary(
        string='Unit Price', currency_field='currency_id', required=True)
    currency_id = fields.Many2one(
        related='comparison_id.currency_id', store=True, readonly=True)
    lead_time_days = fields.Integer(string='Lead Time (days)')
    min_qty = fields.Float(string='Minimum Qty', digits='Product Unit of Measure')
    validity_date = fields.Date(string='Quote Valid Until')
    is_awarded = fields.Boolean(string='Awarded')
    subtotal = fields.Monetary(
        string='Subtotal', compute='_compute_subtotal', store=True,
        currency_field='currency_id')
    note = fields.Char(string='Note')

    @api.depends('quantity', 'unit_price')
    def _compute_subtotal(self):
        for line in self:
            line.subtotal = (line.quantity or 0.0) * (line.unit_price or 0.0)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.description = self.product_id.display_name
            self.uom_id = self.product_id.uom_id
            self.unit_price = self.product_id.standard_price

    @api.constrains('quantity', 'unit_price')
    def _check_values(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(_('Quote quantity must be positive.'))
            if line.unit_price < 0:
                raise ValidationError(_('Quote price cannot be negative.'))


class PurchaseOrderComparison(models.Model):
    _inherit = 'purchase.order'

    vuma_comparison_id = fields.Many2one(
        'vuma.quote.comparison', string='Quote Comparison', readonly=True,
        copy=False)

    def action_vuma_create_comparison(self):
        self.ensure_one()
        comp = self.env['vuma.quote.comparison'].create({
            'rfq_id': self.id,
            'note': _('Comparison for %s', self.name),
            'line_ids': [Command.create({
                'product_id': line.product_id.id,
                'description': line.name,
                'quantity': line.product_qty,
                'uom_id': line.uom_id.id,
                'supplier_id': self.partner_id.id,
                'unit_price': line.price_unit,
            }) for line in self.order_line if line.display_type == 'product'],
        })
        self.vuma_comparison_id = comp
        return {
            'type': 'ir.actions.act_window',
            'name': _('Supplier Comparison'),
            'res_model': 'vuma.quote.comparison',
            'view_mode': 'form',
            'res_id': comp.id,
        }


class VumaSupplierClaim(models.Model):
    _name = 'vuma.supplier.claim'
    _description = 'Supplier Claim'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'claimed_amount', 'approved_amount')

    name = fields.Char(
        string='Claim Number', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Claim Date', default=fields.Date.context_today, required=True,
        index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True, index=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    supplier_id = fields.Many2one(
        'res.partner', string='Supplier', required=True, index=True,
        domain="[('supplier_rank','>',0)]")
    claim_type = fields.Selection(
        [
            ('short', 'Short Delivery'),
            ('over', 'Over Delivery'),
            ('price', 'Price Higher Than Agreed'),
            ('discount', 'Discount Not Applied'),
            ('rebate', 'Rebate Not Credited'),
            ('damage', 'Damaged in Transit'),
            ('wrong', 'Wrong Product'),
            ('expiry', 'Expired / Short-Dated'),
            ('duplicate', 'Duplicate Billing'),
            ('other', 'Other'),
        ],
        string='Claim Type', required=True, default='short', tracking=True)
    purchase_id = fields.Many2one(
        'purchase.order', string='Purchase Order', index=True)
    picking_id = fields.Many2one(
        'stock.picking', string='Goods Receipt', index=True)
    bill_id = fields.Many2one(
        'account.move', string='Supplier Invoice', index=True,
        domain="[('move_type','=','in_invoice')]")
    description = fields.Text(string='Description')
    evidence = fields.Binary(string='Evidence')
    evidence_filename = fields.Char(string='Evidence Filename')
    claimed_amount = fields.Monetary(
        string='Claimed', compute='_compute_amounts', store=True,
        currency_field='currency_id')
    approved_amount = fields.Monetary(
        string='Approved', currency_field='currency_id')
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'),
         ('approved', 'Approved'), ('rejected', 'Rejected'),
         ('settled', 'Settled'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', tracking=True, required=True,
        index=True)
    submitted_by_id = fields.Many2one(
        'res.users', string='Submitted By', readonly=True, copy=False)
    submitted_date = fields.Datetime(
        string='Submitted On', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason', copy=False)
    settled_date = fields.Date(string='Settled On', readonly=True, copy=False)
    debit_note_id = fields.Many2one(
        'account.move', string='Debit Note', readonly=True, copy=False)
    line_ids = fields.One2many(
        'vuma.supplier.claim.line', 'claim_id', string='Claim Lines', copy=True)

    @api.depends('line_ids.claimed_amount')
    def _compute_amounts(self):
        for claim in self:
            claim.claimed_amount = sum(claim.line_ids.mapped('claimed_amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.supplier.claim') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        for claim in self:
            if not claim.line_ids:
                raise UserError(_('Add at least one claim line before submitting.'))
            if claim.claim_type in ('damage', 'short', 'wrong', 'expiry') \
                    and not claim.evidence:
                raise UserError(_(
                    'Attach the evidence (photo, delivery note or credit note) '
                    'before submitting this claim.'))
            claim.write({
                'state': 'submitted',
                'submitted_by_id': self.env.user.id,
                'submitted_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(claim, 'approve', note='Submitted')
        return True

    def _check_approver(self):
        vuma_require(self.env, 'action.approve',
                     _('A supplier claim is approved by finance or the owner.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('A supplier claim is approved by finance or the owner.'))
        for claim in self:
            if claim.approved_amount > 25000.0 and not vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_owner',
                    'base.group_system'):
                raise AccessError(_(
                    'A supplier claim above R25,000 needs owner approval.'))

    def action_approve(self):
        for claim in self:
            if claim.state != 'submitted':
                raise UserError(_('Only a submitted claim can be approved.'))
            if claim.approved_amount <= 0:
                claim.approved_amount = claim.claimed_amount
            claim._check_approver()
            if claim.approved_amount <= 0:
                claim.approved_amount = claim.claimed_amount
            if claim.approved_amount > claim.claimed_amount:
                raise UserError(_('Approved amount cannot exceed the claimed amount.'))
            claim.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(claim, 'approve')
        return True

    def action_reject(self):
        for claim in self:
            claim._check_approver()
            claim.state = 'rejected'
            self.env['vuma.audit.log'].log(claim, 'reject', note=claim.rejection_reason)
        return True

    def action_create_debit_note(self):
        for claim in self:
            if claim.state != 'approved':
                raise UserError(_('Approve the claim before creating a debit note.'))
            if claim.debit_note_id:
                raise UserError(_('A debit note already exists for this claim.'))
            if claim.approved_amount <= 0:
                raise UserError(_('The approved amount must be positive.'))
            lines = []
            remaining = claim.approved_amount
            for line in claim.line_ids:
                amount = line.claimed_amount
                if amount <= 0:
                    continue
                if remaining <= 0:
                    break
                use_amount = min(amount, remaining)
                qty = line.quantity or 1.0
                lines.append(Command.create({
                    'product_id': line.product_id.id if line.product_id else False,
                    'name': line.description or line.product_id.display_name
                            or _('Supplier claim %s', claim.name),
                    'quantity': qty,
                    'price_unit': use_amount / qty if qty else use_amount,
                    'tax_ids': [Command.clear()],
                }))
                remaining -= use_amount
            if not lines:
                # A price or discount claim may not have product lines with a
                # positive amount; fall back to one descriptive line.
                lines.append(Command.create({
                    'name': _('Supplier claim %(claim)s - %(type)s',
                              claim=claim.name,
                              type=dict(claim._fields['claim_type'].selection)[claim.claim_type]),
                    'quantity': 1.0,
                    'price_unit': claim.approved_amount,
                    'tax_ids': [Command.clear()],
                }))
            move = self.env['account.move'].with_company(claim.company_id).create({
                'move_type': 'in_refund',
                'partner_id': claim.supplier_id.id,
                'invoice_date': claim.date,
                'date': claim.date,
                'ref': claim.name,
                'company_id': claim.company_id.id,
                'currency_id': claim.currency_id.id,
                'invoice_line_ids': lines,
                'vuma_supplier_claim_id': claim.id,
                'vuma_credit_approval_state': (
                    'approved' if claim.approved_amount <= 25000.0
                    or vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_owner',
                        'base.group_system') else 'pending'),
                'vuma_credit_approved_by_id': self.env.user.id if (
                    claim.approved_amount <= 25000.0
                    or vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_owner',
                        'base.group_system')) else False,
                'vuma_credit_approved_date': fields.Datetime.now() if (
                    claim.approved_amount <= 25000.0
                    or vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_owner',
                        'base.group_system')) else False,
            })
            claim.write({
                'debit_note_id': move.id,
                'state': 'settled',
                'settled_date': fields.date.today(),
            })
            self.env['vuma.audit.log'].log(
                claim, 'post', note='Debit note %s' % move.name)
        return True

    def action_view_debit_note(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.debit_note_id.id,
        }


class VumaSupplierClaimLine(models.Model):
    _name = 'vuma.supplier.claim.line'
    _description = 'Supplier Claim Line'

    claim_id = fields.Many2one(
        'vuma.supplier.claim', required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one('product.product', string='Product')
    description = fields.Char(string='Description')
    quantity = fields.Float(
        string='Quantity', default=1.0, digits='Product Unit of Measure')
    uom_id = fields.Many2one(
        'uom.uom', string='Unit',
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    unit_price = fields.Monetary(
        string='Unit Price', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='claim_id.currency_id', store=True, readonly=True)
    claimed_amount = fields.Monetary(
        string='Claimed Amount', compute='_compute_amount', store=True,
        currency_field='currency_id', readonly=False)
    received_qty = fields.Float(string='Received Qty', digits='Product Unit of Measure')
    invoiced_qty = fields.Float(string='Invoiced Qty', digits='Product Unit of Measure')
    note = fields.Char(string='Note')

    @api.depends('quantity', 'unit_price')
    def _compute_amount(self):
        for line in self:
            if not line.claimed_amount:
                line.claimed_amount = (line.quantity or 0.0) * (line.unit_price or 0.0)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.description = self.product_id.display_name
            self.uom_id = self.product_id.uom_id
            self.unit_price = self.product_id.standard_price


class VumaSupplierAgreement(models.Model):
    _name = 'vuma.supplier.agreement'
    _description = 'Supplier Trading Agreement'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'date_end, id desc'
    _vuma_audit_fields = ('state', 'date_end', 'attachment')

    name = fields.Char(string='Agreement', required=True, tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', required=True, index=True,
        domain="[('supplier_rank','>',0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    agreement_type = fields.Selection(
        [('price', 'Price Agreement'), ('rebate', 'Rebate Agreement'),
         ('marketing', 'Marketing Agreement'), ('consignment', 'Consignment'),
         ('service', 'Service / SLA'), ('other', 'Other')],
        string='Type', required=True, default='price', tracking=True)
    date_start = fields.Date(string='Start Date', required=True,
                             default=fields.Date.context_today)
    date_end = fields.Date(string='End Date', required=True, index=True)
    notice_days = fields.Integer(string='Renewal Notice (days)', default=60)
    owner_id = fields.Many2one(
        'res.users', string='Owner', default=lambda self: self.env.user)
    state = fields.Selection(
        [('draft', 'Draft'), ('active', 'Active'), ('expired', 'Expired'),
         ('cancelled', 'Cancelled')],
        string='Status', default='draft', tracking=True, required=True)
    attachment = fields.Binary(string='Signed PDF', attachment=True)
    attachment_filename = fields.Char(string='PDF Filename')
    notes = fields.Text(string='Notes')
    reminder_date = fields.Date(
        string='Reminder Date', compute='_compute_reminder_date', store=True)

    @api.depends('date_end', 'notice_days')
    def _compute_reminder_date(self):
        for rec in self:
            rec.reminder_date = fields.Date.subtract(
                rec.date_end, days=rec.notice_days) if rec.date_end else False

    @api.constrains('date_start', 'date_end')
    def _check_dates(self):
        for rec in self:
            if rec.date_end and rec.date_start and rec.date_end < rec.date_start:
                raise ValidationError(_('The end date cannot be before the start date.'))

    def action_activate(self):
        for rec in self:
            if not rec.attachment:
                raise UserError(_('Attach the signed PDF before activating the agreement.'))
            rec.state = 'active'
            self.env['vuma.audit.log'].log(rec, 'approve')
        return True

    def action_expire(self):
        self.write({'state': 'expired'})
        return True

    @api.model
    def _cron_agreement_alerts(self):
        today = fields.Date.context_today(self)
        agreements = self.search([
            ('state', '=', 'active'),
            ('reminder_date', '<=', today),
            ('date_end', '>=', today),
        ])
        for agreement in agreements:
            agreement.activity_schedule(
                'mail.mail_activity_data_todo',
                date_deadline=agreement.reminder_date,
                summary=_('Supplier agreement renewal: %s', agreement.name),
                user_id=agreement.owner_id.id or self.env.user.id,
            )


class VumaRebateAgreement(models.Model):
    _name = 'vuma.rebate.agreement'
    _description = 'Supplier Rebate Agreement'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date_end, id desc'
    _vuma_audit_fields = ('state', 'rate_percent', 'date_end')

    name = fields.Char(string='Rebate Agreement', required=True, tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', required=True, index=True,
        domain="[('supplier_rank','>',0)]")
    agreement_id = fields.Many2one(
        'vuma.supplier.agreement', string='Trading Agreement')
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    rebate_type = fields.Selection(
        [('volume', 'Volume Rebate'), ('growth', 'Growth Rebate'),
         ('marketing', 'Marketing Rebate'), ('settlement', 'Settlement Discount')],
        string='Rebate Type', required=True, default='volume')
    date_start = fields.Date(string='Start Date', required=True,
                             default=fields.Date.context_today)
    date_end = fields.Date(string='End Date', required=True)
    rate_percent = fields.Float(string='Rate (%)', digits=(6, 3))
    target_amount = fields.Monetary(
        string='Target Spend', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    settlement_frequency = fields.Selection(
        [('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
         ('annual', 'Annual')],
        string='Settlement', default='quarterly', required=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('active', 'Active'), ('expired', 'Expired')],
        string='Status', default='draft', required=True, tracking=True)
    notes = fields.Text(string='Notes')
    claim_ids = fields.One2many('vuma.rebate.claim', 'agreement_id', string='Claims')
    accrual_ids = fields.One2many(
        'vuma.rebate.accrual', 'agreement_id', string='Monthly Accruals')
    claim_count = fields.Integer(compute='_compute_claim_count')

    @api.depends('claim_ids')
    def _compute_claim_count(self):
        for rec in self:
            rec.claim_count = len(rec.claim_ids)

    def action_activate(self):
        if not self.date_start or not self.date_end:
            raise UserError(_('Set the agreement period before activating it.'))
        self.write({'state': 'active'})
        return True

    @api.model
    def _cron_monthly_accruals(self):
        """Accrue volume and growth rebates for the previous calendar month.

        Settlement is quarterly, so the accruals stay open until a quarterly
        claim is created.  Marketing is claim-based and is not accrued here.
        """
        today = fields.Date.context_today(self)
        period_end = today.replace(day=1) - relativedelta(days=1)
        period_start = period_end.replace(day=1)
        agreements = self.search([
            ('state', '=', 'active'),
            ('rebate_type', 'in', ('volume', 'growth')),
            ('date_start', '<=', period_end),
            ('date_end', '>=', period_start),
        ])
        for agreement in agreements:
            if agreement.accrual_ids.filtered(
                    lambda a: a.period_start == period_start
                    and a.period_end == period_end):
                continue
            bills = self.env['account.move'].search([
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
                ('partner_id', '=', agreement.partner_id.id),
                ('company_id', '=', agreement.company_id.id),
                ('invoice_date', '>=', period_start),
                ('invoice_date', '<=', period_end),
            ])
            basis = sum(bills.mapped('amount_untaxed'))
            self.env['vuma.rebate.accrual'].create({
                'agreement_id': agreement.id,
                'period_start': period_start,
                'period_end': period_end,
                'basis_amount': basis,
                'amount': basis * (agreement.rate_percent or 0.0) / 100.0,
            })
        return True

    def action_create_quarterly_claim(self):
        self.ensure_one()
        accruals = self.accrual_ids.filtered(lambda a: a.state == 'draft')
        if not accruals:
            raise UserError(_('There are no open monthly accruals to claim.'))
        claim = self.env['vuma.rebate.claim'].create({
            'agreement_id': self.id,
            'date_start': min(accruals.mapped('period_start')),
            'date_end': max(accruals.mapped('period_end')),
            'rate_percent': self.rate_percent,
            'basis_amount': sum(accruals.mapped('basis_amount')),
        })
        accruals.write({'state': 'claimed', 'claim_id': claim.id})
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'vuma.rebate.claim',
            'view_mode': 'form',
            'res_id': claim.id,
        }


class VumaRebateAccrual(models.Model):
    _name = 'vuma.rebate.accrual'
    _description = 'Monthly Supplier Rebate Accrual'
    _order = 'period_end desc, id desc'

    agreement_id = fields.Many2one(
        'vuma.rebate.agreement', string='Agreement', required=True,
        ondelete='cascade', index=True)
    partner_id = fields.Many2one(
        related='agreement_id.partner_id', store=True, readonly=True)
    company_id = fields.Many2one(
        related='agreement_id.company_id', store=True, readonly=True)
    currency_id = fields.Many2one(
        related='agreement_id.currency_id', readonly=True)
    period_start = fields.Date(string='Period From', required=True)
    period_end = fields.Date(string='Period To', required=True)
    basis_amount = fields.Monetary(
        string='Purchases / Basis', currency_field='currency_id')
    amount = fields.Monetary(
        string='Accrual', currency_field='currency_id')
    state = fields.Selection(
        [('draft', 'Accrued'), ('claimed', 'Claimed'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True)
    claim_id = fields.Many2one(
        'vuma.rebate.claim', string='Rebate Claim', readonly=True, copy=False)


class VumaRebateClaim(models.Model):
    _name = 'vuma.rebate.claim'
    _description = 'Supplier Rebate Claim'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date_end desc, id desc'
    _vuma_audit_fields = ('state', 'claimed_amount', 'approved_amount')

    name = fields.Char(
        string='Claim Number', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    agreement_id = fields.Many2one(
        'vuma.rebate.agreement', string='Rebate Agreement', required=True,
        index=True)
    partner_id = fields.Many2one(
        related='agreement_id.partner_id', string='Supplier', store=True,
        readonly=True)
    company_id = fields.Many2one(
        related='agreement_id.company_id', string='Company', store=True,
        readonly=True)
    currency_id = fields.Many2one(
        related='agreement_id.currency_id', string='Currency', readonly=True)
    date_start = fields.Date(string='Period From', required=True)
    date_end = fields.Date(string='Period To', required=True)
    basis_amount = fields.Monetary(
        string='Purchases / Basis', currency_field='currency_id',
        compute='_compute_basis', store=True, readonly=False)
    rate_percent = fields.Float(
        string='Rate (%)', related='agreement_id.rate_percent', readonly=False)
    claimed_amount = fields.Monetary(
        string='Claimed', currency_field='currency_id',
        compute='_compute_claimed', store=True, readonly=False)
    approved_amount = fields.Monetary(
        string='Approved', currency_field='currency_id')
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'),
         ('approved', 'Approved'), ('rejected', 'Rejected'),
         ('settled', 'Settled')],
        string='Status', default='draft', required=True, tracking=True)
    evidence = fields.Binary(string='Supporting Evidence')
    evidence_filename = fields.Char(string='Evidence Filename')
    credit_note_id = fields.Many2one(
        'account.move', string='Supplier Credit Note', readonly=True, copy=False)
    notes = fields.Text(string='Notes')
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)

    @api.depends('agreement_id', 'date_start', 'date_end')
    def _compute_basis(self):
        for claim in self:
            if not (claim.agreement_id and claim.date_start and claim.date_end):
                claim.basis_amount = 0.0
                continue
            domain = [
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
                ('partner_id', '=', claim.partner_id.id),
                ('company_id', '=', claim.company_id.id),
                ('invoice_date', '>=', claim.date_start),
                ('invoice_date', '<=', claim.date_end),
            ]
            bills = self.env['account.move'].search(domain)
            claim.basis_amount = sum(bills.mapped('amount_untaxed'))

    @api.depends('basis_amount', 'rate_percent')
    def _compute_claimed(self):
        for claim in self:
            claim.claimed_amount = (
                (claim.basis_amount or 0.0) * (claim.rate_percent or 0.0) / 100.0)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.rebate.claim') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        for claim in self:
            if claim.state != 'draft':
                raise UserError(_('Only a draft rebate claim can be submitted.'))
            if claim.claimed_amount <= 0:
                raise UserError(_('The rebate claim has no value.'))
            if claim.agreement_id.rebate_type == 'marketing':
                if not claim.evidence:
                    raise UserError(_(
                        'Attach proof of the marketing activity before submitting.'))
                if claim.date_end and (fields.Date.context_today(self)
                                       - claim.date_end).days > 30:
                    raise UserError(_(
                        'Marketing proof must be submitted within 30 days of '
                        'the activity period.'))
        self.write({'state': 'submitted'})
        return True

    def action_approve(self):
        vuma_require(self.env, 'action.approve',
                     _('A rebate claim is approved by finance or the owner.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('A rebate claim is approved by finance or the owner.'))
        for claim in self:
            if claim.state != 'submitted':
                raise UserError(_('Only a submitted rebate claim can be approved.'))
            if claim.claimed_amount > 25000.0 and not vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_owner',
                    'base.group_system'):
                raise AccessError(_(
                    'A rebate claim above R25,000 needs owner approval.'))
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approved_amount': self.claimed_amount,
        })
        self.env['vuma.audit.log'].log(self, 'approve')
        return True

    def action_create_credit_note(self):
        for claim in self:
            if claim.state != 'approved':
                raise UserError(_('Approve the rebate claim first.'))
            if claim.credit_note_id:
                raise UserError(_('A credit note already exists for this claim.'))
            move = self.env['account.move'].with_company(claim.company_id).create({
                'move_type': 'in_refund',
                'partner_id': claim.partner_id.id,
                'invoice_date': fields.Date.context_today(self),
                'date': fields.Date.context_today(self),
                'ref': claim.name,
                'company_id': claim.company_id.id,
                'currency_id': claim.currency_id.id,
                'invoice_line_ids': [Command.create({
                    'name': _('Rebate claim %s', claim.name),
                    'quantity': 1.0,
                    'price_unit': claim.approved_amount,
                    'tax_ids': [Command.clear()],
                })],
                'vuma_rebate_claim_id': claim.id,
                'vuma_credit_approval_state': (
                    'approved' if claim.approved_amount <= 25000.0
                    or vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_owner',
                        'base.group_system') else 'pending'),
                'vuma_credit_approved_by_id': self.env.user.id if (
                    claim.approved_amount <= 25000.0
                    or vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_owner',
                        'base.group_system')) else False,
                'vuma_credit_approved_date': fields.Datetime.now() if (
                    claim.approved_amount <= 25000.0
                    or vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_owner',
                        'base.group_system')) else False,
            })
            claim.write({'credit_note_id': move.id, 'state': 'settled'})
        return True


class VumaSupplierAsset(models.Model):
    _name = 'vuma.supplier.asset'
    _description = 'Supplier-Owned Asset'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'partner_id, name'
    _vuma_audit_fields = ('state', 'location', 'condition')

    name = fields.Char(string='Asset', required=True, tracking=True)
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', required=True, index=True,
        domain="[('supplier_rank','>',0)]")
    agreement_id = fields.Many2one(
        'vuma.supplier.agreement', string='Agreement')
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    asset_type = fields.Selection(
        [('stand', 'Display Stand'), ('fridge', 'Fridge / Cooler'),
         ('shelving', 'Shelving'), ('uniform', 'Uniform'),
         ('marketing', 'Marketing Material'), ('other', 'Other')],
        string='Type', required=True, default='stand')
    serial = fields.Char(string='Serial / Asset Tag')
    location = fields.Char(string='Location')
    placement_date = fields.Date(string='Placed On')
    return_date = fields.Date(string='Returned On')
    condition = fields.Selection(
        [('new', 'New'), ('good', 'Good'), ('fair', 'Fair'),
         ('damaged', 'Damaged'), ('lost', 'Lost')],
        string='Condition', default='good')
    state = fields.Selection(
        [('in_use', 'In Use'), ('returned', 'Returned'),
         ('lost', 'Lost'), ('written_off', 'Written Off')],
        string='Status', default='in_use', required=True, tracking=True)
    value = fields.Monetary(
        string='Value', currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    image = fields.Image(string='Photo', max_width=1024, max_height=1024)
    notes = fields.Text(string='Notes')

    def action_return(self):
        for rec in self:
            rec.write({
                'state': 'returned',
                'return_date': fields.Date.context_today(self),
            })
        return True

    def action_mark_lost(self):
        self.write({'state': 'lost', 'condition': 'lost'})
        return True


class VumaSupplierScorecard(models.Model):
    _name = 'vuma.supplier.scorecard'
    _description = 'Supplier Scorecard'
    _inherit = ['mail.thread']
    _order = 'date_to desc, partner_id'

    name = fields.Char(string='Scorecard', compute='_compute_name', store=True)
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', required=True, index=True,
        domain="[('supplier_rank','>',0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    po_count = fields.Integer(string='Purchase Orders', compute='_compute_metrics')
    receipt_count = fields.Integer(string='Receipts', compute='_compute_metrics')
    on_time_count = fields.Integer(string='On Time', compute='_compute_metrics')
    late_count = fields.Integer(string='Late', compute='_compute_metrics')
    fill_rate = fields.Float(string='Fill Rate (%)', compute='_compute_metrics')
    claim_count = fields.Integer(string='Claims', compute='_compute_metrics')
    claim_amount = fields.Monetary(
        string='Claim Value', compute='_compute_metrics', currency_field='currency_id')
    avg_lead_time = fields.Float(string='Avg Lead Time (days)', compute='_compute_metrics')
    score = fields.Float(string='Score', compute='_compute_metrics')
    grade = fields.Selection(
        [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')],
        string='Grade', compute='_compute_metrics')
    notes = fields.Text(string='Actions')

    @api.depends('partner_id', 'date_to')
    def _compute_name(self):
        for rec in self:
            rec.name = '%s - %s' % (rec.partner_id.name or _('Supplier'),
                                    rec.date_to or '')

    @api.depends('partner_id', 'company_id', 'date_from', 'date_to')
    def _compute_metrics(self):
        for rec in self:
            rec.po_count = rec.receipt_count = rec.on_time_count = 0
            rec.late_count = rec.claim_count = 0
            rec.fill_rate = rec.claim_amount = rec.avg_lead_time = rec.score = 0.0
            rec.grade = 'd'
            if not (rec.partner_id and rec.date_from and rec.date_to):
                continue
            orders = self.env['purchase.order'].search([
                ('partner_id', '=', rec.partner_id.id),
                ('company_id', '=', rec.company_id.id),
                ('state', 'in', ('purchase', 'done')),
                ('date_order', '>=', rec.date_from),
                ('date_order', '<=', rec.date_to),
            ])
            rec.po_count = len(orders)
            pickings = orders.picking_ids.filtered(
                lambda p: p.state == 'done' and p.picking_type_code == 'incoming')
            rec.receipt_count = len(pickings)
            on_time = 0
            lead_times = []
            for picking in pickings:
                scheduled = picking.scheduled_date or picking.date_done
                done = picking.date_done or scheduled
                if scheduled and done and done <= scheduled:
                    on_time += 1
                if picking.date_done and picking.date:
                    lead_times.append((picking.date_done.date() - picking.date.date()).days)
            rec.on_time_count = on_time
            rec.late_count = max(0, rec.receipt_count - on_time)
            ordered = sum(orders.order_line.mapped('product_qty'))
            received = sum(pickings.move_ids.mapped('quantity'))
            rec.fill_rate = (received / ordered * 100.0) if ordered else 0.0
            rec.avg_lead_time = (sum(lead_times) / len(lead_times)) if lead_times else 0.0
            claims = self.env['vuma.supplier.claim'].search([
                ('supplier_id', '=', rec.partner_id.id),
                ('company_id', '=', rec.company_id.id),
                ('date', '>=', rec.date_from),
                ('date', '<=', rec.date_to),
                ('state', 'in', ('approved', 'settled')),
            ])
            rec.claim_count = len(claims)
            rec.claim_amount = sum(claims.mapped('approved_amount'))
            on_time_pct = (rec.on_time_count / rec.receipt_count * 100.0) \
                if rec.receipt_count else 0.0
            fill_score = min(100.0, rec.fill_rate)
            claim_penalty = min(30.0, rec.claim_count * 5.0)
            rec.score = max(0.0, on_time_pct * 0.35 + fill_score * 0.20
                            + 100.0 * 0.20 + 100.0 * 0.15 + 100.0 * 0.10
                            - claim_penalty)
            if rec.score >= 85:
                rec.grade = 'a'
            elif rec.score >= 70:
                rec.grade = 'b'
            elif rec.score >= 50:
                rec.grade = 'c'
            else:
                rec.grade = 'd'


class VumaPaymentRun(models.Model):
    _name = 'vuma.payment.run'
    _description = 'Creditor Payment Run'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Payment Date', default=fields.Date.context_today, required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    journal_id = fields.Many2one(
        'account.journal', string='Bank / Cash Journal', required=True,
        domain="[('type','in',('bank','cash')),('company_id','=',company_id)]")
    payment_method_line_id = fields.Many2one(
        'account.payment.method.line', string='Payment Method',
        domain="[('journal_id','=',journal_id)]")
    date_to = fields.Date(string='Bills Due By', default=fields.Date.context_today)
    state = fields.Selection(
        [('draft', 'Draft'), ('approved', 'Approved'), ('done', 'Paid'),
         ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    line_ids = fields.One2many(
        'vuma.payment.run.line', 'run_id', string='Bills', copy=True)
    total_amount = fields.Monetary(
        string='Total', compute='_compute_total', currency_field='currency_id')
    note = fields.Text(string='Notes')

    @api.depends('line_ids.amount', 'line_ids.is_selected')
    def _compute_total(self):
        for run in self:
            run.total_amount = sum(
                run.line_ids.filtered('is_selected').mapped('amount'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.payment.run') or _('New')
        return super().create(vals_list)

    def action_load_bills(self):
        for run in self:
            run.line_ids.unlink()
            bills = self.env['account.move'].search([
                ('move_type', '=', 'in_invoice'),
                ('state', '=', 'posted'),
                ('payment_state', 'in', ('not_paid', 'partial')),
                ('company_id', '=', run.company_id.id),
                ('invoice_date_due', '<=', run.date_to or run.date),
            ], order='invoice_date_due, id')
            run.line_ids = [Command.create({
                'move_id': bill.id,
                'partner_id': bill.partner_id.id,
                'amount': bill.amount_residual,
                'invoice_date_due': bill.invoice_date_due,
                'is_selected': True,
            }) for bill in bills]
        return True

    def action_approve(self):
        vuma_require(self.env, 'action.approve',
                     _('Payment runs are approved by finance.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Payment runs are approved by finance.'))
        for run in self:
            if run.state != 'draft':
                raise UserError(_('Only a draft payment run can be approved.'))
            if not run.line_ids.filtered('is_selected'):
                raise UserError(_('Select at least one bill first.'))
            run.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(run, 'approve')
        return True

    def action_create_payments(self):
        for run in self:
            if run.state != 'approved':
                raise UserError(_('Approve the payment run before paying.'))
            selected = run.line_ids.filtered('is_selected')
            if not selected:
                raise UserError(_('Select at least one bill to pay.'))
            by_partner = {}
            for line in selected:
                by_partner.setdefault(line.partner_id.id, self.env[
                    'vuma.payment.run.line'])
                by_partner[line.partner_id.id] |= line
            payments = self.env['account.payment']
            for partner_id, lines in by_partner.items():
                bills = lines.move_id
                wizard = self.env['account.payment.register'].with_context(
                    active_model='account.move',
                    active_ids=bills.ids,
                ).create({
                    'payment_date': run.date,
                    'journal_id': run.journal_id.id,
                    'payment_method_line_id': run.payment_method_line_id.id or False,
                    'amount': sum(lines.mapped('amount')),
                    'group_payment': True,
                })
                result = wizard.action_create_payments()
                payment = self.env['account.payment']
                if isinstance(result, dict) and result.get('res_id'):
                    payment = self.env['account.payment'].browse(result['res_id'])
                payments |= payment
                for line in lines:
                    line.payment_id = payment.id if payment else False
            run.state = 'done'
            self.env['vuma.audit.log'].log(run, 'post', note='%s payment(s)' % len(payments))
        return True


class VumaPaymentRunLine(models.Model):
    _name = 'vuma.payment.run.line'
    _description = 'Payment Run Line'
    _order = 'invoice_date_due, id'

    run_id = fields.Many2one(
        'vuma.payment.run', required=True, ondelete='cascade', index=True)
    move_id = fields.Many2one(
        'account.move', string='Bill', required=True, ondelete='cascade')
    partner_id = fields.Many2one(
        'res.partner', string='Supplier', related='move_id.partner_id', store=True)
    amount = fields.Monetary(
        string='Amount', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='run_id.currency_id', store=True, readonly=True)
    invoice_date_due = fields.Date(
        string='Due Date', related='move_id.invoice_date_due', store=True)
    is_selected = fields.Boolean(string='Pay', default=True)
    payment_id = fields.Many2one(
        'account.payment', string='Payment', readonly=True, copy=False)


class VumaAgedPayable(models.Model):
    _name = 'vuma.aged.payable'
    _description = 'Creditor Age Analysis'
    _auto = False
    _log_access = False
    _order = 'bucket_90_plus desc, outstanding desc'

    partner_id = fields.Many2one('res.partner', string='Supplier', readonly=True)
    partner_name = fields.Char('Supplier', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one(
        'res.currency', string='Currency', readonly=True)
    bucket_current = fields.Monetary(
        'Not Due Yet', readonly=True, currency_field='company_currency_id')
    bucket_30 = fields.Monetary(
        '1 to 30 Days', readonly=True, currency_field='company_currency_id')
    bucket_60 = fields.Monetary(
        '31 to 60 Days', readonly=True, currency_field='company_currency_id')
    bucket_90 = fields.Monetary(
        '61 to 90 Days', readonly=True, currency_field='company_currency_id')
    bucket_90_plus = fields.Monetary(
        'Over 90 Days', readonly=True, currency_field='company_currency_id')
    outstanding = fields.Monetary(
        'Total Outstanding', readonly=True, currency_field='company_currency_id')
    overdue = fields.Monetary(
        'Total Overdue', readonly=True, currency_field='company_currency_id')
    invoice_count = fields.Integer('Unpaid Bills', readonly=True)
    oldest_due = fields.Date('Oldest Due', readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, 'vuma_aged_payable')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW vuma_aged_payable AS (
                WITH unpaid AS (
                    SELECT
                        am.partner_id AS partner_id,
                        am.company_id AS company_id,
                        COUNT(*) AS invoice_count,
                        SUM(am.amount_residual) AS outstanding,
                        MIN(am.invoice_date_due) AS oldest_due,
                        SUM(am.amount_residual) FILTER (
                            WHERE am.invoice_date_due IS NULL
                               OR am.invoice_date_due >= CURRENT_DATE) AS bucket_current,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due BETWEEN 1 AND 30) AS bucket_30,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due BETWEEN 31 AND 60) AS bucket_60,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due BETWEEN 61 AND 90) AS bucket_90,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due > 90) AS bucket_90_plus
                    FROM account_move am
                    WHERE am.move_type IN ('in_invoice', 'in_refund')
                      AND am.state = 'posted'
                      AND am.amount_residual != 0
                    GROUP BY am.partner_id, am.company_id
                )
                SELECT
                    ROW_NUMBER() OVER (ORDER BY u.partner_id, u.company_id) AS id,
                    p.id AS partner_id,
                    p.name AS partner_name,
                    u.company_id AS company_id,
                    (SELECT c.currency_id FROM res_company c
                      WHERE c.id = u.company_id) AS company_currency_id,
                    COALESCE(u.invoice_count, 0) AS invoice_count,
                    COALESCE(u.bucket_current, 0) AS bucket_current,
                    COALESCE(u.bucket_30, 0) AS bucket_30,
                    COALESCE(u.bucket_60, 0) AS bucket_60,
                    COALESCE(u.bucket_90, 0) AS bucket_90,
                    COALESCE(u.bucket_90_plus, 0) AS bucket_90_plus,
                    COALESCE(u.outstanding, 0) AS outstanding,
                    COALESCE(u.bucket_30, 0) + COALESCE(u.bucket_60, 0)
                        + COALESCE(u.bucket_90, 0)
                        + COALESCE(u.bucket_90_plus, 0) AS overdue,
                    u.oldest_due AS oldest_due
                FROM res_partner p
                JOIN unpaid u ON u.partner_id = p.id
            )
        """)
