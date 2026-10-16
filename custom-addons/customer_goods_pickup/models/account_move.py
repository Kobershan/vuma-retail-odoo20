from odoo import _, api, fields, models
from odoo.exceptions import UserError


class AccountMove(models.Model):
    """Customer credit notes raised from a store receipt.

    Only two links are added, both purely for traceability:

    * the credit note points at the pickup and the store receipt it came from,
      so the operational side can be walked from the accounting side;
    * each credited line points at the store receipt line it settles, so the
      credited quantity is read from the accounting records rather than from a
      number the module could get wrong.

    The reversal itself is Odoo's own: ``account.move._reverse_moves`` on the
    original invoice, which keeps customer, company, currency, price, discount
    and tax exactly as the invoice had them.
    """

    _inherit = 'account.move'

    goods_pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Goods Pickup', index='btree_not_null',
        copy=False, ondelete='set null',
        help='The customer goods pickup this document was raised from.',
    )
    goods_pickup_receipt_id = fields.Many2one(
        'customer.goods.receipt', string='Store Receipt', index='btree_not_null',
        copy=False, ondelete='set null',
        help='The store receipt whose accepted quantity this credit note settles.',
    )
    goods_pickup_partner_id = fields.Many2one(
        'res.partner', related='goods_pickup_id.partner_id', string='Pickup Customer',
        readonly=True,
    )
    goods_pickup_invoice_number = fields.Char(
        related='goods_pickup_id.invoice_number', string='Pickup Invoice Number',
        readonly=True,
    )

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

    def action_view_goods_receipt(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Store Receipt'),
            'res_model': 'customer.goods.receipt',
            'view_mode': 'form',
            'res_id': self.goods_pickup_receipt_id.id,
            'target': 'current',
        }


class AccountMovePosting(models.Model):
    """Re-check the bound at the moment the money actually moves.

    The creation-time check is what stops two accountants building overlapping
    notes. This is the second half: a note that was cut down while another one
    was still being typed, and then posted later, is measured again here
    against the other notes on the same receipt line. It runs inside
    ``account.move._post`` so it covers the button, the RPC call, a server
    action and the auto-post on invoice registration alike.
    """

    _inherit = 'account.move'

    def _post(self, soft=True):
        moves = super()._post(soft=soft)
        linked = moves.filtered(lambda move: move.move_type == 'out_refund') \
            .line_ids.filtered('goods_pickup_receipt_line_id')
        if linked:
            linked._check_goods_credit_quantity()
        return moves


class AccountMoveLine(models.Model):
    """The link that makes a duplicate credit note impossible.

    Each product line of a credit note raised by this module records exactly
    which store receipt line it settles, so the quantity already credited is
    always read back from the accounting records.

    That link is load-bearing, so the quantity on it is fenced in. Without
    that, an accountant could raise a correct draft note, edit its quantity
    upwards and post it: the module would record the larger figure as
    credited and quietly stop offering the rest for credit, with nothing to
    show that the real acceptance was smaller.
    """

    _inherit = 'account.move.line'

    goods_pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Goods Pickup',
        related='move_id.goods_pickup_id', store=True, index=True, readonly=True,
    )
    goods_pickup_receipt_line_id = fields.Many2one(
        'customer.goods.receipt.line', string='Store Receipt Line',
        index='btree_not_null', copy=False, ondelete='set null',
        help='The store receipt line whose accepted quantity this credit line '
             'settles.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines.filtered('goods_pickup_receipt_line_id')._check_goods_credit_quantity()
        return lines

    def write(self, vals):
        if 'goods_pickup_receipt_line_id' in vals:
            # The link is set once, when the note is built. Moving it later
            # would re-point the credited quantity at a different line of
            # goods, which is exactly the double credit it exists to prevent.
            repointed = self.filtered('goods_pickup_receipt_line_id')
            if repointed:
                raise UserError(
                    _('The store receipt line a credit settles cannot be '
                      'changed: it is what stops the same goods being credited '
                      'twice.'))
        if 'quantity' in vals:
            self.filtered(
                lambda line: line.move_id.state == 'draft'
                and line.goods_pickup_receipt_line_id
            )._check_goods_credit_quantity(vals['quantity'])
        return super().write(vals)

    def _check_goods_credit_quantity(self, wanted=None):
        """Refuse a credit line larger than what the store accepted.

        The bound is "accepted — plus anything a manager released back out of
        quarantine after a second inspection — less everything already credited
        against this receipt line", where *already* covers the line being
        checked so that re-saving it does not count itself twice.

        Refused quantity that is still sitting in quarantine is deliberately
        *not* in that bound. Being in quarantine is a custody fact, not an
        entitlement to money.
        """
        from ..units import compare as _qty_compare, greater as _qty_gt
        from ..units import convert

        for line in self.filtered('goods_pickup_receipt_line_id'):
            receipt_line = line.goods_pickup_receipt_line_id
            others = receipt_line.credit_move_line_ids - line
            settled = 0.0
            for other in others:
                if other.move_id.state not in ('draft', 'posted'):
                    continue
                settled += convert(
                    other.quantity or 0.0, other.product_uom_id,
                    receipt_line.product_uom_id)
            creditable = (receipt_line.qty_accepted
                          + receipt_line.qty_reinspected_accepted)
            ceiling = creditable - settled
            quantity = line.quantity if wanted is None else wanted
            quantity = convert(quantity or 0.0, line.product_uom_id,
                               receipt_line.product_uom_id)
            if _qty_gt(line.env, quantity, ceiling):
                raise UserError(
                    _('%(product)s: only %(eligible)s %(uom)s on %(receipt)s are '
                      'still creditable (%(accepted)s accepted, %(settled)s '
                      'already credited), but this credit note line is for '
                      '%(asked)s.',
                      product=receipt_line.product_id.display_name,
                      eligible=receipt_line.product_uom_id.round(max(ceiling, 0.0)),
                      uom=receipt_line.product_uom_id.name,
                      receipt=receipt_line.receipt_id.name,
                      accepted=receipt_line.product_uom_id.round(creditable),
                      settled=receipt_line.product_uom_id.round(settled),
                      asked=receipt_line.product_uom_id.round(quantity))
                )
        return True