# -*- coding: utf-8 -*-
"""Stock counts, cycle counts and town-based picking."""
from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .approvals import vuma_has_group


class VumaStockCount(models.Model):
    _name = 'vuma.stock.count'
    _description = 'Stock Take / Count'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(
        string='Count Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Count Date', default=fields.Date.context_today, required=True,
        index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True, index=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse', required=True,
        domain="[('company_id','=',company_id)]")
    location_id = fields.Many2one(
        'stock.location', string='Location',
        domain="[('usage','=','internal'),('warehouse_id','=',warehouse_id)]")
    count_type = fields.Selection(
        [('full', 'Full Count'), ('cycle', 'Cycle Count'),
         ('category', 'Category Count')],
        string='Count Type', default='full', required=True)
    product_category_id = fields.Many2one(
        'product.category', string='Product Category',
        domain="[('id','child_of',product_category_id)]")
    state = fields.Selection(
        [('draft', 'Draft'), ('counting', 'Counting'),
         ('pending', 'Pending Approval'), ('approved', 'Approved'),
         ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, tracking=True, index=True)
    user_id = fields.Many2one(
        'res.users', string='Counted By', default=lambda self: self.env.user)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    notes = fields.Text(string='Notes')
    is_locked = fields.Boolean(string='Stock Frozen', default=False, copy=False)
    tolerance_amount = fields.Monetary(
        string='Value Tolerance', currency_field='currency_id',
        default=lambda self: self.env.company.vuma_stock_variance_threshold
        or 1000.0,
        help='A line above this value difference needs a second count.')
    tolerance_percent = fields.Float(
        string='Quantity Tolerance (%)', default=2.0,
        help='A line above this percentage difference needs a second count.')
    line_ids = fields.One2many(
        'vuma.stock.count.line', 'count_id', string='Count Lines', copy=True)
    line_count = fields.Integer(compute='_compute_progress')
    counted_count = fields.Integer(compute='_compute_progress')
    difference_count = fields.Integer(compute='_compute_progress')
    difference_value = fields.Monetary(
        string='Difference Value', compute='_compute_progress',
        currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)

    @api.depends('line_ids.is_counted', 'line_ids.difference', 'line_ids.cost')
    def _compute_progress(self):
        for count in self:
            count.line_count = len(count.line_ids)
            count.counted_count = len(count.line_ids.filtered('is_counted'))
            diff = count.line_ids.filtered(lambda l: l.is_counted and l.difference)
            count.difference_count = len(diff)
            count.difference_value = sum(
                (line.difference or 0.0) * (line.cost or 0.0) for line in diff)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.stock.count') or _('New')
        return super().create(vals_list)

    def action_load_quants(self):
        for count in self:
            if count.state not in ('draft', 'counting'):
                raise UserError(_('Lines can only be loaded on a draft count.'))
            count.line_ids.unlink()
            domain = [
                ('company_id', '=', count.company_id.id),
                ('location_id.usage', '=', 'internal'),
            ]
            if count.warehouse_id:
                domain.append(('location_id.warehouse_id', '=', count.warehouse_id.id))
            if count.location_id:
                domain.append(('location_id', 'child_of', count.location_id.id))
            if count.product_category_id:
                domain.append(('product_id.categ_id', 'child_of',
                               count.product_category_id.id))
            quants = self.env['stock.quant'].search(domain)
            lines = []
            seen = set()
            for quant in quants:
                key = (quant.product_id.id, quant.location_id.id, quant.lot_id.id or 0)
                if key in seen:
                    continue
                seen.add(key)
                lines.append(Command.create({
                    'product_id': quant.product_id.id,
                    'location_id': quant.location_id.id,
                    'lot_id': quant.lot_id.id or False,
                    'system_qty': quant.quantity,
                    'counted_qty': quant.quantity,
                    'uom_id': quant.product_id.uom_id.id,
                    'cost': quant.product_id.standard_price,
                    'is_counted': False,
                }))
            count.line_ids = lines
            count.state = 'counting'
        return True

    def _line_requires_recount(self, line):
        self.ensure_one()
        if not line.difference:
            return False
        absolute_value = abs((line.difference or 0.0) * (line.cost or 0.0))
        base_qty = abs(line.system_qty or 0.0)
        percentage = (abs(line.difference) / base_qty * 100.0) if base_qty else 100.0
        return (absolute_value > (self.tolerance_amount or 0.0)
                or percentage > (self.tolerance_percent or 0.0))

    def _validate_count_lines(self):
        self.ensure_one()
        for line in self.line_ids.filtered(lambda l: l.is_counted and l.difference):
            if not line.reason:
                raise UserError(_(
                    'Choose a reason for the variance on %s.',
                    line.product_id.display_name))
            if not self._line_requires_recount(line):
                continue
            if not line.is_recounted:
                raise UserError(_(
                    '%(product)s is above tolerance and needs a second count.',
                    product=line.product_id.display_name))
            if not line.recounted_by_id:
                raise UserError(_(
                    'Record who performed the second count for %s.',
                    line.product_id.display_name))
            if line.counted_by_id and line.counted_by_id == line.recounted_by_id:
                raise UserError(_(
                    'The first and second count of %s must be done by two '
                    'different people.', line.product_id.display_name))

    def action_lock(self):
        self.write({'state': 'counting', 'is_locked': True})
        return True

    def action_submit(self):
        for count in self:
            if not count.line_ids:
                raise UserError(_('Load the count lines first.'))
            missing = count.line_ids.filtered(lambda l: not l.is_counted)
            if missing:
                raise UserError(_(
                    '%s line(s) still need to be counted.',
                    len(missing)))
            count._validate_count_lines()
            count.state = 'pending'
            self.env['vuma.audit.log'].log(count, 'approve', note='Count submitted')
        return True

    def action_approve(self):
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_ops_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Stock count approval belongs to the warehouse manager.'))
        for count in self:
            if count.state != 'pending':
                raise UserError(_('Only a submitted count can be approved.'))
            count._validate_count_lines()
            for line in count.line_ids.filtered('difference'):
                final_qty = line.recount_qty if line.is_recounted else line.counted_qty
                quant = self.env['stock.quant'].with_context(
                    inventory_mode=True).search([
                        ('product_id', '=', line.product_id.id),
                        ('location_id', '=', line.location_id.id),
                        ('lot_id', '=', line.lot_id.id or False),
                    ], limit=1)
                if quant:
                    quant.inventory_quantity = final_qty
                    quant.action_apply_inventory()
                else:
                    quant = self.env['stock.quant'].with_context(
                        inventory_mode=True).create({
                            'product_id': line.product_id.id,
                            'location_id': line.location_id.id,
                            'lot_id': line.lot_id.id or False,
                            'inventory_quantity': final_qty,
                        })
                    quant.action_apply_inventory()
            count.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
                'is_locked': False,
            })
            self.env['vuma.audit.log'].log(
                count, 'approve', note='Difference value %.2f' % count.difference_value)
        return True

    def action_cancel(self):
        self.write({'state': 'cancelled', 'is_locked': False})
        return True


class VumaStockCountLine(models.Model):
    _name = 'vuma.stock.count.line'
    _description = 'Stock Count Line'
    _order = 'count_id, location_id, product_id'

    count_id = fields.Many2one(
        'vuma.stock.count', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, ondelete='restrict')
    location_id = fields.Many2one(
        'stock.location', string='Location', required=True)
    lot_id = fields.Many2one('stock.lot', string='Lot / Batch')
    uom_id = fields.Many2one('uom.uom', string='Unit', required=True)
    system_qty = fields.Float(
        string='System Qty', digits='Product Unit of Measure', readonly=True)
    counted_qty = fields.Float(
        string='Counted Qty', digits='Product Unit of Measure')
    recount_qty = fields.Float(
        string='Recount Qty', digits='Product Unit of Measure')
    is_recounted = fields.Boolean(string='Second Count Done')
    counted_by_id = fields.Many2one(
        'res.users', string='Counted By', readonly=True, copy=False)
    recounted_by_id = fields.Many2one(
        'res.users', string='Recounted By', readonly=True, copy=False)
    difference = fields.Float(
        string='Difference', compute='_compute_difference', store=True,
        digits='Product Unit of Measure')
    cost = fields.Monetary(
        string='Cost', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='count_id.currency_id', store=True, readonly=True)
    value_difference = fields.Monetary(
        string='Value Difference', compute='_compute_difference', store=True,
        currency_field='currency_id')
    reason = fields.Selection(
        [('shrinkage', 'Shrinkage'), ('damage', 'Damage'),
         ('expiry', 'Expiry'), ('sample', 'Sample'), ('error', 'Count Error'),
         ('other', 'Other')],
        string='Reason')
    is_counted = fields.Boolean(string='Counted')
    note = fields.Char(string='Note')

    @api.depends('system_qty', 'counted_qty', 'recount_qty', 'is_recounted',
                 'cost')
    def _compute_difference(self):
        for line in self:
            final_qty = line.recount_qty if line.is_recounted else line.counted_qty
            line.difference = (final_qty or 0.0) - (line.system_qty or 0.0)
            line.value_difference = line.difference * (line.cost or 0.0)

    def write(self, vals):
        if vals.get('is_counted') and not vals.get('counted_by_id'):
            vals['counted_by_id'] = self.env.user.id
        if vals.get('is_recounted') and not vals.get('recounted_by_id'):
            vals['recounted_by_id'] = self.env.user.id
        return super().write(vals)

    @api.onchange('product_id')
    def _onchange_product_id(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id
            self.cost = self.product_id.standard_price


class VumaCycleCountPlan(models.Model):
    _name = 'vuma.cycle.count.plan'
    _description = 'Cycle Count Plan'
    _inherit = ['mail.thread']
    _order = 'next_date, id'

    name = fields.Char(string='Plan', required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Warehouse', required=True,
        domain="[('company_id','=',company_id)]")
    product_category_id = fields.Many2one(
        'product.category', string='Product Category')
    frequency = fields.Selection(
        [('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
         ('annual', 'Annual')],
        string='Frequency', default='monthly', required=True)
    next_date = fields.Date(
        string='Next Count', default=fields.Date.context_today, required=True)
    responsible_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user)
    active = fields.Boolean(default=True)
    notes = fields.Text(string='Notes')

    def action_create_count(self):
        self.ensure_one()
        count = self.env['vuma.stock.count'].create({
            'date': self.next_date,
            'company_id': self.company_id.id,
            'warehouse_id': self.warehouse_id.id,
            'count_type': 'cycle',
            'product_category_id': self.product_category_id.id,
            'user_id': self.responsible_id.id or self.env.user.id,
            'notes': _('Generated from cycle plan %s', self.name),
        })
        count.action_load_quants()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'vuma.stock.count',
            'view_mode': 'form',
            'res_id': count.id,
        }

    @api.model
    def _cron_generate_due_counts(self):
        today = fields.Date.context_today(self)
        plans = self.search([('active', '=', True), ('next_date', '<=', today)])
        for plan in plans:
            plan.action_create_count()
            if plan.frequency == 'monthly':
                plan.next_date = fields.Date.add(plan.next_date, months=1)
            elif plan.frequency == 'quarterly':
                plan.next_date = fields.Date.add(plan.next_date, months=3)
            else:
                plan.next_date = fields.Date.add(plan.next_date, months=12)


class VumaPickingSlip(models.Model):
    _inherit = 'vuma.picking.slip'

    town = fields.Char(string='Town / Route', index=True)
    route_date = fields.Date(string='Route Date')
    driver_id = fields.Many2one('res.users', string='Driver')
    vehicle = fields.Char(string='Vehicle')

    @api.model
    def _vuma_town_for_invoices(self, invoices):
        towns = sorted({
            (invoice.partner_id.city or '').strip()
            for invoice in invoices
            if (invoice.partner_id.city or '').strip()
        })
        return ', '.join(towns) if towns else _('Unassigned')

    @api.model
    def create_from_invoices(self, invoices):
        slip = super().create_from_invoices(invoices)
        slip.town = self._vuma_town_for_invoices(invoices)
        return slip


class AccountMovePickingTown(models.Model):
    _inherit = 'account.move'

    def action_vuma_create_slips_by_town(self):
        """Create one consolidated picking slip per customer town."""
        invoices = self._get_picking_slip_invoices()
        if not invoices:
            raise UserError(_('Select at least one customer invoice.'))
        groups = defaultdict(lambda: self.env['account.move'])
        for invoice in invoices:
            town = (invoice.partner_id.city or '').strip() or _('Unassigned')
            groups[town] |= invoice
        slips = self.env['vuma.picking.slip']
        for town, town_invoices in sorted(groups.items()):
            slip = self.env['vuma.picking.slip'].create_from_invoices(town_invoices)
            slip.town = town
            slips |= slip
        return {
            'type': 'ir.actions.act_window',
            'name': _('Picking Slips by Town'),
            'res_model': 'vuma.picking.slip',
            'view_mode': 'list,form',
            'domain': [('id', 'in', slips.ids)],
        }


class StockPickingReceiving(models.Model):
    _inherit = 'stock.picking'

    vuma_receiving_notes = fields.Text(string='Receiving Notes')
    vuma_has_damage = fields.Boolean(
        string='Damage / Shortage Recorded', compute='_compute_vuma_damage',
        store=True)
    vuma_claim_ids = fields.One2many(
        'vuma.supplier.claim', 'picking_id', string='Supplier Claims')

    @api.depends('move_ids.vuma_damaged_qty', 'move_ids.quantity',
                 'move_ids.product_uom_qty', 'state')
    def _compute_vuma_damage(self):
        for picking in self:
            picking.vuma_has_damage = any(
                (move.vuma_damaged_qty or 0.0) > 0
                or (move.quantity or 0.0) < (move.product_uom_qty or 0.0)
                for move in picking.move_ids
            ) if picking.state == 'done' else any(
                (move.vuma_damaged_qty or 0.0) > 0 for move in picking.move_ids)

    def action_vuma_create_claim(self):
        self.ensure_one()
        if not self.picking_type_id.code == 'incoming':
            raise UserError(_('Supplier claims are raised from goods receipts.'))
        lines = []
        for move in self.move_ids:
            short = max(0.0, (move.product_uom_qty or 0.0) - (move.quantity or 0.0))
            damaged = move.vuma_damaged_qty or 0.0
            qty = short + damaged
            if qty <= 0:
                continue
            lines.append(Command.create({
                'product_id': move.product_id.id,
                'description': move.product_id.display_name,
                'quantity': qty,
                'uom_id': move.uom_id.id,
                'unit_price': move.price_unit or move.product_id.standard_price,
                'received_qty': move.quantity,
                'invoiced_qty': move.product_uom_qty,
            }))
        if not lines:
            raise UserError(_('This receipt has no shortage or damage to claim.'))
        claim = self.env['vuma.supplier.claim'].create({
            'supplier_id': self.partner_id.id,
            'company_id': self.company_id.id,
            'purchase_id': self.purchase_id.id or False,
            'picking_id': self.id,
            'claim_type': 'short',
            'description': _('Shortage / damage on receipt %s', self.name),
            'line_ids': lines,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Supplier Claim'),
            'res_model': 'vuma.supplier.claim',
            'view_mode': 'form',
            'res_id': claim.id,
        }


class StockMoveReceiving(models.Model):
    _inherit = 'stock.move'

    vuma_damaged_qty = fields.Float(
        string='Damaged Qty', digits='Product Unit of Measure',
        help='Quantity received damaged and not accepted into sellable stock.')
    vuma_rejection_reason = fields.Char(string='Damage / Rejection Reason')
