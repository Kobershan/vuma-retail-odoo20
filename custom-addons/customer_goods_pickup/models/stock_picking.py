from odoo import _, api, fields, models


class StockPicking(models.Model):
    """Link the incoming transfer back to the goods receipt that made it.

    Only a computed lookup is added: the stock side owns the transfer, the
    goods side owns the decision, and the two are tied together by the transfer
    itself rather than by the module rewriting anything stock does.
    """

    _inherit = 'stock.picking'

    goods_receipt_id = fields.Many2one(
        'customer.goods.receipt', string='Goods Store Receipt',
        compute='_compute_goods_receipt_id', store=True,
        help='The store receipt this transfer was created for.',
    )
    goods_pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Goods Pickup',
        related='goods_receipt_id.pickup_id', store=True,
        help='The customer goods pickup the goods belong to.',
    )
    goods_invoice_number = fields.Char(
        string='Invoice Number', related='goods_receipt_id.invoice_number',
        store=True,
    )

    @api.depends('move_ids.goods_move_line_ids')
    def _compute_goods_receipt_id(self):
        for picking in self:
            picking.goods_receipt_id = picking.move_ids.goods_move_line_ids \
                .mapped('receipt_id')[:1]

    def action_view_goods_receipt(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Store Receipt'),
            'res_model': 'customer.goods.receipt',
            'view_mode': 'form',
            'res_id': self.goods_receipt_id.id,
            'target': 'current',
        }

    def action_view_goods_pickup(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Goods Pickup'),
            'res_model': 'customer.goods.pickup',
            'view_mode': 'form',
            'res_id': self.goods_pickup_id.id,
            'target': 'current',
        }


class StockMove(models.Model):
    """Tie each incoming move to the receipt line whose quantity it moves."""

    _inherit = 'stock.move'

    goods_move_line_ids = fields.One2many(
        'customer.goods.receipt.line', 'stock_move_id', string='Goods Receipt Lines',
    )