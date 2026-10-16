from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError

from ..units import is_zero as _qty_is_zero


class CustomerGoodsReceiptReject(models.TransientModel):
    """Turn a whole delivery down, with the reason on the record.

    The store may legitimately refuse goods that are physically present and in
    good condition — they do not qualify for return, the wrong items were
    collected, or company policy says no. Nothing here touches stock or the
    books; it records the decision, the inspection comment, the photographs and
    where the goods are going to end up.
    """

    _name = 'customer.goods.receipt.reject'
    _description = 'Reject A Customer Goods Store Receipt'

    receipt_id = fields.Many2one(
        'customer.goods.receipt', string='Store Receipt', required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    company_id = fields.Many2one(
        'res.company', related='receipt_id.company_id', store=True, readonly=True,
    )
    invoice_number = fields.Char(
        related='receipt_id.invoice_number', string='Invoice Number', readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner', related='receipt_id.partner_id', string='Customer',
        readonly=True,
    )
    line_ids = fields.Many2many(
        'customer.goods.receipt.line', 'goods_receipt_reject_rel', 'wizard_id',
        'line_id', string='Goods Rejected',
        domain="[('receipt_id', '=', receipt_id)]",
    )
    rejection_reason = fields.Text(
        string='Reason For Rejection', required=True,
        help='Why the store is turning this delivery down, in words the sales '
             'representative and the accountant can both act on.',
    )
    inspection_notes = fields.Text(
        string='Inspection Notes',
        help='What the store saw, in more detail than the reason line.',
    )
    photo_ids = fields.Binary(
        string='Supporting Photos', attachment=True,
        help='Photographs of the goods, packaging or paperwork. Any number of '
             'images can be attached.',
    )
    rejected_disposition = fields.Selection(
        selection=[
            ('held', 'Held At The Store'),
            ('return_to_customer', 'To Be Returned To The Customer'),
            ('awaiting_decision', 'Awaiting Further Decision'),
        ],
        string='Where Do The Goods Go', default='held', required=True,
    )
    rejected_disposition_notes = fields.Char(
        string='Disposition Notes',
        help='Who is collecting the goods, when, and any agreement reached.',
    )
    keep_some = fields.Boolean(
        string='Some Goods Are Still Accepted',
        help='Tick this when part of the delivery is good and part is not. '
             'The accepted quantity stays in stock and stays creditable; only '
             'the rest is turned down.',
    )

    @api.model_create_multi
    def create(self, vals_list):
        """Pre-select every line of the receipt, so nothing is missed."""
        for vals in vals_list:
            receipt = self.env['customer.goods.receipt'].browse(
                vals.get('receipt_id') or self.env.context.get('active_id'))
            if not receipt:
                raise UserError(_('No store receipt was given to reject.'))
            if receipt.state != 'draft':
                raise UserError(
                    _('%(name)s has already been received.', name=receipt.name)
                )
            vals.setdefault('line_ids', [(6, 0, receipt.line_ids.ids)])
        return super().create(vals_list)

    def action_reject(self):
        """Record the rejection on the receipt, leaving stock and the books alone."""
        self.ensure_one()
        receipt = self.receipt_id
        if receipt.state != 'draft':
            raise UserError(
                _('%(name)s has already been received.', name=receipt.name)
            )
        if not self.line_ids:
            raise UserError(
                _('Nothing was selected to reject on %(name)s.', name=receipt.name)
            )
        commands = []
        for line in self.line_ids:
            if _qty_is_zero(self.env, line.qty_accepted) \
                    and not self.keep_some:
                continue
            values = {
                'qty_accepted': 0.0 if not self.keep_some else line.qty_accepted,
                'rejection_reason': self.rejection_reason,
            }
            if not self.keep_some:
                values['qty_received'] = line.qty_received
            commands.append(Command.update(line.id, values))
        if not commands:
            raise UserError(
                _('Nothing was selected to reject on %(name)s.', name=receipt.name))
        receipt.write({
            'rejection_reason': self.rejection_reason,
            'inspection_notes': self.inspection_notes,
            'rejected_disposition': self.rejected_disposition,
            'rejected_disposition_notes': self.rejected_disposition_notes,
            'line_ids': commands,
        })
        if self.photo_ids:
            receipt.message_post(
                body=_('Supporting photograph attached to the rejection.'),
                attachment_ids=[self.env['ir.attachment'].create({
                    'name': _('Rejection evidence — %s', receipt.name),
                    'raw': self.photo_ids,
                    'res_model': receipt._name,
                    'res_id': receipt.id,
                    'mimetype': self.env.context.get('mimetype') or 'image/png',
                }).id],
            )
        receipt.message_post(body=_(
            'Receipt rejected. %(reason)s %(notes)s',
            reason=self.rejection_reason,
            notes=_('Goods: %s.') % self.rejected_disposition))
        return receipt.action_view_receipt_form()