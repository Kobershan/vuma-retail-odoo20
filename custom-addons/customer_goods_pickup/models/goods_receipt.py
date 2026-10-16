import logging
from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_datetime, float_compare

from ..access import ACCOUNTANT, MANAGER, STORE, require_group
from ..units import (are_compatible, compare as _qty_compare,
                     greater as _qty_gt, is_zero as _qty_is_zero)

_logger = logging.getLogger(__name__)

# The only fields an accountant may touch directly; everything else on a
# receipt belongs to the store.
CREDIT_WORKFLOW_FIELDS = frozenset({
    'credit_approval_state',
    'credit_approval_by_id',
    'credit_approval_date',
    'credit_approval_notes',
})


class CustomerGoodsReceipt(models.Model):
    """One trip's worth of goods presented at the store counter.

    This is where the business decision happens. The store counts what turned
    up, decides how much of it is good enough to sell again, and the receipt
    records three numbers that never blur into each other: what arrived, what
    was accepted, and what was turned down.

    Accepted quantity is the *only* thing that moves stock, and it does so
    through a genuine incoming transfer into the destination warehouse.
    Rejected quantity never touches a quant at all: it is recorded with a
    mandatory reason and the physical place the goods are sitting, which keeps
    it out of sellable stock without pretending the goods do not exist.

    The credit is a separate, later step. ``action_create_credit_note`` builds
    a **draft** customer credit note for the accepted-and-not-yet-credited
    quantity; posting it stays with whoever already holds Odoo's standard
    accounting permission.
    """

    _name = 'customer.goods.receipt'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Customer Goods Store Receipt'
    _order = 'receipt_date desc, id desc'
    _rec_names_search = ['name', 'pickup_id', 'pickup_id.partner_id']
    # Turns the ``check_company=True`` fields below into a hard rule.
    _check_company_auto = True

    # ------------------------------------------------------------- reference
    name = fields.Char(
        string='Receipt Reference', required=True, copy=False,
        default=lambda self: _('New'), index=True, tracking=True,
        help='Unique reference of this store receipt, e.g. RCP-00001.',
    )
    pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Customer Goods Pickup', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', related='pickup_id.company_id', store=True, index=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )
    invoice_id = fields.Many2one(
        'account.move', related='pickup_id.invoice_id', string='Original Invoice',
        readonly=True, store=True,
    )
    invoice_number = fields.Char(
        related='pickup_id.invoice_number', string='Invoice Number', readonly=True,
        store=True, index=True,
    )
    partner_id = fields.Many2one(
        'res.partner', related='pickup_id.partner_id', string='Customer',
        readonly=True, store=True,
    )
    sales_rep_id = fields.Many2one(
        'res.users', related='pickup_id.sales_rep_id', string='Sales Rep',
        readonly=True, store=True,
    )

    # -------------------------------------------------------------- logistics
    warehouse_id = fields.Many2one(
        'stock.warehouse', related='pickup_id.warehouse_id', string='Warehouse',
        readonly=True, store=True,
    )
    source_location_id = fields.Many2one(
        'stock.location', string='Source Location', readonly=True, copy=False,
        check_company=True,
        help='Where the goods came from — normally the customer location.',
    )
    dest_location_id = fields.Many2one(
        'stock.location', string='Destination Location', required=True,
        check_company=True,
        help='Where accepted goods are received into.',
    )
    picking_id = fields.Many2one(
        'stock.picking', string='Stock Receipt', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The incoming stock transfer this receipt created.',
    )
    picking_state = fields.Selection(
        related='picking_id.state', string='Transfer Status', readonly=True,
    )
    reverse_picking_id = fields.Many2one(
        'stock.picking', string='Reversal Transfer', readonly=True, copy=False,
        ondelete='set null',
        help='Outgoing transfer created when a manager reversed this receipt.',
    )
    is_stock_reversed = fields.Boolean(
        string='Stock Reversed', readonly=True, copy=False,
        help='Set when an authorised manager sent the accepted goods back out '
             'through a traceable stock transfer.',
    )

    # -------------------------------------------------------------- receiving
    receipt_date = fields.Datetime(
        string='Received On', copy=False, tracking=True,
        help='When the store received and inspected the goods.',
    )
    received_by_id = fields.Many2one(
        'res.users', string='Received By', readonly=True, copy=False,
        help='The store employee who received and inspected these goods.',
    )
    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user,
        index=True, tracking=True, copy=False,
        help='Who is currently dealing with this receipt.',
    )
    inspection_notes = fields.Text(
        string='Inspection Notes',
        help='Overall inspection comment: packaging, paperwork, and anything '
             'the accountant should know about the condition.',
    )
    rejection_reason = fields.Text(
        string='Rejection Reason', readonly=True, copy=False,
        help='Mandatory when nothing at all was accepted.',
    )
    line_ids = fields.One2many(
        'customer.goods.receipt.line', 'receipt_id', string='Goods Received',
        copy=True,
    )

    # ------------------------------------------------------ rejected physical
    rejected_location_id = fields.Many2one(
        'stock.location', string='Quarantine Location', readonly=True,
        copy=False, check_company=True,
        help='Where the refused goods physically are. They are moved there by a '
             'real validated stock transfer, so the quarantine holds a genuine '
             'quant — but the location sits outside every warehouse, so the '
             'quantity can never be sold or reserved.',
    )
    quarantine_picking_id = fields.Many2one(
        'stock.picking', string='Quarantine Receipt', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The incoming transfer that physically received the refused '
             'quantity into quarantine.',
    )
    quarantine_picking_state = fields.Selection(
        related='quarantine_picking_id.state',
        string='Quarantine Transfer Status', readonly=True,
    )
    quarantine_ids = fields.One2many(
        'customer.goods.quarantine', 'receipt_id', string='Quarantine Custody',
    )
    quarantine_count = fields.Integer(
        string='Quarantine Records', compute='_compute_quarantine_count',
    )
    quarantine_qty = fields.Float(
        string='Still Held', compute='_compute_quarantine_count', store=True,
        digits='Product Unit',
        help='Refused quantity that no disposition has taken yet. A physical '
             'count of the returns area should agree with this figure.',
    )
    quarantine_released_qty = fields.Float(
        string='Re-inspected And Accepted', compute='_compute_quarantine_count',
        store=True, digits='Product Unit',
    )
    needs_quarantine_verification = fields.Boolean(
        string='Quarantine Custody Unverified',
        compute='_compute_quarantine_count', store=True,
        help='This receipt refused goods but has no quarantine custody record, '
             'so it predates real quarantine stock and needs a physical check. '
             'The module never invents a stock move for goods nobody has '
             'confirmed are in the building.',
    )
    rejected_disposition = fields.Selection(
        selection=[
            ('held', 'Held At The Store'),
            ('return_to_customer', 'To Be Returned To The Customer'),
            ('awaiting_decision', 'Awaiting Further Decision'),
        ],
        string='Rejected Goods Disposition',
        help='What is going to happen to the goods that were turned down. The '
             'real decision, and the stock transfer it causes, is on each '
             'quarantine record.',
    )
    rejected_disposition_notes = fields.Char(
        string='Disposition Notes',
        help='Detail of the disposition — who is collecting the goods, when, '
             'and any agreement with the customer.',
    )
    rejected_disposition_date = fields.Datetime(
        string='Disposition Recorded On', readonly=True, copy=False,
    )

    # -------------------------------------------------------------- workflow
    state = fields.Selection(
        selection=[
            ('draft', 'Being Received'),
            ('done', 'Received'),
            ('cancel', 'Cancelled'),
        ],
        string='Status', default='draft', required=True, copy=False, tracking=True,
        index=True,
    )

    # ----------------------------------------------------------------- totals
    total_received_qty = fields.Float(
        string='Received', compute='_compute_totals', store=True,
        digits='Product Unit',
    )
    total_accepted_qty = fields.Float(
        string='Accepted Into Stock', compute='_compute_totals', store=True,
        digits='Product Unit',
    )
    total_rejected_qty = fields.Float(
        string='Rejected', compute='_compute_totals', store=True,
        digits='Product Unit',
    )
    total_credited_qty = fields.Float(
        string='Credited', compute='_compute_totals', store=True,
        digits='Product Unit',
    )
    has_rejection = fields.Boolean(
        string='Has Rejection', compute='_compute_has_rejection', store=True,
    )

    # ------------------------------------------------------------------ credit
    credit_approval_state = fields.Selection(
        selection=[
            ('pending', 'Awaiting Approval'),
            ('approved', 'Approved'),
            ('declined', 'Declined'),
        ],
        string='Credit Approval', default='pending', required=True, copy=False,
        tracking=True,
    )
    credit_approval_by_id = fields.Many2one(
        'res.users', string='Credit Approved By', readonly=True, copy=False,
    )
    credit_approval_date = fields.Datetime(
        string='Credit Approved On', readonly=True, copy=False,
    )
    credit_approval_notes = fields.Text(
        string='Credit Decision Notes',
        help='Why the credit was approved or declined. Required when declining.',
    )
    move_ids = fields.One2many(
        'account.move', 'goods_pickup_receipt_id', string='Credit Notes',
    )
    move_id = fields.Many2one(
        'account.move', compute='_compute_credit_move', store=True,
        string='Credit Note',
        help='The customer credit note raised for this receipt.',
    )
    move_state = fields.Selection(related='move_id.state', string='Credit Note Status')
    move_amount_total = fields.Monetary(
        string='Credit Note Amount', compute='_compute_move_amount_total',
        currency_field='currency_id',
    )
    credit_state = fields.Selection(
        selection=[
            ('not_eligible', 'Nothing To Credit'),
            ('awaiting_approval', 'Awaiting Credit Approval'),
            ('approved', 'Approved For Credit'),
            ('declined', 'Credit Declined'),
            ('draft_note', 'Draft Credit Note'),
            ('posted', 'Credit Note Posted'),
        ],
        string='Credit Status', compute='_compute_credit_state', store=True,
        help='Derived from what the store accepted and what accounting has '
             'actually posted. Nothing here claims a credit that has not been '
             'posted.',
    )
    pending_credit_qty = fields.Float(
        string='Eligible For Credit', compute='_compute_pending_credit_qty',
        store=True, digits='Product Unit',
        help='Accepted quantity that no credit note covers yet.',
    )

    # ----------------------------------------------------------------- create
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                company = self.env['res.company'].browse(
                    vals.get('company_id') or self.env.company.id
                )
                vals['name'] = self.with_company(company).env['ir.sequence'].next_by_code(
                    'customer.goods.receipt'
                ) or _('New')
            self._check_creation_rights(vals)
        return super().create(vals_list)

    def _check_creation_rights(self, vals):
        """``create`` carries the same split of duties as ``write``.

        Without this the accountant-only credit decision was reachable by
        handing ``create`` the very fields ``write`` refuses: a store clerk
        could bring a receipt into existence already marked approved, in a
        colleague's name. It also refuses a receipt that arrives already
        closed, because ``state`` belongs to ``action_validate_receipt`` alone.
        """
        touched = set(vals)
        if touched & CREDIT_WORKFLOW_FIELDS:
            require_group(self.env, (ACCOUNTANT, MANAGER), _(
                'Only an accountant can change the credit decision on a goods '
                'receipt.'))
        if not touched <= CREDIT_WORKFLOW_FIELDS:
            require_group(self.env, (STORE, MANAGER), _(
                'Only store staff can open a goods receipt.'))
        if vals.get('state') not in (False, None, 'draft'):
            raise UserError(_(
                'A goods receipt is only closed by confirming the receipt, '
                'which is what puts the goods into stock.'))

    def write(self, vals):
        if {'line_ids', 'dest_location_id'} & set(vals):
            for receipt in self:
                if receipt.state != 'draft':
                    raise UserError(
                        _('%(name)s has already been received. A goods pickup '
                          'manager has to raise a new receipt or reverse this '
                          'one.', name=receipt.name)
                    )
        # The accountant may run the credit workflow and nothing else; the
        # store owns the operational fields.
        if set(vals) & CREDIT_WORKFLOW_FIELDS:
            require_group(self.env, (ACCOUNTANT, MANAGER), _(
                'Only an accountant can change the credit decision on a goods '
                'receipt.'))
        if not set(vals) <= CREDIT_WORKFLOW_FIELDS:
            require_group(self.env, (STORE, MANAGER), _(
                'Only store staff can change what was received or accepted on '
                'a goods receipt.'))
        # ``state`` closes a receipt, and closing it is what moves the accepted
        # quantity into stock and freezes the counts. That happens in exactly
        # one place -- ``action_validate_receipt`` -- so a plain write that
        # skips it would settle goods the warehouse never received. The action
        # runs its own write with ``sudo``: ``env.su`` is an attribute of the
        # environment object and not a context key, so it cannot be set over
        # RPC the way the context key this replaced could be.
        if 'state' in vals and not set(vals) <= CREDIT_WORKFLOW_FIELDS \
                and not self.env.su:
            raise UserError(
                _('A goods receipt is only closed by confirming the receipt, '
                  'which is what puts the accepted goods into stock.'))
        # The approval stamp records who made the decision; it may only be
        # written by the action that makes it, again through ``sudo``.
        if {'credit_approval_by_id', 'credit_approval_date'} & set(vals) \
                and not self.env.su:
            raise UserError(
                _('The credit approval details are recorded by the approval '
                  'action itself and cannot be set by hand.'))
        return super().write(vals)

    def unlink(self):
        for receipt in self:
            if receipt.state != 'draft':
                raise UserError(
                    _('%(name)s has already been received and is part of the '
                      'audit trail; it cannot be deleted.', name=receipt.name)
                )
        return super().unlink()

    # -------------------------------------------------------------- computes
    def _state_label(self):
        self.ensure_one()
        selection = dict(self._fields['state']._description_selection(self.env))
        return selection.get(self.state) or self.state

    @api.depends('line_ids.qty_received', 'line_ids.qty_accepted',
                 'line_ids.qty_rejected', 'line_ids.qty_credited',
                 'line_ids.qty_reinspected_accepted')
    def _compute_totals(self):
        for receipt in self:
            receipt.total_received_qty = sum(receipt.line_ids.mapped('qty_received'))
            receipt.total_accepted_qty = (sum(receipt.line_ids.mapped('qty_accepted'))
                                         + sum(receipt.line_ids.mapped(
                                             'qty_reinspected_accepted')))
            receipt.total_rejected_qty = sum(receipt.line_ids.mapped('qty_rejected'))
            receipt.total_credited_qty = sum(receipt.line_ids.mapped('qty_credited'))
        return True

    @api.depends('total_rejected_qty')
    def _compute_has_rejection(self):
        for receipt in self:
            receipt.has_rejection = _qty_gt(self.env, receipt.total_rejected_qty, 0.0)
        return True

    @api.depends('quarantine_ids', 'quarantine_ids.qty_pending',
                 'quarantine_ids.qty_released', 'total_rejected_qty',
                 'quarantine_ids.parent_quarantine_id')
    def _compute_quarantine_count(self):
        held = released = defaultdict(float)
        counts = defaultdict(int)
        records = self.mapped('quarantine_ids')
        for record in records:
            held[record.receipt_id.id] += record.qty_pending
            released[record.receipt_id.id] += record.qty_released
            if not record.parent_quarantine_id:
                counts[record.receipt_id.id] += 1
        for receipt in self:
            receipt.quarantine_qty = held[receipt.id]
            receipt.quarantine_released_qty = released[receipt.id]
            receipt.quarantine_count = counts[receipt.id]
            # A refusal with no custody record is legacy data: the goods were
            # never booked into quarantine, and the module will not pretend
            # otherwise. It is flagged so somebody can physically check.
            receipt.needs_quarantine_verification = _qty_gt(
                self.env, receipt.total_rejected_qty, 0.0) \
                and not receipt.quarantine_count

    @api.depends('move_ids')
    def _compute_credit_move(self):
        """The credit note a reader should be looking at.

        A cancelled draft is not the note for this receipt any more -- the
        accepted quantity went back on the market and a fresh note was raised
        for it. Taking the first note ever created would leave the receipt
        pointing at a cancelled one and hide the live note behind it, so the
        live note wins and the newest of those is the one shown.
        """
        for receipt in self:
            live = receipt.move_ids.filtered(lambda move: move.state != 'cancel')
            moves = live or receipt.move_ids
            receipt.move_id = moves[-1] if moves else False
        return True

    @api.depends('move_id.amount_total')
    def _compute_move_amount_total(self):
        for receipt in self:
            receipt.move_amount_total = receipt.move_id.amount_total
        return True

    @api.depends('state', 'total_accepted_qty', 'line_ids.qty_accepted',
                 'line_ids.qty_credited', 'line_ids.qty_credit_pending',
                 'credit_approval_state', 'move_ids.state')
    def _compute_pending_credit_qty(self):
        for receipt in self:
            receipt.pending_credit_qty = sum(
                receipt._get_eligible_credit_lines().mapped('qty_to_credit'))
        return True

    @api.depends('pending_credit_qty', 'state', 'total_accepted_qty',
                 'credit_approval_state', 'move_ids.state')
    def _compute_credit_state(self):
        for receipt in self:
            posted = receipt.move_ids.filtered(lambda move: move.state == 'posted')
            draft = receipt.move_ids.filtered(lambda move: move.state == 'draft')
            if receipt.state != 'done' or _qty_is_zero(self.env, receipt.total_accepted_qty):
                state = 'not_eligible'
            elif draft:
                state = 'draft_note'
            elif posted:
                state = 'posted'
            elif receipt.credit_approval_state == 'declined':
                state = 'declined'
            elif receipt.credit_approval_state == 'approved':
                state = 'approved'
            else:
                state = 'awaiting_approval'
            receipt.credit_state = state
        return True

    # ------------------------------------------------------------ constraints
    @api.constrains('line_ids', 'dest_location_id')
    def _check_receipt_lines(self):
        for receipt in self:
            if not receipt.dest_location_id:
                raise ValidationError(
                    _('Please choose the location accepted goods are received '
                      'into on %(name)s.', name=receipt.name)
                )
            if receipt.dest_location_id.usage != 'internal':
                raise ValidationError(
                    _('%(location)s is not an internal location, so accepted '
                      'goods could not be sold from it.',
                      location=receipt.dest_location_id.display_name)
                )
            if not receipt.line_ids:
                raise ValidationError(
                    _('Please record at least one product line on %(name)s.',
                      name=receipt.name)
                )
            seen = set()
            for line in receipt.line_ids:
                if line.invoice_line_id.id in seen:
                    raise ValidationError(
                        _('Invoice line %(line)s appears twice on %(name)s. Each '
                          'invoice line is received on a single line of the receipt.',
                          line=line.invoice_line_id.display_name, name=receipt.name)
                    )
                seen.add(line.invoice_line_id.id)
                if _qty_compare(self.env, line.qty_received, 0.0) < 0:
                    raise ValidationError(
                        _('A received quantity cannot be negative on %(product)s.',
                          product=line.product_id.display_name)
                    )
                if _qty_compare(self.env, line.qty_accepted, 0.0) < 0 \
                        or _qty_compare(self.env, line.qty_accepted, line.qty_received) > 0:
                    raise ValidationError(
                        _('On %(product)s you accepted %(accepted)s of the %(received)s '
                          'presented. The accepted quantity must be between zero and '
                          'the quantity received.',
                          product=line.product_id.display_name,
                          accepted=line.product_uom_id.round(line.qty_accepted),
                          received=line.product_uom_id.round(line.qty_received))
                    )
                if _qty_gt(self.env, line.qty_received, line.qty_outstanding):
                    raise ValidationError(
                        _('%(product)s: only %(outstanding)s %(uom)s were still '
                          'outstanding on %(pickup)s, but %(received)s were '
                          'presented, so the same goods would be received twice.',
                          product=line.product_id.display_name,
                          outstanding=line.product_uom_id.round(line.qty_outstanding),
                          uom=line.product_uom_id.name,
                          pickup=receipt.pickup_id.name,
                          received=line.product_uom_id.round(line.qty_received))
                    )
                if _qty_gt(self.env, line.qty_rejected, 0.0) \
                        and not (line.rejection_reason or '').strip():
                    raise ValidationError(
                        _('Please give a reason for the %(qty)s rejected on '
                          '%(product)s.',
                          qty=line.product_uom_id.round(line.qty_rejected),
                          product=line.product_id.display_name)
                    )

    # ---------------------------------------------------------- validate stock
    def action_validate_receipt(self):
        """Store side: the goods have arrived, been counted and judged.

        Accepted quantity goes into stock through a real incoming transfer into
        the configured warehouse. Refused quantity goes into quarantine through
        a real incoming transfer of its own, so the refused goods have a
        physical location, a quant and a value, while sitting outside every
        warehouse where availability, reservation and replenishment cannot see
        them. No accounting credit happens here: only the accepted quantity is
        ever creditable, and even that waits for the accountant.
        """
        require_group(self.env, (STORE, MANAGER), _(
            'Only store staff can confirm that goods were received.'))
        for receipt in self:
            receipt._check_can_validate()
        for receipt in self:
            receipt._prepare_receipt_values()
            receipt._validate_stock_receipt()
            receipt._validate_quarantine_receipt()
            values = {
                'state': 'done',
                'receipt_date': fields.Datetime.now(),
                'received_by_id': self.env.user.id,
            }
            if receipt.has_rejection:
                # A part-rejection arrives from the receiving screen, where
                # nobody was asked where the goods go; it is recorded as
                # awaiting a decision rather than invented here.
                values['rejected_location_id'] = \
                    receipt._get_rejected_location().id
                values['rejected_disposition'] = \
                    receipt.rejected_disposition or 'awaiting_decision'
            receipt.sudo().write(values)
            receipt._log_receiving_outcome()
            receipt.pickup_id._action_refresh_receiving_state()
            receipt._notify_sales_rep()
            # The pickup's counters read the receipt lines; make sure a value
            # cached earlier in this transaction (by the representative, say)
            # cannot leave them looking at a stale count.
            self.env['customer.goods.pickup']._refresh_invoice_positions(
                receipt.pickup_id.invoice_id)
        return receipt.action_view_picking() if receipt.picking_id else True

    def _log_receiving_outcome(self):
        """Write the receiving decision into the document's own history.

        The counts are on lines that carry no chatter, and a receipt line has
        no ``mail.thread`` of its own, so before this the accept/reject
        decision existed only as field values: nothing on the timeline said
        what was accepted, what was turned down or why, or who decided.
        """
        self.ensure_one()
        body = _(
            'Goods received: %(received)s presented, %(accepted)s accepted into '
            '%(location)s, %(rejected)s rejected.',
            received=self.total_received_qty, accepted=self.total_accepted_qty,
            rejected=self.total_rejected_qty,
            location=self.dest_location_id.display_name)
        reasons = [(line.product_id.display_name, line.rejection_reason)
                   for line in self.line_ids if line.rejection_reason]
        if reasons:
            body += '\n' + _('Rejected — %(reasons)s', reasons='; '.join(
                '%s: %s' % (product, reason) for product, reason in reasons))
        if self.has_rejection:
            body += '\n' + _(
                'Refused goods were received into quarantine at %(location)s '
                '(%(disposition)s) through %(transfer)s. They sit outside every '
                'warehouse, so they cannot be sold or reserved.',
                location=self.rejected_location_id.display_name,
                disposition=self.rejected_disposition or _('awaiting a decision'),
                transfer=self.quarantine_picking_id.name or _('(pending)'))
        if self.inspection_notes:
            body += '\n' + _('Inspection: %s', self.inspection_notes)
        self.message_post(body=body)
        return True

    def _check_can_validate(self):
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(
                _('%(name)s was already received %(when)s. The same goods cannot '
                  'be received twice.',
                  name=self.name,
                  when=format_datetime(self.env, self.receipt_date)
                  if self.receipt_date else _('earlier'))
            )
        if not self.line_ids:
            raise UserError(
                _('Please record what was presented on %(name)s.', name=self.name)
            )
        if _qty_is_zero(self.env, self.total_received_qty):
            raise UserError(
                _('Nothing was presented on %(name)s. Record the quantity that '
                  'actually arrived, or cancel the receipt.', name=self.name)
            )
        if _qty_is_zero(self.env, self.total_accepted_qty) \
                and not (self.rejection_reason or '').strip():
            raise UserError(
                _('Nothing on %(name)s was accepted. Please give the reason the '
                  'store turned the whole receipt down.', name=self.name)
            )
        if _qty_is_zero(self.env, self.total_accepted_qty) and not self.rejected_disposition:
            raise UserError(
                _('Please say what happens to the rejected goods on %(name)s, '
                  'e.g. held at the store or going back to the customer.',
                  name=self.name)
            )

    def _prepare_receipt_values(self):
        """Stamp the source location and flush before the counters are read."""
        self.ensure_one()
        if not self.source_location_id:
            self.source_location_id = self._get_customer_location().id
        self.invalidate_recordset(['total_received_qty', 'total_accepted_qty',
                                   'total_rejected_qty'])
        self.env.flush_all()
        return True

    def _get_customer_location(self):
        """The stock location the goods travel from: the customer's own."""
        self.ensure_one()
        location = self.partner_id.property_stock_customer
        if not location:
            location = self.env['stock.location'].search([
                ('usage', '=', 'customer'),
                ('company_id', 'in', (False, self.company_id.id)),
            ], limit=1)
        if not location:
            raise UserError(
                _('No customer stock location could be found for company %s. '
                  'Please create one in Inventory > Configuration > Locations.',
                  self.company_id.display_name)
            )
        return location

    def _get_rejected_location(self):
        """Where refused goods are physically received.

        The configured location when the shop has set one, otherwise the one
        the module maintains. Either way it is validated first: a location that
        would put refused goods inside a warehouse is refused outright rather
        than quietly used, because the alternative is exactly the silent
        fallback this design exists to prevent.
        """
        self.ensure_one()
        configured = self.env['ir.config_parameter'].sudo().get_int(
            'customer_goods_pickup.quarantine_location_id')
        if configured:
            location = self.env['stock.location'].sudo().browse(configured).exists()
            if location:
                location._check_goods_return_location_suitability()
                if location.company_id in (self.env['res.company'],
                                           self.company_id):
                    return location
                raise UserError(_(
                    'The quarantine location configured in Settings belongs to '
                    '%(other)s, not to %(mine)s. Fix it in Settings > Inventory > '
                    'Customer Goods Pickup.',
                    other=location.company_id.display_name,
                    mine=self.company_id.display_name))
        location = self.env['stock.location']._get_quarantine_location(
            self.company_id)
        location._check_goods_return_location_suitability()
        return location

    def _validate_quarantine_receipt(self):
        """Receive the refused quantity into quarantine, for real.

        This is the part the previous audit found missing: refused goods used
        to exist only as a location name on the receipt. Here they get a real
        validated incoming transfer, so there is a quant, a stock move and a
        valuation to point at — and because the destination is outside every
        warehouse, none of that makes them sellable.
        """
        self.ensure_one()
        refused_lines = self.line_ids.filtered(
            lambda line: not _qty_is_zero(self.env, line.qty_rejected))
        if not refused_lines:
            return self.env['stock.picking']
        quarantine = self._get_rejected_location()
        commands = [Command.create(self._prepare_move_values(
            line, quarantine, 'qty_rejected')) for line in refused_lines]
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': self.warehouse_id.in_type_id.id,
            'partner_id': self.partner_id.id,
            'location_id': self.source_location_id.id,
            'location_dest_id': quarantine.id,
            'origin': self.pickup_id.name,
            'note': self._get_quarantine_note(quarantine),
            'move_ids': commands,
        })
        moves = picking.move_ids
        # ``merge=False`` for the same reason as the accepted transfer: one
        # move per receipt line, so each refused quantity keeps its own link
        # back to the invoice line it came from.
        moves._action_confirm(merge=False)
        moves.write({'picked': True})
        picking.with_context(cancel_backorder=True)._action_done()
        self.sudo().write({'quarantine_picking_id': picking.id})
        for line, move in zip(refused_lines, moves):
            line.sudo().write({'quarantine_move_id': move.id,
                               'lot_id': line.lot_id
                               or move.move_line_ids[:1].lot_id})
            self.env['customer.goods.quarantine'].sudo().create({
                'receipt_id': self.id,
                'receipt_line_id': line.id,
                'company_id': self.company_id.id,
                'lot_id': line.lot_id.id,
                'qty': line.qty_rejected,
                'location_id': quarantine.id,
                'picking_id': picking.id,
                'stock_move_id': move.id,
            })
        self.message_post(body=_(
            'Refused goods received into %(location)s: %(qty)s. Transfer '
            '%(picking)s. The quantity is real stock, but it sits outside every '
            'warehouse, so it cannot be sold or reserved.',
            location=quarantine.display_name,
            qty=sum(refused_lines.mapped('qty_rejected')),
            picking=picking.name))
        return picking

    def _get_quarantine_note(self, quarantine):
        self.ensure_one()
        return _(
            'Refused customer goods from receipt %(receipt)s against invoice '
            '%(invoice)s, pickup %(pickup)s.\nRefused: %(rejected)s\n'
            'Quarantine: %(location)s\nReceived by %(user)s on %(date)s.',
            receipt=self.name, invoice=self.invoice_number or '',
            pickup=self.pickup_id.name,
            rejected=sum(self.line_ids.mapped('qty_rejected')),
            location=quarantine.display_name,
            user=self.env.user.display_name,
            date=format_datetime(self.env, fields.Datetime.now()))

    def _validate_stock_receipt(self):
        """Create and validate the incoming transfer for the accepted quantity.

        Nothing here writes a quant by hand: the movement goes through
        ``stock.picking``, so Odoo's own valuation, traceability and costing
        apply exactly as they would for any other receipt.
        """
        self.ensure_one()
        picking = self._prepare_stock_picking()
        if not picking:
            return picking
        # Confirmed without merging. ``stock.picking.action_confirm`` merges
        # moves that share a product and locations, so two receipt lines for
        # the same product would collapse into one move: the merged-away move
        # is unlinked, the receipt line's ``stock_move_id`` is set to null by
        # its ``ondelete``, and that part of the accepted quantity can then
        # never be reversed. One move per receipt line keeps every accepted
        # quantity individually reversible.
        moves = picking.move_ids
        moves._action_confirm(merge=False)
        moves.write({'picked': True})
        picking.with_context(cancel_backorder=True)._action_done()
        return picking

    def _prepare_stock_picking(self):
        """Build the incoming transfer that receives the accepted quantity."""
        self.ensure_one()
        accepted_lines = self.line_ids.filtered(
            lambda line: not _qty_is_zero(self.env, line.qty_accepted))
        if not accepted_lines:
            return self.env['stock.picking']
        move_commands = [Command.create(self._prepare_move_values(
            line, self.dest_location_id, 'qty_accepted'))
            for line in accepted_lines]
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse_id.in_type_id.id,
            'partner_id': self.partner_id.id,
            'location_id': self.source_location_id.id,
            'location_dest_id': self.dest_location_id.id,
            'origin': self.pickup_id.name,
            'note': self._get_stock_note(),
            'move_ids': move_commands,
        })
        # Link each line back to its own move, so the two documents can be
        # walked in either direction.
        for line, move in zip(accepted_lines, picking.move_ids):
            line.stock_move_id = move
        self.picking_id = picking
        return picking

    def _prepare_move_values(self, line, dest_location, qty_field='qty_accepted'):
        """One ``stock.move`` for one receipt line, to one destination."""
        self.ensure_one()
        product = line.product_id
        move_uom = line.product_uom_id
        if not are_compatible(move_uom, product.uom_id):
            move_uom = product.uom_id
        convert = self.env['customer.goods.pickup']._convert_qty
        qty = convert(line[qty_field], line.product_uom_id, move_uom)
        move_vals = {
            # Odoo 20 dropped stock.move.name; the description lives on the
            # picking label field.
            'description_picking_manual': line.name or product.display_name,
            'product_id': product.id,
            'product_uom_qty': qty,
            'uom_id': move_uom.id,
            'location_id': self.source_location_id.id,
            'location_dest_id': dest_location.id,
            # Value the returned goods at the price the customer was actually
            # invoiced. That is what Odoo uses for an incoming move that has no
            # purchase order behind it, and it keeps the stock valuation and
            # the credit note telling the same story.
            'price_unit': convert(line.price_unit, line.product_uom_id, product.uom_id),
        }
        if product.tracking:
            move_vals['move_line_ids'] = [
                Command.create(values) for values in
                self._prepare_move_line_values(line, qty, dest_location)
            ]
        return move_vals

    def _prepare_move_line_values(self, line, qty, dest_location=None):
        """One operation line per lot, and one per serial number.

        Odoo insists a serial number is worth exactly one unit, so serial
        goods are split into as many lines as there are units; lot-tracked
        goods share a single lot for the whole quantity.
        """
        self.ensure_one()
        dest_location = dest_location or self.dest_location_id
        product = line.product_id
        uom = line.product_uom_id
        if not are_compatible(uom, product.uom_id):
            uom = product.uom_id
        if product.tracking == 'serial':
            total = max(int(qty), 1)
            lots = [self._create_return_lot(product, line, index)
                    for index in range(total)]
            quantities = [1.0] * total
            # A fractional remainder cannot have a serial number of its own,
            # so it joins the first one.
            quantities[0] += qty - total
        else:
            lots = [self._create_return_lot(product, line)]
            quantities = [qty]
        return [
            {
                'product_id': product.id,
                'uom_id': uom.id,
                'quantity': quantity,
                'lot_id': lot.id,
                'lot_name': lot.name,
                'location_id': self.source_location_id.id,
                'location_dest_id': dest_location.id,
                'picked': True,
            }
            for lot, quantity in zip(lots, quantities)
        ]

    def _create_return_lot(self, product, line, index=0):
        """Give tracked goods a lot, so traceability survives the receipt."""
        self.ensure_one()
        if line.lot_id and not index:
            return line.lot_id
        suffix = '' if not index else '-%s' % index
        lot_name = '%s-%s%s' % (
            self.name, product.default_code or product.id, suffix)
        lot = self.env['stock.lot'].search([
            ('name', '=', lot_name), ('product_id', '=', product.id),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not lot:
            lot = self.env['stock.lot'].create({
                'name': lot_name,
                'product_id': product.id,
                'company_id': self.company_id.id,
            })
        if not index:
            line.lot_id = lot
        return lot

    def _get_stock_note(self):
        self.ensure_one()
        return _(
            'Customer goods received against invoice %(invoice)s for pickup '
            '%(pickup)s.\nAccepted: %(accepted)s\nRejected: %(rejected)s\n'
            'Received by %(user)s on %(date)s.',
            invoice=self.invoice_number or self.invoice_id.display_name,
            pickup=self.pickup_id.name,
            accepted=self.total_accepted_qty,
            rejected=self.total_rejected_qty,
            user=self.env.user.display_name,
            date=format_datetime(self.env, fields.Datetime.now()),
        )

    # -------------------------------------------------------------- reversing
    def action_reverse_stock_receipt(self):
        """Manager correction: send accepted goods back out, on the record.

        Stock is never edited or deleted. A proper outgoing transfer is
        created, validated and linked, so both movements stay visible and the
        inventory history still adds up.
        """
        require_group(self.env, MANAGER, _(
            'Only a goods pickup manager can reverse a validated stock receipt.'))
        for receipt in self:
            if not receipt.picking_id or receipt.state != 'done':
                raise UserError(
                    _('%(name)s has no validated stock receipt to reverse.',
                      name=receipt.name)
                )
            if receipt.is_stock_reversed:
                raise UserError(
                    _('%(name)s has already been reversed.', name=receipt.name)
                )
            if receipt.move_ids.filtered(lambda move: move.state == 'posted'):
                raise UserError(
                    _('%(name)s already carries a posted credit note, so its stock '
                      'cannot be reversed. Reverse the credit note first.',
                      name=receipt.name)
                )
            receipt._reverse_stock_picking()
        return True

    def _reverse_stock_picking(self):
        self.ensure_one()
        source = self.picking_id
        reversible = self.line_ids.filtered(
            lambda line: not _qty_is_zero(self.env, line.qty_accepted) and line.stock_move_id)
        if not reversible:
            raise UserError(
                _('%(name)s has no stock moves left to reverse.', name=self.name)
            )
        reverse = self.env['stock.picking'].create({
            'picking_type_id': self.warehouse_id.out_type_id.id,
            'partner_id': self.partner_id.id,
            'location_id': source.location_dest_id.id,
            'location_dest_id': source.location_id.id,
            'origin': self.name,
            'note': _(
                'Reversal of customer goods receipt %(receipt)s for pickup '
                '%(pickup)s.', receipt=self.name, pickup=self.pickup_id.name),
            'move_ids': [Command.create(self._prepare_reverse_move_vals(line))
                         for line in reversible],
        })
        # ``merge=False`` for the same reason as the incoming transfer: the
        # reversal has to keep one move per reversed receipt line.
        reverse.move_ids._action_confirm(merge=False)
        reverse.move_ids.write({'picked': True})
        reverse.with_context(cancel_backorder=True)._action_done()
        self.write({'reverse_picking_id': reverse.id, 'is_stock_reversed': True})
        self.message_post(body=_(
            'Accepted goods reversed back to %(location)s through %(transfer)s. '
            'No stock record was edited or deleted.',
            location=reverse.location_dest_id.display_name,
            transfer=reverse.name))
        return reverse

    def _prepare_reverse_move_vals(self, line):
        """The outgoing move that undoes an accepted receipt line."""
        self.ensure_one()
        original = line.stock_move_id
        convert = self.env['customer.goods.pickup']._convert_qty
        return {
            'description_picking_manual': original.description_picking_manual,
            'product_id': line.product_id.id,
            'product_uom_qty': convert(line.qty_accepted, line.product_uom_id,
                                       original.uom_id),
            'uom_id': original.uom_id.id,
            'location_id': original.location_dest_id.id,
            'location_dest_id': original.location_id.id,
            'price_unit': original.price_unit,
            'origin_returned_move_id': original.id,
        }

    def action_record_rejected_disposition(self):
        """Note what the store intends to do with the refused goods.

        This is the *intent*, recorded on the receipt. The decision that
        actually moves stock is on each quarantine record, because only that
        produces the transfer, the approver and the handover evidence.
        """
        require_group(self.env, (STORE, MANAGER), _(
            'Only store staff can record where refused goods are going.'))
        for receipt in self:
            if receipt.state != 'done':
                raise UserError(
                    _('%(name)s has not been received yet.', name=receipt.name)
                )
            if not receipt.rejected_disposition:
                raise UserError(
                    _('Choose what happens to the refused goods on %(name)s.',
                      name=receipt.name)
                )
            if receipt.rejected_disposition == 'return_to_customer' \
                    and not (receipt.rejected_disposition_notes or '').strip():
                raise UserError(
                    _('Please note how and when the refused goods go back to '
                      '%(customer)s.', name=receipt.name,
                      customer=receipt.partner_id.display_name)
                )
            receipt.write({'rejected_disposition_date': fields.Datetime.now()})
            receipt.message_post(body=_(
                'Refused goods — %(disposition)s. %(notes)s Act on each '
                'quarantine record to move the goods.',
                disposition=receipt.rejected_disposition,
                notes=receipt.rejected_disposition_notes or ''))
        return True

    def action_view_quarantine(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Quarantine Custody'),
            'res_model': 'customer.goods.quarantine',
            'view_mode': 'list,form',
            'domain': [('receipt_id', '=', self.id)],
            'context': {'default_receipt_id': self.id, 'create': False},
        }

    def action_view_quarantine_picking(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Quarantine Receipt'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.quarantine_picking_id.id,
            'target': 'current',
        }

    # -------------------------------------------------------------- notifying
    def _notify_sales_rep(self):
        """Tell the representative what the store decided."""
        self.ensure_one()
        notify = self.env['ir.config_parameter'].sudo().get_bool(
            'customer_goods_pickup.notify_sales_rep', True)
        body = _(
            'Store receiving %(receipt)s for %(pickup)s against invoice '
            '%(invoice)s: %(accepted)s accepted into stock, %(rejected)s rejected.',
            receipt=self.name, pickup=self.pickup_id.name,
            invoice=self.invoice_number or '',
            accepted=self.total_accepted_qty, rejected=self.total_rejected_qty)
        if self.has_rejection:
            reasons = [line.rejection_reason for line in self.line_ids
                       if line.rejection_reason]
            body += '\n' + _('Reasons given: %(reasons)s', reasons='; '.join(reasons))
        self.pickup_id.message_post(
            body=body,
            partner_ids=self.pickup_id.sales_rep_id.partner_id.ids if notify else [],
            subtype_xmlid='mail.mt_note',
        )
        return True

    # ----------------------------------------------------------------- credit
    def action_approve_credit(self):
        """Accountant side: this accepted quantity may be credited."""
        require_group(self.env, (ACCOUNTANT, MANAGER), _(
            'Only an accountant can approve a customer credit.'))
        for receipt in self:
            receipt._check_can_approve_credit()
            receipt.sudo().write({
                'credit_approval_state': 'approved',
                'credit_approval_by_id': self.env.user.id,
                'credit_approval_date': fields.Datetime.now(),
            })
            receipt.message_post(body=_(
                'Credit approved for %(qty)s of accepted goods.',
                qty=receipt.pending_credit_qty))
        return True

    def action_decline_credit(self):
        """Accept the goods, refuse the credit — without touching the stock.

        Goods the store accepted stay in stock and stay on the books; only the
        financial credit is refused, and the refusal is on the record.
        """
        require_group(self.env, (ACCOUNTANT, MANAGER), _(
            'Only an accountant can decline a customer credit.'))
        for receipt in self:
            if receipt.state != 'done':
                raise UserError(
                    _('%(name)s has not been received yet.', name=receipt.name)
                )
            if not (receipt.credit_approval_notes or '').strip():
                raise UserError(
                    _('Please record why the credit is declined on %(name)s.',
                      name=receipt.name)
                )
            if receipt.move_ids.filtered(lambda move: move.state == 'posted'):
                raise UserError(
                    _('%(name)s already has a posted credit note, so the credit '
                      'cannot be declined.', name=receipt.name)
                )
            receipt.write({'credit_approval_state': 'declined'})
            receipt.message_post(body=_(
                'Credit declined: %(notes)s The goods accepted on this receipt '
                'stay in stock.', notes=receipt.credit_approval_notes))
        return True

    def action_reset_credit_approval(self):
        """Manager correction: reopen a credit decision that was not posted."""
        require_group(self.env, MANAGER, _(
            'Only a goods pickup manager can reopen a credit decision.'))
        for receipt in self:
            if receipt.credit_approval_state == 'pending':
                continue
            if receipt.move_ids.filtered(lambda move: move.state == 'posted'):
                raise UserError(
                    _('%(name)s already has a posted credit note; its credit '
                      'cannot be reopened.', name=receipt.name)
                )
            receipt.move_ids.filtered(lambda move: move.state == 'draft').unlink()
            receipt.write({
                'credit_approval_state': 'pending',
                'credit_approval_by_id': False,
                'credit_approval_date': False,
            })
        return True

    def _check_can_approve_credit(self):
        self.ensure_one()
        if self.state != 'done':
            raise UserError(
                _('%(name)s has not been received yet, so there is nothing to '
                  'credit.', name=self.name)
            )
        if not self._get_eligible_credit_lines():
            raise UserError(
                _('Every accepted quantity on %(name)s is already covered by a '
                  'credit note.', name=self.name)
            )
        if self.pickup_id.state == 'cancel':
            raise UserError(
                _('%(pickup)s has been cancelled, so nothing on %(name)s may be '
                  'credited.', pickup=self.pickup_id.name, name=self.name)
            )
        return True

    def _get_eligible_credit_lines(self):
        """Receipt lines with accepted quantity that no credit note covers."""
        self.ensure_one()
        return self.line_ids.filtered(lambda line: not _qty_is_zero(self.env, line.qty_to_credit))

    def action_create_credit_note(self):
        """Build the draft customer credit note for the accepted quantity.

        Uses Odoo's own reversal of the original invoice, so customer,
        company, currency, price, discount and tax are whatever the invoice
        said. The note is left in draft — posting stays a separate, deliberate
        act by a user with the standard accounting permission.
        """
        require_group(self.env, (ACCOUNTANT, MANAGER), _(
            'Only an accountant can raise a customer credit note.'))
        for receipt in self:
            receipt._check_can_approve_credit()
            if receipt.credit_approval_state == 'pending':
                raise UserError(
                    _('The credit on %(name)s has not been approved yet. Review '
                      'the accepted quantity and approve it first.',
                      name=receipt.name)
                )
            if receipt.credit_approval_state == 'declined':
                raise UserError(
                    _('The credit on %(name)s has been declined.', name=receipt.name)
                )
            receipt._create_credit_move()
        return self._get_credit_move_action()

    def _lock_credit_positions(self):
        """Serialise two accountants working on the same accepted quantity.

        Takes row locks on the invoice lines first and then on this receipt's
        lines, in that order everywhere, so two credit notes raised at the same
        moment queue up instead of both reading "6 units are eligible". The
        figures are then re-read from the database rather than from a cache
        that a colleague's committed transaction may already have moved.
        """
        self.ensure_one()
        self.env.flush_all()
        invoice_line_ids = self.invoice_id.invoice_line_ids.ids
        if invoice_line_ids:
            self.env.cr.execute(
                'SELECT id FROM account_move_line WHERE id IN %s FOR UPDATE',
                [tuple(invoice_line_ids)])
        receipt_line_ids = self.line_ids.ids
        if receipt_line_ids:
            self.env.cr.execute(
                'SELECT id FROM customer_goods_receipt_line WHERE id IN %s '
                'FOR UPDATE', [tuple(receipt_line_ids)])
        self.line_ids.invalidate_recordset([
            'qty_accepted', 'qty_credited', 'qty_credit_pending', 'qty_to_credit',
            'qty_reinspected_accepted'])
        self.env.flush_all()
        return True

    def _check_invoice_credit_bound(self, reverse):
        """Keep the sum of credits inside the invoice's own value.

        Line-by-line quantity limits are the primary control, but they are
        expressed in product units while the customer is made whole in money,
        and unit-price rounding can add up. This is the second, monetary bound:
        everything already posted against the invoice, plus what this note adds,
        may not exceed the invoice.
        """
        self.ensure_one()
        invoice = self.invoice_id
        currency = invoice.currency_id
        reverse.flush_recordset()
        reverse.invalidate_recordset(['amount_total'])
        self.env.flush_all()
        already = sum(
            abs(move.amount_total) for move in invoice.reversal_move_ids
            if move.move_type == 'out_refund' and move.state == 'posted')
        proposed = abs(reverse.amount_total)
        ceiling = abs(invoice.amount_total)
        if float_compare(already + proposed, ceiling,
                         precision_rounding=currency.rounding) > 0:
            raise UserError(_(
                'The credit notes against invoice %(invoice)s total %(total)s, '
                'which is more than the %(ceiling)s the customer was invoiced. '
                'Nothing was created.',
                invoice=invoice.display_name,
                total=currency.round(already + proposed),
                ceiling=currency.round(ceiling)))
        return True

    def _create_credit_move(self):
        """Reverse the invoice, then cut the reversal down to what is eligible.

        The whole read of "what is still eligible" happens under a row lock
        taken *before* it, and the bound is re-checked *after* the note has
        been linked to its receipt lines — which is the point at which the
        quantity on the link starts counting against the next note. A losing
        transaction therefore fails here, with nothing half-created.
        """
        self.ensure_one()
        invoice = self.invoice_id
        if not invoice or invoice.state != 'posted':
            raise UserError(
                _('The original invoice of %(name)s is no longer posted, so no '
                  'credit note can be raised against it.', name=self.name)
            )
        self._lock_credit_positions()
        eligible = self._get_eligible_credit_lines()
        convert = self.env['customer.goods.pickup']._convert_qty
        wanted, receipt_line_by_invoice_line = {}, {}
        for line in eligible:
            invoice_line = line.invoice_line_id
            wanted[invoice_line.id] = wanted.get(invoice_line.id, 0.0) \
                + convert(line.qty_to_credit, line.product_uom_id,
                          invoice_line.product_uom_id)
            receipt_line_by_invoice_line.setdefault(invoice_line.id, line)

        reverse = invoice.with_context(include_business_fields=True)._reverse_moves([{
            'invoice_date': fields.Date.context_today(self),
            'ref': _('Customer goods returned — %(pickup)s / %(receipt)s',
                     pickup=self.pickup_id.name, receipt=self.name),
            'payment_reference': invoice.payment_reference,
        }])

        origin_lines = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product')
        reverse_lines = reverse.line_ids.filtered(
            lambda line: line.display_type == 'product')
        if len(origin_lines) != len(reverse_lines):
            reverse.unlink()
            raise UserError(
                _('The credit note for %(name)s could not be lined up with '
                  'invoice %(invoice)s, so nothing was created. Please raise '
                  'the credit note manually.', name=self.name,
                  invoice=invoice.display_name)
            )
        pairs = list(zip(origin_lines, reverse_lines))
        remove = [rev.id for origin, rev in pairs
                  if _qty_is_zero(self.env, wanted.get(origin.id, 0.0))]
        adjust = [(rev.id, wanted[origin.id]) for origin, rev in pairs
                  if not _qty_is_zero(self.env, wanted.get(origin.id, 0.0))]
        link = {rev.id: receipt_line_by_invoice_line[origin.id].id
                for origin, rev in pairs if origin.id in receipt_line_by_invoice_line}
        if remove:
            reverse.line_ids = [Command.delete(line_id) for line_id in remove]
        if adjust:
            reverse.line_ids = [
                Command.update(line_id, {'quantity': qty}) for line_id, qty in adjust
            ]
        # Guard the bound while the note is still a draft: the quantity on a
        # linked line is what stops the same accepted goods being credited
        # twice, and a draft note is still editable by an accountant who may
        # have a reason to adjust it.
        reverse.line_ids.filtered(lambda line: line.goods_pickup_receipt_line_id) \
            ._check_goods_credit_quantity()
        reverse.write({
            'goods_pickup_id': self.pickup_id.id,
            'goods_pickup_receipt_id': self.id,
        })
        # Attach each remaining line to the receipt line it credits, so the
        # credited quantity can be read back from the accounting side and the
        # same accepted quantity can never be credited twice. Re-running the
        # bound check *after* the link is set is what makes two concurrent
        # notes impossible: the second one finds the first line already counted.
        for reverse_line in reverse.line_ids.filtered(
                lambda line: line.display_type == 'product'):
            reverse_line.goods_pickup_receipt_line_id = link.get(reverse_line.id)
        try:
            reverse.line_ids.filtered('goods_pickup_receipt_line_id') \
                ._check_goods_credit_quantity()
            self._check_invoice_credit_bound(reverse)
        except (UserError, ValidationError):
            # Nothing half-built: the note never becomes real.
            reverse.sudo().button_draft()
            reverse.sudo().unlink()
            raise
        self.message_post(body=_(
            'Draft credit note %(move)s created for %(qty)s of accepted goods '
            'against invoice %(invoice)s.',
            move=reverse.name or _('(not numbered yet)'),
            qty=sum(eligible.mapped('qty_to_credit')),
            invoice=self.invoice_number or invoice.display_name))
        return reverse

    def _get_credit_move_action(self):
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Notes'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.mapped('move_ids').ids)],
            'context': {'create': False},
        }

    def action_view_credit_note(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Note'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
            'target': 'current',
        }

    # ------------------------------------------------------------- navigation
    def action_view_receipt_form(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Store Receipt'),
            'res_model': 'customer.goods.receipt',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
        }

    def action_view_picking(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Stock Receipt'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.picking_id.id,
            'target': 'current',
        }

    def action_view_reverse_picking(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Reversal Transfer'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.reverse_picking_id.id,
            'target': 'current',
        }

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

    def action_view_pickup(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Goods Pickup'),
            'res_model': 'customer.goods.pickup',
            'view_mode': 'form',
            'res_id': self.pickup_id.id,
            'target': 'current',
        }