from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..units import compare as _qty_compare, greater as _qty_gt


class CustomerGoodsReceiptLine(models.Model):
    """What turned up at the store for one invoice line, and what was kept.

    The store sees the whole story on one row — what was invoiced, what had
    already been credited, what the rep brought in, what previous visits had
    already taken, and what is still outstanding — so it can type a count
    without cross-referencing anything else.

    Three quantities are kept apart and are never derived from each other:
    ``qty_received`` (physically presented), ``qty_accepted`` (accepted into
    sellable stock) and the rejected remainder. Only the accepted figure can
    ever become stock or credit.
    """

    _name = 'customer.goods.receipt.line'
    _description = 'Customer Goods Store Receipt Line'
    _order = 'sequence, id'
    _check_company_auto = True

    receipt_id = fields.Many2one(
        'customer.goods.receipt', string='Store Receipt', required=True,
        ondelete='cascade', index=True,
    )
    company_id = fields.Many2one(
        'res.company', related='receipt_id.company_id', store=True, index=True,
    )
    sequence = fields.Integer(default=10)
    pickup_line_id = fields.Many2one(
        'customer.goods.pickup.line', string='Pickup Line', required=True,
        ondelete='cascade', index=True,
    )
    invoice_line_id = fields.Many2one(
        'account.move.line', string='Original Invoice Line', required=True,
        ondelete='restrict', index=True,
    )
    invoice_id = fields.Many2one(
        'account.move', related='receipt_id.invoice_id', string='Invoice',
        readonly=True, store=True,
    )
    invoice_number = fields.Char(
        related='receipt_id.invoice_number', string='Invoice Number',
        readonly=True, store=True,
    )
    product_id = fields.Many2one(
        'product.product', related='pickup_line_id.product_id', string='Product',
        store=True, readonly=True,
    )
    name = fields.Text(
        related='pickup_line_id.name', string='Description', readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', related='pickup_line_id.product_uom_id', string='Unit',
        readonly=True, store=True,
    )
    price_unit = fields.Float(
        related='pickup_line_id.price_unit', string='Invoiced Unit Price',
        digits='Product Price', readonly=True,
        help='Unit price on the original invoice line. Accepted goods are '
             'valued at this price and the credit note uses it.',
    )
    barcode = fields.Char(
        string='Barcode', copy=False,
        help='Scan or type the product barcode to fill in the quantity. Works '
             'with any ordinary reader, without needing the barcode module.',
    )

    # ------------------------------------------------- context for the store
    invoiced_qty = fields.Float(
        string='Invoiced', related='pickup_line_id.invoiced_qty',
        digits='Product Unit', readonly=True,
    )
    credited_qty = fields.Float(
        string='Previously Credited', related='pickup_line_id.credited_qty',
        digits='Product Unit', readonly=True,
    )
    qty_collected = fields.Float(
        string='Collected', related='pickup_line_id.qty_collected',
        digits='Product Unit', readonly=True,
        help='Quantity the sales representative reported collecting.',
    )
    qty_previously_received = fields.Float(
        string='Received Before', compute='_compute_previously_received',
        digits='Product Unit',
        help='Quantity presented at the store on earlier receipts of this pickup.',
    )
    qty_outstanding = fields.Float(
        string='Outstanding', compute='_compute_previously_received',
        digits='Product Unit',
        help='Still to be presented at the store. Nothing more than this can '
             'be received, which is what stops a quantity being received twice.',
    )

    # -------------------------------------------------------- what happened
    qty_received = fields.Float(
        string='Quantity Received', digits='Product Unit', default=0.0,
        help='Quantity physically presented to the store on this visit.',
    )
    qty_accepted = fields.Float(
        string='Accepted Into Stock', digits='Product Unit', default=0.0,
        help='Quantity the store accepted. Only this enters sellable stock, '
             'and only this can be credited.',
    )
    qty_rejected = fields.Float(
        string='Quantity Rejected', compute='_compute_qty_rejected', store=True,
        digits='Product Unit',
        help='What was received but not accepted. Needs a reason.',
    )
    condition = fields.Selection(
        selection=[
            ('unknown', 'Not Inspected'),
            ('good', 'Good'),
            ('damaged', 'Damaged'),
            ('incomplete', 'Incomplete / Parts Missing'),
            ('wrong_item', 'Wrong Item'),
            ('unopened', 'Unopened'),
        ],
        string='Condition', default='unknown',
        help="The store's own inspection result. This, not the "
             "representative's report, decides whether goods are accepted.",
    )
    rejection_reason = fields.Char(
        string='Rejection Reason',
        help='Required as soon as any quantity is rejected — damaged, wrong '
             'product, does not qualify for return, and so on.',
    )
    inspection_notes = fields.Text(
        string='Inspection Notes',
        help='What the store saw on this line.',
    )
    lot_id = fields.Many2one(
        'stock.lot', string='Lot / Serial', check_company=True, copy=False,
        domain="[('product_id', '=', product_id)]",
        help='Filled in automatically for tracked goods; can be overridden '
             'when the store has the customer lot in front of them.',
    )

    # ------------------------------------------------------------ downstream
    stock_move_id = fields.Many2one(
        'stock.move', string='Stock Move', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The incoming stock move that received the accepted quantity.',
    )
    quarantine_move_id = fields.Many2one(
        'stock.move', string='Quarantine Stock Move', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The incoming stock move that received the refused quantity into '
             'quarantine. It exists because refused goods are physically held, '
             'not merely described.',
    )
    quarantine_ids = fields.One2many(
        'customer.goods.quarantine', 'receipt_line_id', string='Quarantine Custody',
    )
    qty_in_quarantine = fields.Float(
        string='Still Held', compute='_compute_quarantine_totals', store=True,
        digits='Product Unit',
        help='Refused quantity that no disposition has taken yet. A physical '
             'count of the returns area must agree with this figure.',
    )
    qty_reinspected_accepted = fields.Float(
        string='Re-inspected And Accepted',
        compute='_compute_quarantine_totals', store=True, digits='Product Unit',
        help='Refused goods a manager later inspected again and released back '
             'into sellable stock. This quantity joins the accepted quantity '
             'for credit purposes, because it is now genuinely accepted stock.',
    )
    qty_disposed = fields.Float(
        string='Disposed', compute='_compute_quarantine_totals', store=True,
        digits='Product Unit',
        help='Refused quantity that has been returned to the customer, scrapped '
             'or otherwise closed out through a real stock transfer.',
    )
    credit_move_line_ids = fields.One2many(
        'account.move.line', 'goods_pickup_receipt_line_id', string='Credit Lines',
    )
    qty_credited = fields.Float(
        string='Credited', compute='_compute_qty_credited', store=True,
        digits='Product Unit',
        help='Accepted quantity already covered by a *posted* credit note.',
    )
    qty_credit_pending = fields.Float(
        string='Credit In Draft', compute='_compute_qty_credited', store=True,
        digits='Product Unit',
        help='Accepted quantity already claimed by a draft credit note. It is '
             'reserved, so a second credit note cannot be raised for it.',
    )
    qty_to_credit = fields.Float(
        string='Eligible For Credit', compute='_compute_qty_credited', store=True,
        digits='Product Unit',
    )

    # ------------------------------------------------------------- computes
    @api.depends('pickup_line_id.qty_collected',
                 'pickup_line_id.receipt_line_ids.qty_received',
                 'pickup_line_id.receipt_line_ids.receipt_id.state',
                 'qty_received', 'receipt_id.state')
    def _compute_previously_received(self):
        """Work out how much of this pickup line is still waiting to arrive.

        Only *other* validated receipts are subtracted while this receipt is
        being typed; once it is validated its own count is part of the total.
        """
        for line in self:
            previous = sum(
                other.qty_received
                for other in line.pickup_line_id.receipt_line_ids
                if other.id != line.id and other.receipt_id.state == 'done'
            )
            if line.receipt_id.state == 'done':
                previous += line.qty_received
            line.qty_previously_received = previous
            line.qty_outstanding = max(line.qty_collected - previous, 0.0)
        return True

    @api.depends('qty_received', 'qty_accepted')
    def _compute_qty_rejected(self):
        for line in self:
            line.qty_rejected = max(line.qty_received - line.qty_accepted, 0.0)
        return True

    @api.depends('quarantine_ids.qty', 'quarantine_ids.qty_disposed',
                 'quarantine_ids.qty_pending', 'quarantine_ids.qty_released',
                 'quarantine_ids.state')
    def _compute_quarantine_totals(self):
        """What became of the refused quantity on this line.

        Three numbers, kept apart on purpose:

        ``qty_in_quarantine``
            still physically held. What a stock count of the returns area has
            to agree with.
        ``qty_reinspected_accepted``
            released back into sellable stock by a manager. This is the only
            refused quantity that ever becomes creditable.
        ``qty_disposed``
            gone: handed back to the customer, scrapped, or otherwise closed.
        """
        # Roots only: a split child is already accounted for inside its root,\n        # so counting children as well would double the refused quantity.\n        records = self.mapped('quarantine_ids').filtered(\n            lambda record: not record.parent_quarantine_id)
        # Three separate dicts: three different facts about one refused qty;\n        # sharing one accumulator would add "still held" onto "released back".
        held, released, disposed = (defaultdict(float) for _ in range(3))
        records = self.mapped('quarantine_ids').filtered(
            lambda record: not record.parent_quarantine_id)
        if records:
            Quarantine = self.env['customer.goods.quarantine']
            for receipt_line, state, pending, released_qty, quantity in \
                    Quarantine._read_group(
                        [('id', 'in', records.ids)],
                        ['receipt_line_id', 'state'],
                        ['qty_pending:sum', 'qty_released:sum', 'qty:sum']):
                held[receipt_line.id] += pending
                released[receipt_line.id] += released_qty
                if state in ('returned', 'scrapped', 'written_off'):
                    disposed[receipt_line.id] += quantity
        for line in self:
            line.qty_in_quarantine = held[line.id]
            line.qty_reinspected_accepted = released[line.id]
            line.qty_disposed = disposed[line.id]
        return True

    @api.depends('qty_accepted', 'qty_reinspected_accepted',
                 'credit_move_line_ids.quantity',
                 'credit_move_line_ids.move_id.state')
    def _compute_qty_credited(self):
        """Split what a credit note has done into posted and merely promised.

        A draft note reserves its quantity without crediting anything, so the
        customer is never credited twice while a note is still waiting for an
        accountant, and is never shown as credited before the note is posted.

        The ceiling is the accepted quantity *plus* anything a manager released
        back out of quarantine after a second inspection — at that point it is
        ordinary accepted stock, and refusing to credit it would mean the
        customer paid for goods the shop had decided to resell.
        """
        for line in self:
            credited, pending = 0.0, 0.0
            for move_line in line.credit_move_line_ids:
                qty = move_line.quantity or 0.0
                if move_line.product_uom_id and move_line.product_uom_id != line.product_uom_id:
                    qty = move_line.product_uom_id._compute_quantity(
                        qty, line.product_uom_id, round=False, raise_if_failure=False)
                if move_line.move_id.state == 'posted':
                    credited += qty
                elif move_line.move_id.state == 'draft':
                    pending += qty
            line.qty_credited = credited
            line.qty_credit_pending = pending
            line.qty_to_credit = max(
                line.qty_accepted + line.qty_reinspected_accepted
                - credited - pending, 0.0)
        return True

    # ------------------------------------------------------------ constraints
    @api.constrains('qty_received', 'qty_accepted', 'rejection_reason', 'receipt_id')
    def _check_quantities(self):
        for line in self:
            if _qty_compare(self.env, line.qty_received, 0.0) < 0 \
                    or _qty_compare(self.env, line.qty_accepted, 0.0) < 0:
                raise ValidationError(
                    _('Quantities on %(product)s cannot be negative.',
                      product=line.product_id.display_name)
                )
            if _qty_compare(self.env, line.qty_accepted, line.qty_received) > 0:
                raise ValidationError(
                    _('On %(product)s the accepted quantity (%(accepted)s) is more '
                      'than the quantity received (%(received)s).',
                      product=line.product_id.display_name,
                      accepted=line.product_uom_id.round(line.qty_accepted),
                      received=line.product_uom_id.round(line.qty_received))
                )
            if _qty_gt(self.env, line.qty_received, line.qty_outstanding):
                raise ValidationError(
                    _('%(product)s: only %(outstanding)s %(uom)s were still '
                      'outstanding on %(pickup)s, but %(received)s were presented.',
                      product=line.product_id.display_name,
                      outstanding=line.product_uom_id.round(line.qty_outstanding),
                      uom=line.product_uom_id.name,
                      pickup=line.receipt_id.pickup_id.name,
                      received=line.product_uom_id.round(line.qty_received))
                )
            if _qty_gt(self.env, line.qty_rejected, 0.0) \
                    and not (line.rejection_reason or '').strip():
                raise ValidationError(
                    _('Please give a reason for the %(qty)s rejected on '
                      '%(product)s.', qty=line.product_uom_id.round(line.qty_rejected),
                      product=line.product_id.display_name)
                )

    @api.constrains('pickup_line_id')
    def _check_line_belongs_to_pickup(self):
        for line in self:
            if line.pickup_line_id.pickup_id != line.receipt_id.pickup_id:
                raise ValidationError(
                    _('Receipt line %(line)s does not belong to pickup %(pickup)s.',
                      line=line.product_id.display_name,
                      pickup=line.receipt_id.pickup_id.name)
                )

    # ------------------------------------------------------------- onchanges
    @api.onchange('barcode')
    def _onchange_barcode(self):
        """Fill the quantity from a scanned barcode.

        Deliberately dependency-free: any USB scanner that types the code and
        presses Enter works, so scanning is a convenience rather than a
        prerequisite for receiving.
        """
        for line in self:
            code = (line.barcode or '').strip()
            if not code:
                continue
            product = self.env['product.product'].search([
                ('barcode', '=', code),
                ('id', 'in', line.receipt_id.pickup_id.line_ids.product_id.ids),
            ], limit=1)
            if not product:
                line.barcode = False
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Product not on this pickup'),
                        'message': _('Barcode %(code)s does not match any product '
                                     'being collected on %(pickup)s.',
                                     code=code,
                                     pickup=line.receipt_id.pickup_id.name),
                        'type': 'warning',
                    },
                }
            line.barcode = False
            if line.product_id and line.product_id != product:
                return {
                    'type': 'ir.actions.client',
                    'tag': 'display_notification',
                    'params': {
                        'title': _('Wrong product scanned'),
                        'message': _('Barcode %(code)s is %(scanned)s, but this '
                                     'line is for %(expected)s.',
                                     code=code, scanned=product.display_name,
                                     expected=line.product_id.display_name),
                        'type': 'warning',
                    },
                }
            step = self.env['decimal.precision'].precision_get('Product Unit')
            line.qty_received += 10 ** -step
            if line.qty_received > line.qty_outstanding:
                line.qty_received = line.qty_outstanding
        return True
    # -------------------------------------------------------------- security
    def write(self, vals):
        """A validated receipt is frozen for everyone but a manager."""
        watched = {'qty_received', 'qty_accepted', 'rejection_reason',
                   'lot_id', 'inspection_notes'}
        if watched & set(vals):
            if not self.env.su and not self.env.user.has_group(
                    'customer_goods_pickup.group_goods_pickup_manager'):
                frozen = self.filtered(
                    lambda line: line.receipt_id.state != 'draft')
                if frozen:
                    raise UserError(
                        _('%(names)s has already been received. A goods pickup '
                          'manager has to raise a new receipt or reverse the '
                          'existing one, so the history is kept.',
                          names=', '.join(
                              frozen.mapped('receipt_id.name'))))
        return super().write(vals)

    def unlink(self):
        if not self.env.su and not self.env.user.has_group(
                'customer_goods_pickup.group_goods_pickup_manager'):
            frozen = self.filtered(lambda line: line.receipt_id.state != 'draft')
            if frozen:
                raise UserError(
                    _('A validated store receipt is part of the audit trail and '
                      'cannot be deleted.')
                )
        return super().unlink()