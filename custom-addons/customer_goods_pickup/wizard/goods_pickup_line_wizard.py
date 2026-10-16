from odoo import _, api, fields, models
from odoo.exceptions import UserError

from ..units import is_zero as _qty_is_zero


class CustomerGoodsPickupAddLines(models.TransientModel):
    """Tick the invoice lines the representative is taking goods off.

    The list is built from the invoice itself and shows, per line, how much is
    still collectable once earlier credit notes and other open pickups have
    had their share. Lines with nothing left are shown greyed out rather than
    hidden, so a rep can see *why* a product they were told to collect cannot
    be collected.
    """

    _name = 'customer.goods.pickup.add.lines'
    _description = 'Add Invoice Lines To A Customer Goods Pickup'

    pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Pickup', required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    company_id = fields.Many2one(
        'res.company', related='pickup_id.company_id', store=True, readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner', related='pickup_id.partner_id', readonly=True,
    )
    invoice_id = fields.Many2one(
        'account.move', related='pickup_id.invoice_id', string='Invoice',
        readonly=True,
    )
    invoice_number = fields.Char(
        related='pickup_id.invoice_number', string='Invoice Number', readonly=True,
    )
    line_ids = fields.One2many(
        'customer.goods.pickup.add.lines.line', 'wizard_id',
        string='Invoice Lines',
    )
    picked_count = fields.Integer(compute='_compute_picked_count')
    picked_qty = fields.Float(compute='_compute_picked_count', string='Total To Collect')

    @api.model_create_multi
    def create(self, vals_list):
        """Offer every product line of the invoice with its collectable amount.

        Built on create rather than in ``default_get`` so the button, the
        ``active_id`` binding and scripted calls all get the same list.
        """
        for vals in vals_list:
            pickup = self.env['customer.goods.pickup'].browse(
                vals.get('pickup_id') or self.env.context.get('active_id'))
            if not pickup:
                raise UserError(_('No goods pickup was given.'))
            if pickup.state != 'draft':
                raise UserError(
                    _('%(name)s is %(state)s; lines can only be added while the '
                      'pickup is a draft.',
                      name=pickup.name, state=pickup._state_label())
                )
            existing = pickup.line_ids.mapped('invoice_line_id')
            credited = pickup._get_invoice_line_credited_map()
            reserved = pickup._get_invoice_line_reserved_map()
            commands, index = [], 10
            for invoice_line in pickup.invoice_id.invoice_line_ids:
                if invoice_line.display_type != 'product' \
                        or not invoice_line.product_id:
                    continue
                eligible = (invoice_line.quantity or 0.0) \
                    - credited.get(invoice_line.id, 0.0) \
                    - reserved.get(invoice_line.id, 0.0)
                commands.append((0, 0, {
                    'invoice_line_id': invoice_line.id,
                    'product_id': invoice_line.product_id.id,
                    'name': invoice_line.name,
                    'product_uom_id': invoice_line.product_uom_id.id,
                    'price_unit': invoice_line.price_unit,
                    'discount': invoice_line.discount,
                    'return_reason': pickup.pickup_reason,
                    'qty_collected': 0.0,
                    'already_added': invoice_line.id in existing.ids,
                    'eligible_qty': max(eligible, 0.0),
                    'sequence': index,
                }))
                index += 10
            vals['line_ids'] = commands
        return super().create(vals_list)

    @api.depends('line_ids.selected', 'line_ids.qty_collected')
    def _compute_picked_count(self):
        for wizard in self:
            picked = wizard.line_ids.filtered('selected')
            wizard.picked_count = len(picked)
            wizard.picked_qty = sum(picked.mapped('qty_collected'))

    def action_add_lines(self):
        self.ensure_one()
        picked = self.line_ids.filtered(
            lambda line: line.selected and not line.already_added)
        if not picked:
            raise UserError(
                _('Tick at least one invoice line to add to the pickup.'))
        for line in picked:
            if _qty_is_zero(self.env, line.qty_collected):
                raise UserError(
                    _('Enter a quantity for "%(product)s", or untick the line.',
                      product=line.product_id.display_name)
                )
            if line.qty_collected > line.eligible_qty:
                raise UserError(
                    _('%(product)s: only %(eligible)s %(uom)s are still collectable '
                      'on invoice %(invoice)s. You entered %(asked)s.',
                      product=line.product_id.display_name,
                      eligible=line.product_uom_id.round(line.eligible_qty),
                      uom=line.product_uom_id.name,
                      invoice=self.invoice_number,
                      asked=line.product_uom_id.round(line.qty_collected))
                )
        commands = []
        sequence = max(self.pickup_id.line_ids.mapped('sequence') or [0]) + 10
        for line in picked:
            commands.append((0, 0, {
                'invoice_line_id': line.invoice_line_id.id,
                'product_id': line.product_id.id,
                'name': line.name,
                'product_uom_id': line.product_uom_id.id,
                'qty_collected': line.qty_collected,
                'return_reason': line.return_reason,
                'sequence': sequence,
            }))
            sequence += 10
        self.pickup_id.write({'line_ids': commands})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Goods Pickup'),
            'res_model': 'customer.goods.pickup',
            'view_mode': 'form',
            'res_id': self.pickup_id.id,
            'target': 'current',
        }


class CustomerGoodsPickupAddLinesLine(models.TransientModel):
    """One invoice line offered to the representative."""

    _name = 'customer.goods.pickup.add.lines.line'
    _description = 'Invoice Line Offered On A Customer Goods Pickup'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'customer.goods.pickup.add.lines', string='Wizard', required=True,
        ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    invoice_line_id = fields.Many2one(
        'account.move.line', string='Invoice Line', required=True,
        ondelete='restrict',
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, readonly=True,
    )
    name = fields.Text(string='Description')
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True, readonly=True,
    )
    invoiced_qty = fields.Float(
        related='invoice_line_id.quantity', string='Invoiced', digits='Product Unit',
        readonly=True,
    )
    credited_qty = fields.Float(
        string='Already Credited', digits='Product Unit', readonly=True,
        compute='_compute_figures',
    )
    reserved_qty = fields.Float(
        string='On Other Pickups', digits='Product Unit', readonly=True,
        compute='_compute_figures',
    )
    eligible_qty = fields.Float(
        string='Still Collectable', digits='Product Unit', readonly=True,
        compute='_compute_figures',
    )
    price_unit = fields.Float(string='Unit Price', digits='Product Price',
                              readonly=True)
    discount = fields.Float(string='Discount (%)', digits='Discount', readonly=True)
    return_reason = fields.Selection(
        selection=lambda self: self.env['customer.goods.pickup.line']
        ._fields['return_reason'].selection,
        string='Reason for Return',
    )
    selected = fields.Boolean(string='Collect', default=False)
    already_added = fields.Boolean(string='Already On The Pickup', readonly=True)
    qty_collected = fields.Float(
        string='Quantity Collected', digits='Product Unit', default=0.0,
    )

    @api.depends('invoice_line_id')
    def _compute_figures(self):
        for line in self:
            wizard = line.wizard_id
            invoice_line = line.invoice_line_id
            credited_map = wizard.pickup_id._get_invoice_line_credited_map() \
                if wizard.pickup_id else {}
            reserved_map = wizard.pickup_id._get_invoice_line_reserved_map() \
                if wizard.pickup_id else {}
            convert = wizard.pickup_id._convert_qty if wizard.pickup_id else (
                lambda qty, *args: qty)
            uom = invoice_line.product_uom_id
            credited = convert(credited_map.get(invoice_line.id, 0.0), uom, uom)
            reserved = convert(reserved_map.get(invoice_line.id, 0.0), uom, uom)
            line.credited_qty = credited
            line.reserved_qty = reserved
            line.eligible_qty = max((invoice_line.quantity or 0.0) - credited - reserved, 0.0)
        return True

    @api.onchange('selected')
    def _onchange_selected(self):
        for line in self:
            if line.selected and not line.already_added \
                    and _qty_is_zero(self.env, line.qty_collected):
                line.qty_collected = line.eligible_qty