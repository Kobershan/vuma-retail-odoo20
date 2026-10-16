from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..units import are_compatible, compare as _qty_compare


RETURN_REASONS = [
    ('customer_request', 'Customer Request'),
    ('damaged', 'Damaged'),
    ('wrong_item', 'Wrong Item Supplied'),
    ('not_fit', 'Does Not Fit / Not Needed'),
    ('quality_issue', 'Quality Issue'),
    ('duplicate', 'Duplicate Purchase'),
    ('other', 'Other'),
]


class CustomerGoodsPickupLine(models.Model):
    """One product the representative took off one invoice line.

    Every figure the rep is allowed to change sits next to the figures they
    are *not* allowed to change — the invoiced quantity, what credit notes
    have already taken, and what other open pickups have already reserved — so
    over-collection is visible while it is being typed, not after the goods
    have left the customer.
    """

    _name = 'customer.goods.pickup.line'
    _description = 'Customer Goods Pickup Line'
    _order = 'sequence, id'
    _check_company_auto = True

    pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Pickup', required=True, ondelete='cascade',
        index=True,
    )
    company_id = fields.Many2one(
        'res.company', related='pickup_id.company_id', store=True, index=True,
    )
    sequence = fields.Integer(default=10)

    # ------------------------------------------------------- invoice identity
    invoice_line_id = fields.Many2one(
        'account.move.line', string='Original Invoice Line', required=True,
        ondelete='restrict', index=True,
        domain="[('move_id', '=', parent.invoice_id), ('display_type', '=', 'product')]",
        help='The exact invoice line this goods came off. Products that are '
             'not on the invoice cannot be collected against it.',
    )
    invoice_id = fields.Many2one(
        'account.move', related='pickup_id.invoice_id', string='Invoice',
        readonly=True, store=True,
    )
    invoice_number = fields.Char(
        related='pickup_id.invoice_number', string='Invoice Number', readonly=True,
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, index=True,
        ondelete='restrict',
        help='Taken from the invoice line. Editable only by a manager, so an '
             'invoiced line can never be quietly swapped for another product.',
    )
    name = fields.Text(
        string='Description', required=False,
        help='Description shown to the store, taken from the invoice line by '
             'default. The rep may add what the invoice could not say.',
    )
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        help='Unit the quantity is expressed in. Defaults to the invoice line '
             'unit; any other unit of the same category is accepted and '
             'converted.',
    )

    # ------------------------------------------------------ invoiced position
    invoiced_qty = fields.Float(
        string='Invoiced Qty', compute='_compute_invoice_position',
        digits='Product Unit',
        help='Quantity on the original invoice line, expressed in this line\'s '
             'unit so the four figures below can be read side by side.',
    )
    credited_qty = fields.Float(
        string='Previously Credited', compute='_compute_invoice_position',
        digits='Product Unit',
        help='Quantity on this invoice line that posted credit notes have '
             'already covered, in the invoice unit.',
    )
    outstanding_pickup_qty = fields.Float(
        string='On Other Pickups', compute='_compute_invoice_position',
        digits='Product Unit',
        help='Quantity reserved by other open pickups against this invoice '
             'line, in the invoice unit.',
    )
    eligible_qty = fields.Float(
        string='Still Collectable', compute='_compute_invoice_position',
        digits='Product Unit',
        help='Invoiced, less already credited, less held by other open '
             'pickups. This is the ceiling for "Quantity Collected".',
    )
    qty_collected = fields.Float(
        string='Quantity Collected', default=0.0, digits='Product Unit',
        help='How many the representative actually took from the customer.',
    )

    # ------------------------------------------------------------- pricing
    # Price, discount and taxes are read straight off the invoice line and
    # cannot be typed over: the credit note is built from these figures, and a
    # credit that does not match the invoice it cancels is not a credit note.
    price_unit = fields.Float(
        string='Invoiced Unit Price', related='invoice_line_id.price_unit',
        digits='Product Price', readonly=True,
        help='Unit price from the original invoice line. This is the price '
             'the credit note uses, so the credit always matches the invoice.',
    )
    discount = fields.Float(
        string='Discount (%)', related='invoice_line_id.discount',
        digits='Discount', readonly=True,
        help='Discount from the original invoice line.',
    )
    tax_ids = fields.Many2many(
        'account.tax', related='invoice_line_id.tax_ids', string='Taxes',
        readonly=True,
        help='Taxes that applied on the original invoice line. The credit note '
             'reproduces them exactly.',
    )
    tax_label = fields.Char(
        string='Tax', compute='_compute_tax_label',
        help='Readable list of the taxes that apply to this line.',
    )

    # ----------------------------------------------------------- collection
    return_reason = fields.Selection(
        selection=RETURN_REASONS,
        string='Reason for Return',
        help='Why this particular product is being taken back.',
    )
    condition_reported = fields.Selection(
        selection=[
            ('unknown', 'Not Reported'),
            ('good', 'Looks Good'),
            ('damaged', 'Damaged'),
            ('incomplete', 'Incomplete / Parts Missing'),
            ('unopened', 'Unopened'),
            ('wrong_item', 'Wrong Item'),
        ],
        string='Condition Reported', default='unknown',
        help="The representative's own read on the goods. The store makes its "
             "own decision at receiving; this is only information.",
    )
    notes = fields.Char(
        string='Line Notes',
        help='Anything about this product line the store should know.',
    )

    # ----------------------------------------------------------- downstream
    receipt_line_ids = fields.One2many(
        'customer.goods.receipt.line', 'pickup_line_id', string='Receipts',
    )
    qty_received_total = fields.Float(
        string='Received So Far', compute='_compute_received_totals', store=True,
        digits='Product Unit',
        help='Quantity presented at the store across all validated receipts.',
    )
    qty_accepted_total = fields.Float(
        string='Accepted So Far', compute='_compute_received_totals', store=True,
        digits='Product Unit',
    )
    qty_rejected_total = fields.Float(
        string='Rejected So Far', compute='_compute_received_totals', store=True,
        digits='Product Unit',
    )
    qty_credited_total = fields.Float(
        string='Credited So Far', compute='_compute_received_totals', store=True,
        digits='Product Unit',
        help='Quantity covered by a posted customer credit note.',
    )
    qty_to_receive = fields.Float(
        string='Still To Receive', compute='_compute_received_totals', store=True,
        digits='Product Unit',
    )

    # ------------------------------------------------------------ onchanges
    @api.onchange('invoice_line_id')
    def _onchange_invoice_line_id(self):
        """Pull the whole line out of the invoice line so nothing is retyped."""
        for line in self:
            invoice_line = line.invoice_line_id
            if not invoice_line:
                continue
            line.product_id = invoice_line.product_id
            line.name = invoice_line.name
            line.product_uom_id = invoice_line.product_uom_id

    @api.onchange('product_id')
    def _onchange_product_id(self):
        """A manager re-pointing a line must re-read it off the invoice."""
        for line in self:
            if not line.product_id:
                continue
            if not are_compatible(line.product_uom_id, line.product_id.uom_id):
                line.product_uom_id = line.invoice_line_id.product_uom_id \
                    or line.product_id.uom_id

    # ------------------------------------------------------------- computes
    @api.depends('invoice_line_id', 'pickup_id.invoice_id')
    def _compute_invoice_position(self):
        """Work out the invoice position for every line in one pass.

        These four figures are deliberately **not** stored. They depend on
        credit notes raised elsewhere and on other representatives' pickups,
        so a stored copy would go stale the moment either moved and the store
        would be looking at a number that is quietly wrong.

        A pickup has many lines but at most one invoice, so the credited and
        reserved figures are read once for the whole pickup and handed to each
        line. Doing this per line would be one query per line on a long
        invoice. Sibling lines of the same pickup that point at the same
        invoice line share what is left, so two rows cannot both claim the
        whole quantity.
        """
        lines_by_pickup = self.grouped('pickup_id')
        for pickup, lines in lines_by_pickup.items():
            credited_map = pickup._get_invoice_line_credited_map() if pickup.invoice_id \
                else {}
            reserved_map = pickup._get_invoice_line_reserved_map() if pickup.invoice_id \
                else {}
            claimed = {}
            for line in lines:
                invoice_line = line.invoice_line_id
                invoiced = self._convert(invoice_line,
                                         invoice_line.quantity or 0.0)
                credited = self._convert(invoice_line, credited_map.get(invoice_line.id, 0.0))
                reserved = self._convert(invoice_line, reserved_map.get(invoice_line.id, 0.0))
                eligible = invoiced - credited - reserved - claimed.get(invoice_line.id, 0.0)
                claimed[invoice_line.id] = claimed.get(invoice_line.id, 0.0) + line.qty_collected
                line.invoiced_qty = invoiced
                line.credited_qty = credited
                line.outstanding_pickup_qty = reserved
                line.eligible_qty = eligible
        return True

    @api.depends('qty_collected',
                 'receipt_line_ids.receipt_id.state',
                 'receipt_line_ids.qty_received', 'receipt_line_ids.qty_accepted',
                 'receipt_line_ids.qty_rejected', 'receipt_line_ids.qty_credited')
    def _compute_received_totals(self):
        for line in self:
            done = line.receipt_line_ids.filtered(
                lambda receipt_line: receipt_line.receipt_id.state == 'done')
            received = sum(done.mapped('qty_received'))
            accepted = sum(done.mapped('qty_accepted'))
            rejected = sum(done.mapped('qty_rejected'))
            credited = sum(done.mapped('qty_credited'))
            line.qty_received_total = received
            line.qty_accepted_total = accepted
            line.qty_rejected_total = rejected
            line.qty_credited_total = credited
            line.qty_to_receive = max(line.qty_collected - received, 0.0)
        return True

    def _compute_tax_label(self):
        for line in self:
            line.tax_label = ', '.join(
                sorted({tax.display_name for tax in line.tax_ids}))

    def _convert(self, invoice_line, qty):
        """Convert a quantity stored in the invoice unit into the line unit."""
        if not invoice_line:
            return 0.0
        return self.env['customer.goods.pickup']._convert_qty(
            qty, invoice_line.product_uom_id, self.product_uom_id
            or invoice_line.product_uom_id)

    # ---------------------------------------------------------- constraints
    @api.constrains('qty_collected', 'invoice_line_id', 'product_id', 'product_uom_id')
    def _check_collected_quantity(self):
        """The over-collection guard, run against what is eligible right now.

        Draft pickups are allowed to be over-allocated (the rep is still
        working), but the moment the state leaves draft the arithmetic has to
        hold, and ``action_confirm_collection`` re-runs it under a row lock.
        """
        for line in self:
            if _qty_compare(self.env, line.qty_collected, 0.0) < 0:
                raise ValidationError(
                    _('The collected quantity on "%(product)s" cannot be negative.',
                      product=line.product_id.display_name)
                )
            if line.product_uom_id and line.invoice_line_id.product_uom_id \
                    and not are_compatible(line.product_uom_id,
                                           line.invoice_line_id.product_uom_id):
                raise ValidationError(
                    _('%(uom)s and %(other)s are not the same kind of unit, so the '
                      'quantity for "%(product)s" cannot be converted.',
                      uom=line.product_uom_id.name,
                      other=line.invoice_line_id.product_uom_id.name,
                      product=line.product_id.display_name)
                )
            if line.pickup_id.state in ('draft', 'cancel'):
                continue
            if _qty_compare(self.env, line.qty_collected, line.eligible_qty) > 0:
                raise ValidationError(
                    _('%(product)s: %(asked)s were entered but only %(eligible)s are '
                      'still collectable against invoice %(invoice)s.',
                      product=line.product_id.display_name,
                      asked=line.product_uom_id.round(line.qty_collected),
                      eligible=line.product_uom_id.round(line.eligible_qty),
                      invoice=line.invoice_number)
                )

    @api.constrains('invoice_line_id')
    def _check_line_belongs_to_invoice(self):
        for line in self:
            invoice_line = line.invoice_line_id
            if not invoice_line:
                continue
            if invoice_line.move_id != line.pickup_id.invoice_id:
                raise ValidationError(
                    _('The invoice line chosen on "%(product)s" belongs to a '
                      'different invoice than this pickup.',
                      product=line.product_id.display_name)
                )
            if invoice_line.display_type not in ('product', 'line_section',
                                                 'line_subsection', 'line_note'):
                raise ValidationError(
                    _('%(line)s on %(invoice)s is not a product line and cannot be '
                      'collected.', line=invoice_line.display_name,
                      invoice=line.pickup_id.invoice_number)
                )
            if not invoice_line.product_id:
                raise ValidationError(
                    _('%(line)s on %(invoice)s has no product on it.',
                      line=invoice_line.display_name,
                      invoice=line.pickup_id.invoice_number)
                )

    # -------------------------------------------------------------- security
    def write(self, vals):
        """Once collection is confirmed, only a manager may touch the lines."""
        if {'qty_collected', 'invoice_line_id', 'product_id', 'product_uom_id'} & set(vals):
            self._check_not_confirmed()
        return super().write(vals)

    def unlink(self):
        """Deleting a line is the same edit as changing its quantity.

        A one-to-many write reaches ``customer.goods.pickup.write`` but a line
        removal goes straight to this model's ``unlink``, so without the same
        check a representative could drop what they had already collected.
        """
        if not self.env.su and not self.env.user.has_group(
                'customer_goods_pickup.group_goods_pickup_manager'):
            self._check_not_confirmed()
        return super().unlink()

    def _check_not_confirmed(self):
        frozen = self.filtered(
            lambda line: line.pickup_id.state not in ('draft', 'cancel'))
        if frozen:
            raise UserError(
                _('The collected quantities on %(names)s are confirmed and can '
                  'no longer be edited. A goods pickup manager has to make '
                  'the correction.',
                  names=', '.join(frozen.mapped('pickup_id.name')))
            )

    # ------------------------------------------------------------ navigation
    def action_view_invoice(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Original Invoice'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.invoice_id.id,
            'target': 'current',
        }