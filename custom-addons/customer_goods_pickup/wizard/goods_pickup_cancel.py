from odoo import _, api, fields, models
from odoo.exceptions import UserError


class CustomerGoodsPickupCancel(models.TransientModel):
    """Cancel a pickup, with the reason on the record.

    A confirmed pickup is never deleted. If goods have already been accepted
    into stock, the store has to reverse that receipt through a proper
    outgoing transfer first, so the inventory history still adds up.
    """

    _name = 'customer.goods.pickup.cancel'
    _description = 'Cancel A Customer Goods Pickup'

    pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Pickup', required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    invoice_number = fields.Char(
        related='pickup_id.invoice_number', string='Invoice Number', readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner', related='pickup_id.partner_id', string='Customer',
        readonly=True,
    )
    cancel_reason = fields.Text(
        string='Reason For Cancellation', required=True,
        help='A reason is mandatory: nothing here is ever cancelled quietly.',
    )
    goods_disposition = fields.Selection(
        selection=[
            ('not_collected', 'Never Collected'),
            ('returned_to_customer', 'Handed Back To The Customer'),
            ('still_with_representative', 'Still With The Representative'),
        ],
        string='What Happened To The Goods', default='not_collected', required=True,
        help='Where the goods physically are now, so the next person does not '
             'have to guess.',
    )
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        """Refuse up front when cancelling would mean deleting real work.

        Odoo resolves field defaults from the field definition rather than by
        calling ``default_get`` on create, so the rules live here.
        """
        for vals in vals_list:
            pickup = self.env['customer.goods.pickup'].browse(
                vals.get('pickup_id') or self.env.context.get('active_id'))
            if not pickup:
                raise UserError(_('No goods pickup was given to cancel.'))
            if pickup.state == 'cancel':
                raise UserError(
                    _('%(name)s is already cancelled.', name=pickup.name)
                )
            pending = pickup.receipt_ids.filtered(
                lambda receipt: receipt.state == 'draft')
            if pending:
                raise UserError(
                    _('%(name)s has a receiving sheet that is still open. Close '
                      'or discard it first.', name=pickup.name)
                )
            blocked = pickup.receipt_ids.filtered(
                lambda receipt: receipt.state == 'done'
                and not receipt.is_stock_reversed)
            if blocked:
                raise UserError(
                    _('%(name)s already moved accepted goods into stock on '
                      '%(receipts)s. A goods pickup manager must reverse those '
                      'receipts first — cancelling never deletes stock.',
                      name=pickup.name, receipts=', '.join(blocked.mapped('name')))
                )
            if pickup.credit_note_ids.filtered(lambda move: move.state == 'posted'):
                raise UserError(
                    _('%(name)s already has a posted credit note, so it cannot '
                      'be cancelled. Reverse the credit note first.',
                      name=pickup.name)
                )
        return super().create(vals_list)

    def action_cancel_pickup(self):
        self.ensure_one()
        pickup = self.pickup_id
        if pickup.state == 'cancel':
            raise UserError(
                _('%(name)s is already cancelled.', name=pickup.name)
            )
        disposition = dict(self._fields[
            'goods_disposition']._description_selection(self.env)
        ).get(self.goods_disposition, '')
        reason = '%s — %s' % (self.cancel_reason, disposition)
        if self.notes:
            reason = '%s\n%s' % (reason, self.notes)
        pickup.write({
            'state': 'cancel',
            'cancel_reason': reason,
            'cancel_date': fields.Datetime.now(),
        })
        pickup.message_post(body=_('Pickup cancelled. %s', reason))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Goods Pickup'),
            'res_model': 'customer.goods.pickup',
            'view_mode': 'form',
            'res_id': pickup.id,
            'target': 'current',
        }