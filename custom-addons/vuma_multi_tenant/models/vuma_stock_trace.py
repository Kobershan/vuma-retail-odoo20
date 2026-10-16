# -*- coding: utf-8 -*-
"""
Stock Traceability Audit.

Answers one question, on demand: *which company owns this stock, and where
does it live?* Every row joins a stock quant to the location that gives it
its company (``stock.quant.company_id`` is derived from
``location_id.company_id``), so nothing on the shelf stays unattributable.

Three checks:

* ``orphan``       -- on-hand stock in a location with **no company**. It
  cannot be invoiced to anybody, so it is not traceable. Repairable.
* ``shared``       -- one product stocked by BOTH trading companies, so the
  rep must pick a stock-source company on each order line.
* ``untraced_line``-- a sale order line with a product but no stock-source
  company; the basket cannot be split without guessing.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class VumaStockTrace(models.TransientModel):
    """Transient stock-traceability audit screen."""
    _name = 'vuma.stock.trace'
    _description = 'Stock Company Traceability Audit'

    state = fields.Selection([
        ('orphan', 'Stock Without Company'),
        ('shared', 'Stock In Both Companies'),
        ('untraced_line', 'Order Lines Without Source'),
    ], string='Check', required=True, default='orphan',
        help="Which traceability check to run.")

    repair_company_id = fields.Many2one(
        'res.company', string='Repair Into Company',
        help="Company that will own company-less stock when repaired.")

    line_ids = fields.One2many(
        'vuma.stock.trace.line', 'parent_id', string='Results')

    traceable_count = fields.Integer(
        string='Traceable', compute='_compute_counts')
    untraceable_count = fields.Integer(
        string='Not Traceable', compute='_compute_counts')

    @api.depends('line_ids.traceable')
    def _compute_counts(self):
        for rec in self:
            rec.traceable_count = len(rec.line_ids.filtered('traceable'))
            rec.untraceable_count = len(rec.line_ids) - rec.traceable_count

    # ------------------------------------------------------------------
    def _vuma_report_company_ids(self):
        return self.env['sale.order.line']._vuma_mixed_basket_companies()

    def action_run_audit(self):
        """Populate the results for the selected check."""
        self.ensure_one()
        self.line_ids.unlink()
        Quant = self.env['stock.quant'].sudo()

        if self.state == 'orphan':
            quants = Quant.search([
                ('quantity', '>', 0),
                ('location_id.company_id', '=', False),
                ('location_id.usage', 'in', ('internal', 'view')),
            ])
            self.line_ids = self._vuma_rows_from_quants(quants)

        elif self.state == 'shared':
            companies = self._vuma_report_company_ids()
            quants = Quant.search([
                ('quantity', '>', 0),
                ('location_id.company_id', 'in', companies.ids),
                ('location_id.usage', '=', 'internal'),
            ])
            rows = self._vuma_rows_from_quants(quants)
            holders = {}
            for row in rows:
                holders.setdefault(row.product_id.id, set()).add(
                    row.quant_company_id.id)
            multi = {pid for pid, comps in holders.items() if len(comps) > 1}
            self.line_ids = rows.filtered(lambda r: r.product_id.id in multi)

        else:  # untraced_line
            SOL = self.env['sale.order.line'].sudo()
            untraced = SOL.search([
                ('product_id', '!=', False),
                ('vuma_source_company_id', '=', False),
            ])
            self.line_ids = self._vuma_rows_from_untraced(untraced)

        return self._vuma_open_results()

    def _vuma_rows_from_quants(self, quants):
        self.ensure_one()
        return self.env['vuma.stock.trace.line'].create([{
            'parent_id': self.id,
            'product_id': quant.product_id.id,
            'location_id': quant.location_id.id,
            'quant_company_id': quant.location_id.company_id.id,
            'product_company_id': quant.product_id.product_tmpl_id.company_id.id,
            'quantity': quant.quantity,
            'available_quantity': quant.available_quantity,
            'reference': quant.location_id.complete_name,
            'traceable': bool(quant.location_id.company_id),
        } for quant in quants])

    def _vuma_rows_from_untraced(self, sale_lines):
        self.ensure_one()
        return self.env['vuma.stock.trace.line'].create([{
            'parent_id': self.id,
            'product_id': sol.product_id.id,
            'product_company_id': sol.product_id.product_tmpl_id.company_id.id,
            'quantity': sol.product_uom_qty,
            'reference': _('%s / %s') % (sol.order_id.display_name,
                                         sol.product_id.display_name),
            'traceable': False,
        } for sol in sale_lines])

    def _vuma_open_results(self):
        self.ensure_one()
        return {
            'name': _('Stock Traceability — %s') % dict(
                self._fields['state']._description_selection(self.env)).get(
                    self.state, self.state),
            'type': 'ir.actions.act_window',
            'res_model': 'vuma.stock.trace.line',
            'view_mode': 'list,form',
            'domain': [('parent_id', '=', self.id)],
            'target': 'new',
            'context': dict(self.env.context, create=False),
        }

    # ------------------------------------------------------------------
    def action_backfill_order_lines(self):
        """Fill the missing stock-source company on every order line.

        Uses the same resolution logic as the live UI, so the backfill
        matches what a rep would have picked.
        """
        lines = self.env['sale.order.line'].search([
            ('product_id', '!=', False),
            ('vuma_source_company_id', '=', False),
        ])
        if not lines:
            raise UserError(_('Every order line already has a stock source.'))
        lines._vuma_apply_source_company()
        remaining = lines.filtered(lambda l: not l.vuma_source_company_id)
        return {
            'name': _('Backfill Complete — %d line(s) still unassigned') % len(remaining),
            'type': 'ir.actions.act_window',
            'res_model': 'vuma.stock.trace.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', remaining.ids)],
            'target': 'new',
            'context': dict(self.env.context, create=False),
        }

    def action_repair_stock_company(self):
        """Give company-less stock an owner.

        Moves the orphaned stock under the chosen company's warehouse so its
        location (and therefore the quant's company) becomes set.
        """
        self.ensure_one()
        company = self.repair_company_id
        if not company:
            raise UserError(_('Pick the company that will own this stock.'))
        targets = []
        for row in self.line_ids:
            location = row.location_id
            if not location or location.company_id:
                continue
            warehouse = self.env['stock.warehouse'].sudo().search(
                [('company_id', '=', company.id)], limit=1)
            if not warehouse or not warehouse.lot_stock_id:
                raise UserError(_('%s has no warehouse stock location.') %
                                company.display_name)
            if location.complete_name == warehouse.lot_stock_id.complete_name:
                continue
            location.write({'location_id': warehouse.lot_stock_id.id})
            row.write({
                'quant_company_id': company.id,
                'traceable': True,
            })
            targets.append(location.complete_name)
        return {
            'type': 'ir.actions.act_window_close',
        }


class VumaStockTraceLine(models.TransientModel):
    """One audit result row."""
    _name = 'vuma.stock.trace.line'
    _description = 'Stock Company Traceability Result'
    _order = 'product_id, location_id, id'

    parent_id = fields.Many2one('vuma.stock.trace', string='Audit',
                                ondelete='cascade', index=True)
    product_id = fields.Many2one('product.product', string='Product')
    location_id = fields.Many2one('stock.location', string='Stock Location')
    quant_company_id = fields.Many2one('res.company', string='Stock Owner')
    product_company_id = fields.Many2one('res.company', string='Product Owner')
    company_name = fields.Char(string='Stock Owner Name',
                               compute='_compute_company_name', store=True)
    quantity = fields.Float(string='On Hand')
    available_quantity = fields.Float(string='Available')
    reference = fields.Char(string='Line / Reference')
    traceable = fields.Boolean(
        string='Traceable',
        help="True when this stock can be attributed to a company.")

    @api.depends('quant_company_id')
    def _compute_company_name(self):
        for rec in self:
            rec.company_name = (rec.quant_company_id.display_name
                                if rec.quant_company_id else _('NO COMPANY'))