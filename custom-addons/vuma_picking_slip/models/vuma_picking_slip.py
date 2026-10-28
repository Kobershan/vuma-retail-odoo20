# -*- coding: utf-8 -*-
"""Saved picking sessions built from one or more customer invoices.

The invoice selection is turned into an ordinary Odoo record so the warehouse
can keep the slip open on a tablet, enter the quantity actually picked on each
line, and finish the trip. The record is deliberately independent from stock:
it never reserves or moves anything.
"""
from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class VumaPickingSlip(models.Model):
    _name = 'vuma.picking.slip'
    _description = 'Consolidated Picking Slip'
    _order = 'date desc, id desc'

    name = fields.Char(
        string='Reference', default=lambda self: _('New'),
        readonly=True, copy=False, required=True)
    date = fields.Date(
        default=fields.Date.context_today, required=True, index=True)
    user_id = fields.Many2one(
        'res.users', string='Prepared by',
        default=lambda self: self.env.user, required=True)
    company_id = fields.Many2one(
        'res.company', string='Company',
        default=lambda self: self.env.company, required=True, index=True)
    invoice_ids = fields.Many2many(
        'account.move', 'vuma_picking_slip_invoice_rel',
        'slip_id', 'invoice_id', string='Invoices',
        readonly=True, copy=False)
    line_ids = fields.One2many(
        'vuma.picking.slip.line', 'slip_id', string='Picking Lines',
        copy=True)
    state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('in_progress', 'Picking'),
            ('done', 'Done'),
        ],
        string='Status', default='draft', required=True,
        copy=False, index=True)
    note = fields.Text(string='Notes')
    finished_date = fields.Datetime(
        string='Finished on', readonly=True, copy=False)
    finished_by_id = fields.Many2one(
        'res.users', string='Finished by', readonly=True, copy=False)

    invoice_count = fields.Integer(compute='_compute_progress')
    line_count = fields.Integer(compute='_compute_progress')
    picked_count = fields.Integer(compute='_compute_progress')
    short_count = fields.Integer(compute='_compute_progress')
    progress = fields.Float(
        string='Progress (%)', compute='_compute_progress')

    source_company_ids = fields.Many2many(
        'res.company', string='Source companies',
        compute='_compute_company_info')
    multi_company = fields.Boolean(
        string='Multiple companies', compute='_compute_company_info')

    @api.depends(
        'invoice_ids', 'line_ids.is_picked',
        'line_ids.picked_qty', 'line_ids.demand_qty')
    def _compute_progress(self):
        for slip in self:
            lines = slip.line_ids
            picked = lines.filtered('is_picked')
            slip.invoice_count = len(slip.invoice_ids)
            slip.line_count = len(lines)
            slip.picked_count = len(picked)
            slip.short_count = len(picked.filtered(
                lambda line: line.picked_qty < line.demand_qty))
            slip.progress = (len(picked) * 100.0 / len(lines)) if lines else 0.0

    @api.depends('line_ids.source_company_id')
    def _compute_company_info(self):
        for slip in self:
            companies = slip.line_ids.source_company_id
            slip.source_company_ids = companies
            slip.multi_company = len(companies) > 1

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('name') or vals['name'] == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.picking.slip') or _('New')
        return super().create(vals_list)

    @api.model
    def create_from_invoices(self, invoices):
        """Create and return a saved slip for the selected customer invoices."""
        data = invoices.get_consolidated_picking_data()
        slip = self.create({
            'invoice_ids': [Command.set(data.invoices.ids)],
        })
        line_model = self.env['vuma.picking.slip.line']
        for index, line in enumerate(data.lines, start=1):
            line_model.create({
                'slip_id': slip.id,
                'sequence': index * 10,
                'product_id': line.product.id,
                'product_uom_id': line.uom.id,
                'demand_qty': line.quantity,
                'source_company_id': line.company.id,
                'source_label': line.source_label or False,
            })
        return slip

    def action_start(self):
        """Mark the slip as actively being picked."""
        for slip in self:
            if slip.state == 'draft':
                slip.state = 'in_progress'
        return True

    def action_mark_all_picked(self):
        """Convenience for a complete pick: everything present and picked."""
        for slip in self:
            if not slip.line_ids:
                raise UserError(_('This picking slip has no lines to pick.'))
            for line in slip.line_ids:
                line.write({
                    'picked_qty': line.demand_qty,
                    'is_picked': True,
                })
            if slip.state != 'done':
                slip.state = 'in_progress'
        return True

    def action_finish(self):
        """Close the slip, but only once every line has been dealt with.

        A line can be marked picked with a quantity lower than the demand: that
        is how a short pick is recorded. What cannot happen is finishing while
        lines are still untouched, because the warehouse would lose track of
        them.
        """
        for slip in self:
            if not slip.line_ids:
                raise UserError(_('This picking slip has no lines to pick.'))
            remaining = slip.line_ids.filtered(lambda line: not line.is_picked)
            if remaining:
                raise UserError(_(
                    '%(count)s line(s) still need to be marked as picked. '
                    'Enter the quantity found and tap Picked on each line '
                    'before finishing.',
                    count=len(remaining),
                ))
            slip.write({
                'state': 'done',
                'finished_date': fields.Datetime.now(),
                'finished_by_id': self.env.user.id,
            })
        return True

    def action_reopen(self):
        """Put a finished slip back into picking mode."""
        for slip in self:
            slip.write({
                'state': 'in_progress',
                'finished_date': False,
                'finished_by_id': False,
            })
        return True

    def action_print_pdf(self):
        """Print the slip, including the quantities captured so far."""
        self.ensure_one()
        report = self.env.ref(
            'vuma_picking_slip.action_report_picking_slip')
        return report.report_action(self, config=False)

    def action_open_tablet(self):
        """Open the large-button picking screen used on warehouse tablets."""
        self.ensure_one()
        return {
            'type': 'ir.actions.client',
            'tag': 'vuma_picking_slip.tablet',
            'name': self.name,
            'params': {'slip_id': self.id},
        }

    def action_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices on %(slip)s', slip=self.name),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.invoice_ids.ids)],
        }


class VumaPickingSlipLine(models.Model):
    _name = 'vuma.picking.slip.line'
    _description = 'Consolidated Picking Slip Line'
    _order = 'slip_id, sequence, id'

    slip_id = fields.Many2one(
        'vuma.picking.slip', string='Picking Slip',
        required=True, ondelete='cascade', index=True)
    sequence = fields.Integer(default=10)
    product_id = fields.Many2one(
        'product.product', string='Product',
        required=True, ondelete='restrict')
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True)
    demand_qty = fields.Float(
        string='Ordered', digits='Product Unit of Measure', required=True)
    picked_qty = fields.Float(
        string='Picked Qty', digits='Product Unit of Measure')
    is_picked = fields.Boolean(string='Picked')
    source_company_id = fields.Many2one(
        'res.company', string='Company')
    source_label = fields.Char(string='Source')
    product_barcode = fields.Char(
        related='product_id.barcode', string='Barcode', readonly=True)
    remaining_qty = fields.Float(
        string='Remaining', compute='_compute_remaining_qty',
        digits='Product Unit of Measure')
    is_short = fields.Boolean(
        string='Short', compute='_compute_remaining_qty')

    @api.depends('demand_qty', 'picked_qty', 'is_picked')
    def _compute_remaining_qty(self):
        for line in self:
            line.remaining_qty = max(line.demand_qty - line.picked_qty, 0.0)
            line.is_short = bool(
                line.is_picked and line.picked_qty < line.demand_qty)

    @api.constrains('demand_qty', 'picked_qty')
    def _check_quantities(self):
        for line in self:
            if line.demand_qty <= 0:
                raise ValidationError(_(
                    'The ordered quantity on a picking line must be positive.'))
            if line.picked_qty < 0:
                raise ValidationError(_(
                    'The picked quantity cannot be negative.'))

    def write(self, vals):
        result = super().write(vals)
        if {'picked_qty', 'is_picked'} & set(vals):
            for slip in self.slip_id:
                if slip.state == 'draft':
                    slip.state = 'in_progress'
                elif slip.state == 'done' and not all(
                        slip.line_ids.mapped('is_picked')):
                    slip.write({
                        'state': 'in_progress',
                        'finished_date': False,
                        'finished_by_id': False,
                    })
        return result
