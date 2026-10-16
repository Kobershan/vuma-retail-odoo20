from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class CustomerGoodsReceiptReceive(models.TransientModel):
    """The Store Receiving screen.

    Opened from the pickup, it builds one receiving line for every product on
    the pickup that is still outstanding, pre-filled with the quantity the
    representative said they were bringing. The store changes the numbers to
    what actually turned up and what it is prepared to accept; everything else
    — the invoice, the prices, the taxes, the unit — comes across untouched so
    the credit note later matches the invoice exactly.
    """

    _name = 'customer.goods.receipt.receive'
    _description = 'Customer Goods Store Receiving Screen'

    pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Pickup', required=True,
        default=lambda self: self.env.context.get('active_id'),
    )
    company_id = fields.Many2one(
        'res.company', related='pickup_id.company_id', store=True, readonly=True,
    )
    partner_id = fields.Many2one(
        'res.partner', related='pickup_id.partner_id', string='Customer',
        readonly=True,
    )
    invoice_id = fields.Many2one(
        'account.move', related='pickup_id.invoice_id', string='Original Invoice',
        readonly=True,
    )
    invoice_number = fields.Char(
        related='pickup_id.invoice_number', string='Invoice Number', readonly=True,
    )
    sales_rep_id = fields.Many2one(
        'res.users', related='pickup_id.sales_rep_id', string='Sales Rep',
        readonly=True,
    )
    collection_date = fields.Datetime(
        related='pickup_id.collection_date', string='Collected On', readonly=True,
    )
    warehouse_id = fields.Many2one(
        'stock.warehouse', related='pickup_id.warehouse_id', string='Warehouse',
        readonly=True,
    )
    dest_location_id = fields.Many2one(
        'stock.location', string='Receive Accepted Goods Into', required=True,
        check_company=True,
        domain="[('usage', '=', 'internal'), ('warehouse_id', '=', warehouse_id)]",
    )
    received_by_id = fields.Many2one(
        'res.users', string='Received By', required=True,
        default=lambda self: self.env.user,
    )
    receipt_date = fields.Datetime(
        string='Received On', required=True, default=fields.Datetime.now,
    )
    inspection_notes = fields.Text(
        string='Inspection Notes',
        help='General comment on the condition of the goods as a whole.',
    )
    line_ids = fields.One2many(
        'customer.goods.receipt.receive.line', 'wizard_id',
        string='Goods Presented',
    )
    total_received = fields.Float(compute='_compute_totals', digits='Product Unit')
    total_accepted = fields.Float(compute='_compute_totals', digits='Product Unit')
    total_rejected = fields.Float(compute='_compute_totals', digits='Product Unit')

    @api.model_create_multi
    def create(self, vals_list):
        """Build the screen from whatever the pickup still has outstanding.

        Odoo resolves field defaults from the field definition rather than by
        calling ``default_get`` on create, so the counting lines are built here
        — which also means the same code path serves the button, the
        ``active_id`` binding and any scripted call.
        """
        for vals in vals_list:
            pickup = self.env['customer.goods.pickup'].browse(
                vals.get('pickup_id') or self.env.context.get('active_id'))
            if not pickup:
                raise UserError(_('No goods pickup was given to receive.'))
            if pickup.state not in ('collected', 'in_transit', 'partially_received'):
                raise UserError(
                    _('%(name)s is %(state)s, so there is nothing to receive.',
                      name=pickup.name, state=pickup._state_label())
                )
            vals.setdefault('dest_location_id', pickup.dest_location_id.id)
            if vals.get('line_ids'):
                continue
            commands, index = [], 10
            for line in pickup._get_outstanding_lines().sorted():
                commands.append((0, 0, {
                    'pickup_line_id': line.id,
                    'invoice_line_id': line.invoice_line_id.id,
                    'product_id': line.product_id.id,
                    'name': line.name,
                    'product_uom_id': line.product_uom_id.id,
                    'qty_collected': line.qty_collected,
                    'qty_outstanding': line.qty_to_receive,
                    'qty_received': line.qty_to_receive,
                    'qty_accepted': line.qty_to_receive,
                    'condition': 'good',
                    'sequence': index,
                }))
                index += 10
            vals['line_ids'] = commands
        return super().create(vals_list)

    @api.depends('line_ids.qty_received', 'line_ids.qty_accepted')
    def _compute_totals(self):
        for wizard in self:
            wizard.total_received = sum(wizard.line_ids.mapped('qty_received'))
            wizard.total_accepted = sum(wizard.line_ids.mapped('qty_accepted'))
            wizard.total_rejected = max(wizard.total_received - wizard.total_accepted, 0.0)
        return True

    def action_confirm_receiving(self):
        """Create the receiving document from what the store just counted."""
        self.ensure_one()
        lines = self.line_ids.filtered(
            lambda line: line.qty_received > 0 or line.qty_accepted > 0)
        if not lines:
            raise UserError(_('Please record what was presented at the store.'))
        for line in lines:
            if line.qty_accepted > line.qty_received:
                raise UserError(
                    _('On "%(product)s" you accepted more than was presented.',
                      product=line.product_id.display_name)
                )
            if line.qty_received > line.qty_outstanding and line.qty_outstanding:
                raise UserError(
                    _('Only %(outstanding)s of "%(product)s" were still outstanding '
                      'on %(pickup)s.', outstanding=line.product_uom_id.round(
                          line.qty_outstanding), product=line.product_id.display_name,
                      pickup=self.pickup_id.name)
                )
            if line.qty_received < line.qty_accepted:
                raise UserError(
                    _('On "%(product)s" the accepted quantity is larger than the '
                      'quantity received.', product=line.product_id.display_name)
                )
        # Flush what the pickup's counters read before the receipt is created: a
        # prefetch that runs before the new lines are written would leave an
        # empty line set cached for the rest of the request.
        self.env.flush_all()
        receipt = self.env['customer.goods.receipt'].create({
            'pickup_id': self.pickup_id.id,
            'dest_location_id': self.dest_location_id.id,
            'inspection_notes': self.inspection_notes,
            'state': 'draft',
            'line_ids': [Command.create(line._prepare_receipt_line_values())
                         for line in lines],
        })
        self.env.flush_all()
        receipt.invalidate_recordset(['line_ids'])
        receipt.write({
            'receipt_date': self.receipt_date,
            'received_by_id': self.received_by_id.id,
        })
        if self.pickup_id.state in ('collected', 'in_transit'):
            self.pickup_id.state = 'awaiting_receipt'
        return receipt.action_view_receipt_form()


class CustomerGoodsReceiptReceiveLine(models.TransientModel):
    """One product being counted in at the counter."""

    _name = 'customer.goods.receipt.receive.line'
    _description = 'Customer Goods Store Receiving Screen Line'
    _order = 'sequence, id'

    wizard_id = fields.Many2one(
        'customer.goods.receipt.receive', string='Wizard', required=True,
        ondelete='cascade', index=True,
    )
    sequence = fields.Integer(default=10)
    pickup_line_id = fields.Many2one(
        'customer.goods.pickup.line', string='Pickup Line', required=True,
        readonly=True,
    )
    invoice_line_id = fields.Many2one(
        'account.move.line', string='Invoice Line', required=True, readonly=True,
    )
    product_id = fields.Many2one(
        'product.product', string='Product', required=True, readonly=True,
    )
    name = fields.Text(string='Description', readonly=True)
    product_uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True, readonly=True,
    )
    qty_collected = fields.Float(
        string='Collected', digits='Product Unit', readonly=True,
        help='What the representative said they were bringing.',
    )
    qty_outstanding = fields.Float(
        string='Outstanding', digits='Product Unit', readonly=True,
    )
    qty_received = fields.Float(
        string='Presented', digits='Product Unit',
        help='How much was physically in front of the store.',
    )
    qty_accepted = fields.Float(
        string='Accept Into Stock', digits='Product Unit',
        help='How much the store accepts back into sellable stock. Anything '
             'below "Presented" is rejected and needs a reason.',
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
        string='Condition', default='good',
    )
    rejection_reason = fields.Char(string='Rejection Reason')
    inspection_notes = fields.Char(string='Inspection Notes')
    barcode = fields.Char(string='Barcode')

    @api.onchange('barcode')
    def _onchange_barcode(self):
        for line in self:
            code = (line.barcode or '').strip()
            if not code:
                continue
            product = self.env['product.product'].search([
                ('barcode', '=', code),
                ('id', '=', line.product_id.id),
            ], limit=1)
            line.barcode = False
            if not product:
                continue
            step = self.env['decimal.precision'].precision_get('Product Unit')
            line.qty_received += 10 ** -step

    def _prepare_receipt_line_values(self):
        """Turn a counted wizard line into the permanent receiving line."""
        self.ensure_one()
        return {
            'sequence': self.sequence,
            'pickup_line_id': self.pickup_line_id.id,
            'invoice_line_id': self.invoice_line_id.id,
            'qty_received': self.qty_received,
            'qty_accepted': self.qty_accepted,
            'condition': self.condition,
            'rejection_reason': self.rejection_reason,
            'inspection_notes': self.inspection_notes,
        }