import logging
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.fields import Domain

from ..access import MANAGER, REP, STORE, require_group
from ..units import (compare as _qty_compare, convert, greater as _qty_gt,
                     is_zero as _qty_is_zero)

_logger = logging.getLogger(__name__)

# Quantities are compared with Odoo's own "Product Unit" precision, never with
# plain float equality, so 0.1 + 0.2 behaves the way a user expects.



class CustomerGoodsPickup(models.Model):
    """Goods a sales representative collected from a customer against an invoice.

    One pickup is one trip: the rep names the invoice the goods came off, lists
    what was actually taken, and confirms collection. That confirmation is
    deliberately inert — it moves no stock and posts nothing. The store picks
    the pickup up from here, inspects it, and only what the store *accepts*
    ever becomes sellable stock, and only what the store accepts ever becomes
    credit-note eligible.
    """

    _name = 'customer.goods.pickup'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Customer Goods Pickup'
    _order = 'collection_date desc, id desc'
    _rec_names_search = ['name', 'partner_id', 'invoice_id']
    # Turns the ``check_company=True`` fields below into a hard rule, so a
    # pickup cannot be pointed at another company's invoice or warehouse.
    _check_company_auto = True

    # ------------------------------------------------------------- reference
    name = fields.Char(
        string='Pickup Reference',
        required=True,
        copy=False,
        default=lambda self: _('New'),
        index=True,
        tracking=True,
        help='Unique reference of this pickup, e.g. PICK-00001.',
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
        index=True, tracking=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )

    # ----------------------------------------------------------------- who
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True, tracking=True,
        domain="[('customer_rank', '>', 0)]",
        check_company=True,
        help='Customer the goods were collected from.',
    )
    sales_rep_id = fields.Many2one(
        'res.users', string='Sales Representative', required=True,
        default=lambda self: self.env.user, index=True, tracking=True,
        help='The representative who collected the goods.',
    )
    user_id = fields.Many2one(
        'res.users', string='Responsible', default=lambda self: self.env.user,
        index=True, tracking=True, copy=False,
        help='Who is currently dealing with this pickup.',
    )

    # -------------------------------------------------------------- invoice
    invoice_id = fields.Many2one(
        'account.move', string='Original Customer Invoice', required=True,
        index=True, check_company=True, tracking=True,
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), ('partner_id', 'child_of', partner_id)]",
        help='The posted customer invoice the collected goods belong to. Only '
             'posted customer invoices of this customer and company are offered.',
    )
    invoice_number = fields.Char(
        string='Invoice Number', compute='_compute_invoice_number', store=True,
        index=True,
        help='Number of the original customer invoice, shown everywhere the '
             'pickup appears so nobody has to open another document.',
    )
    invoice_date = fields.Date(
        string='Invoice Date', related='invoice_id.invoice_date', store=True,
        readonly=True,
    )
    invoice_amount_total = fields.Monetary(
        string='Invoiced Total', related='invoice_id.amount_total', readonly=True,
    )
    invoice_currency_id = fields.Many2one(
        'res.currency', related='invoice_id.currency_id', readonly=True,
        string='Invoice Currency',
    )

    # ------------------------------------------------------------ collection
    collection_date = fields.Datetime(
        string='Collection Date', index=True, tracking=True, copy=False,
        help='When the representative collected the goods from the customer.',
    )
    collection_confirmed_by_id = fields.Many2one(
        'res.users', string='Collection Confirmed By', readonly=True, copy=False,
    )
    collection_notes = fields.Char(
        string='Collection Notes',
        help='Short note about the collection, e.g. "collected at the shop counter".',
    )
    pickup_reason = fields.Selection(
        selection=[
            ('customer_request', 'Customer Request'),
            ('damaged', 'Damaged'),
            ('wrong_item', 'Wrong Item Supplied'),
            ('not_fit', 'Does Not Fit / Not Needed'),
            ('quality_issue', 'Quality Issue'),
            ('duplicate', 'Duplicate Purchase'),
            ('other', 'Other'),
        ],
        string='Pickup Reason', required=True, default='customer_request',
        tracking=True,
        help='Why the goods are being taken back.',
    )
    pickup_reason_notes = fields.Char(
        string='Reason Details',
        help='Filled in when the pickup reason is "Other".',
    )
    notes = fields.Text(
        string='Notes',
        help='Anything the store should know before it receives these goods.',
    )

    # -------------------------------------------------------------- logistics
    warehouse_id = fields.Many2one(
        'stock.warehouse', string='Destination Warehouse / Store', required=True,
        check_company=True, tracking=True,
        help='The store the goods are being carried to. Accepted goods are '
             'received into this warehouse.',
    )
    dest_location_id = fields.Many2one(
        'stock.location', string='Destination Stock Location', required=True,
        check_company=True, tracking=True,
        domain="[('usage', '=', 'internal'), ('warehouse_id', '=', warehouse_id)]",
        help='The stock location accepted goods are received into.',
    )
    source_location_id = fields.Many2one(
        'stock.location', string='Source Location', readonly=True, check_company=True,
        help='The customer location the goods travel from.',
    )

    # -------------------------------------------------------------- workflow
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('collected', 'Collected'),
            ('in_transit', 'In Transit'),
            ('awaiting_receipt', 'Awaiting Store Receipt'),
            ('partially_received', 'Partially Received'),
            ('received', 'Received'),
            ('partially_rejected', 'Partially Rejected'),
            ('rejected', 'Rejected'),
            ('cancel', 'Cancelled'),
        ],
        string='Status', default='draft', required=True, copy=False, tracking=True,
        index=True,
    )
    cancel_reason = fields.Text(
        string='Cancellation Reason', readonly=True, copy=False, tracking=True,
    )
    cancel_date = fields.Datetime(string='Cancelled On', readonly=True, copy=False)

    # ------------------------------------------------------------------ lines
    line_ids = fields.One2many(
        'customer.goods.pickup.line', 'pickup_id', string='Goods Collected',
        copy=True,
    )
    receipt_ids = fields.One2many(
        'customer.goods.receipt', 'pickup_id', string='Store Receipts',
    )
    receipt_count = fields.Integer(
        string='Receipts', compute='_compute_receipt_count',
    )
    credit_note_ids = fields.One2many(
        'account.move', 'goods_pickup_id', string='Related Credit Notes',
    )
    credit_note_count = fields.Integer(
        string='Credit Notes', compute='_compute_credit_note_count',
    )

    # ----------------------------------------------------------------- totals
    total_collected_qty = fields.Float(
        string='Total Collected', compute='_compute_totals', store=True,
        digits='Product Unit',
        help='Total quantity the representative reported collecting.',
    )
    total_received_qty = fields.Float(
        string='Total Received', compute='_compute_totals', store=True,
        digits='Product Unit',
    )
    total_accepted_qty = fields.Float(
        string='Total Accepted', compute='_compute_totals', store=True,
        digits='Product Unit',
        help='Quantity the store accepted. This is the only quantity that can '
             'become sellable stock and the only quantity that can be credited.',
    )
    total_rejected_qty = fields.Float(
        string='Total Rejected', compute='_compute_totals', store=True,
        digits='Product Unit',
    )
    total_credited_qty = fields.Float(
        string='Total Credited', compute='_compute_totals', store=True,
        digits='Product Unit',
        help='Quantity already covered by a posted customer credit note.',
    )
    total_credit_amount = fields.Monetary(
        string='Credited Amount', compute='_compute_credit_amount',
        currency_field='currency_id',
    )

    # -------------------------------------------------------- invoice choice
    @api.model
    def _eligible_invoice_domain(self, partner=None):
        """Only posted customer invoices of this company may back a pickup.

        Used by the field domain, by the invoice picker and by the onchange
        that fills the customer in from the chosen invoice.
        """
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('company_id', '=', self.env.company.id),
        ]
        if partner:
            domain = Domain.AND([
                domain, [('partner_id', 'child_of', partner.commercial_partner_id.id)],
            ])
        return domain

    @api.constrains('invoice_id', 'partner_id', 'company_id')
    def _check_invoice_is_eligible(self):
        for pickup in self:
            invoice = pickup.invoice_id
            if not invoice:
                continue
            if invoice.move_type != 'out_invoice':
                raise ValidationError(
                    _('%(name)s: %(invoice)s is not a customer invoice.',
                      name=pickup.name, invoice=invoice.display_name)
                )
            if invoice.state != 'posted':
                raise ValidationError(
                    _('%(name)s: %(invoice)s is not posted. A goods pickup can '
                      'only be raised against a posted customer invoice.',
                      name=pickup.name, invoice=invoice.display_name)
                )
            if invoice.company_id != pickup.company_id:
                raise ValidationError(
                    _('%(name)s: %(invoice)s belongs to company %(other)s, not '
                      '%(mine)s.', name=pickup.name, invoice=invoice.display_name,
                      other=invoice.company_id.display_name,
                      mine=pickup.company_id.display_name)
                )
            if not invoice.commercial_partner_id \
                    or invoice.commercial_partner_id != pickup.partner_id.commercial_partner_id:
                raise ValidationError(
                    _('%(name)s: the customer of %(invoice)s is not the same as '
                      'the customer of this pickup.', name=pickup.name,
                      invoice=invoice.display_name)
                )

    @api.constrains('pickup_reason', 'pickup_reason_notes')
    def _check_pickup_reason_details(self):
        for pickup in self:
            if pickup.pickup_reason == 'other' \
                    and not (pickup.pickup_reason_notes or '').strip():
                raise ValidationError(
                    _('Please describe the pickup reason on %(name)s.', name=pickup.name)
                )

    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        """Restrict invoice selection to the chosen customer."""
        if self.partner_id:
            return {'domain': {'invoice_id': [
                ('move_type', '=', 'out_invoice'),
                ('state', '=', 'posted'),
                ('partner_id', 'child_of', self.partner_id.id),
                ('company_id', '=', self.company_id.id),
            ]}}
        return {'domain': {'invoice_id': [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('company_id', '=', self.company_id.id),
        ]}}

    @api.onchange('invoice_id')
    def _onchange_invoice_id(self):
        """Fill the whole line list with the chosen invoice's product lines.

        Choosing the invoice is the only decision here: every product line of
        that invoice is offered immediately, so the representative just says how
        many of each one they are taking instead of opening a picker to add
        rows one at a time. Anything already typed for a line survives, because
        re-choosing the invoice is more likely to correct the invoice than to
        abandon what was counted.

        The same commands are built for a saved write, via
        ``_sync_lines_for_invoice()``, so a record created or amended from code
        ends up with the same lines a rep would have seen on screen.
        """
        self.line_ids = self._sync_lines_for_invoice()

    def _sync_lines_for_invoice(self):
        """Commands that make ``line_ids`` cover every invoice product line.

        An empty result clears the list, so pointing the pickup away from an
        invoice takes the lines with it — Goods must stay tied to the invoice
        they were taken off.
        """
        self.ensure_one()
        invoice = self.invoice_id
        if not invoice:
            return [(5, 0, 0)]

        entered = {
            line.invoice_line_id.id: line.qty_collected
            for line in self.line_ids if line.invoice_line_id
        }
        commands = [(5, 0, 0)]
        sequence = 10
        for invoice_line in invoice.invoice_line_ids:
            # Sections and notes carry no goods and no unit.
            if invoice_line.display_type != 'product' or not invoice_line.product_id:
                continue
            commands.append((0, 0, {
                'invoice_line_id': invoice_line.id,
                'product_id': invoice_line.product_id.id,
                'name': invoice_line.name,
                'product_uom_id': invoice_line.product_uom_id.id,
                'return_reason': self.pickup_reason,
                'qty_collected': entered.get(invoice_line.id, 0.0),
                'sequence': sequence,
            }))
            sequence += 10
        return commands

    # ---------------------------------------------------------------- writes
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                # next_by_code resolves the company from the environment, so run
                # it in the record's own company for multi-company setups.
                company = self.env['res.company'].browse(
                    vals.get('company_id') or self.env.company.id
                )
                vals['name'] = self.with_company(company).env['ir.sequence'].next_by_code(
                    'customer.goods.pickup'
                ) or _('New')
        # Deliberately no line filling here. Choosing the invoice on the form
        # is what fills the list, through the onchange, and a caller that
        # supplies its own lines -- a test, a script, an import -- means them.
        # Filling on create as well would hand it the invoice's lines *and*
        # leave its own, so every invoice line would be claimed twice.
        return super().create(vals_list)

    def action_reload_invoice_lines(self):
        """Re-offer every product line of the invoice, keeping what was typed.

        Filling the list is also what choosing the invoice does in the browser;
        this is the same thing on demand, for when the invoice has been amended
        since the list was first drawn.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(
                _('%(name)s is %(state)s; its lines can only be reloaded while '
                  'it is a draft.', name=self.name, state=self._state_label())
            )
        self.write({'line_ids': self._sync_lines_for_invoice()})
        return True

    def _auto_lines_on_write(self, vals):
        """Re-offer the invoice's lines whenever the invoice itself changes.

        Only when the caller did not bring lines of its own: repointing a draft
        at a different invoice has to take the old invoice's lines with it, and
        a caller writing both at once has already said what it wants.
        """
        if 'line_ids' in vals or 'invoice_id' not in vals:
            return
        for record in self:
            record.write({'line_ids': record._sync_lines_for_invoice()})

    def write(self, vals):
        # Freeze a confirmed pickup. Anything that changes what was collected
        # after the rep has handed the goods over has to go through an explicit
        # correction, never a silent edit. ``sales_rep_id`` is in the list
        # because the record rule is built on it: letting a representative
        # hand their pickup to a colleague would silently move a document
        # out of their own scope.
        watched = ('line_ids', 'invoice_id', 'partner_id', 'pickup_reason',
                   'pickup_reason_notes', 'collection_notes', 'sales_rep_id')
        if any(field in vals for field in watched):
            for pickup in self:
                if pickup.state in ('draft', 'cancel'):
                    continue
                if pickup.env.su or self.env.user.has_group(
                        'customer_goods_pickup.group_goods_pickup_manager'):
                    continue
                raise UserError(
                    _('%(name)s is %(state)s. Send it back to draft before changing '
                      'what was collected from the customer.',
                      name=pickup.name, state=pickup._state_label())
                )
        if 'pickup_reason' in vals and vals['pickup_reason'] == 'other':
            for pickup in self:
                if not (pickup.pickup_reason_notes
                        or vals.get('pickup_reason_notes')):
                    raise UserError(
                        _('Please describe the pickup reason on %(name)s.',
                          name=pickup.name)
                    )
        self._auto_lines_on_write(vals)
        result = super().write(vals)
        return result

    def unlink(self):
        for pickup in self:
            if pickup.state != 'draft':
                raise UserError(
                    _('%(name)s is %(state)s and is part of the audit trail. '
                      'Cancel it instead of deleting it.',
                      name=pickup.name, state=pickup._state_label())
                )
        return super().unlink()

    # -------------------------------------------------------------- computes
    def _state_label(self):
        self.ensure_one()
        selection = dict(self._fields['state']._description_selection(self.env))
        return selection.get(self.state) or self.state

    @api.depends('invoice_id')
    def _compute_invoice_number(self):
        for pickup in self:
            pickup.invoice_number = pickup.invoice_id.name or ''

    @api.depends('receipt_ids.state')
    def _compute_receipt_count(self):
        data = self.env['customer.goods.receipt']._read_group(
            [('pickup_id', 'in', self.ids), ('state', '!=', 'cancel')],
            ['pickup_id'], ['__count'],
        )
        counts = {pickup.id: count for pickup, count in data}
        for record in self:
            record.receipt_count = counts.get(record.id, 0)

    @api.depends('credit_note_ids')
    def _compute_credit_note_count(self):
        data = self.env['account.move']._read_group(
            [('goods_pickup_id', 'in', self.ids)], ['goods_pickup_id'], ['__count'],
        )
        counts = {pickup.id: count for pickup, count in data}
        for record in self:
            record.credit_note_count = counts.get(record.id, 0)

    @api.depends('line_ids.qty_collected', 'receipt_ids.state',
                 'receipt_ids.line_ids.qty_received',
                 'receipt_ids.line_ids.qty_accepted',
                 'receipt_ids.line_ids.qty_rejected',
                 'receipt_ids.line_ids.qty_credited')
    def _compute_totals(self):
        """Roll up the pickup's operational figures.

        The receipt quantities come from a single grouped query rather than
        from walking ``receipt_ids.line_ids``: a pickup can have many
        receipts and each of those many lines, and one round trip per pickup
        is enough. The line fields are still declared above, so changing a
        count still marks these figures for recomputation.
        """
        done = self.env['customer.goods.receipt'].search([
            ('pickup_id', 'in', self.ids), ('state', '=', 'done'),
        ]) if self.ids else self.env['customer.goods.receipt']
        totals = defaultdict(
            lambda: [0.0, 0.0, 0.0, 0.0])
        if done:
            for (receipt, received, accepted, rejected, credited) in \
                    self.env['customer.goods.receipt.line']._read_group(
                        [('receipt_id', 'in', done.ids)],
                        ['receipt_id'],
                        ['qty_received:sum', 'qty_accepted:sum',
                         'qty_rejected:sum', 'qty_credited:sum']):
                totals[receipt.id] = [received, accepted, rejected, credited]
        for pickup in self:
            rows = [totals[receipt.id]
                    for receipt in pickup.receipt_ids
                    if receipt.id in totals]
            pickup.total_collected_qty = sum(
                pickup.line_ids.mapped('qty_collected'))
            pickup.total_received_qty = sum(row[0] for row in rows)
            pickup.total_accepted_qty = sum(row[1] for row in rows)
            pickup.total_rejected_qty = sum(row[2] for row in rows)
            pickup.total_credited_qty = sum(row[3] for row in rows)
        return True

    @api.depends('credit_note_ids.state', 'credit_note_ids.amount_total')
    def _compute_credit_amount(self):
        for pickup in self:
            posted = pickup.credit_note_ids.filtered(lambda move: move.state == 'posted')
            pickup.total_credit_amount = -sum(posted.mapped('amount_total'))

    # ---------------------------------------------------------- credit notes
    def action_view_credit_notes(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Credit Notes'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('goods_pickup_id', '=', self.id)],
            'context': {'default_move_type': 'out_refund', 'create': False},
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

    def action_view_receipts(self):
        self.ensure_one()
        action = self.env.ref(
            'customer_goods_pickup.action_customer_goods_receipt_all')._get_action_dict()
        action['domain'] = [('pickup_id', '=', self.id)]
        action['context'] = {'default_pickup_id': self.id}
        return action

    # ------------------------------------------------------------- state flow
    def _check_can_confirm_collection(self):
        """Only the state is checked here.

        Whether anything was actually collected cannot be answered until the
        untouched lines have been dropped, so that is asked separately, after.
        """
        self.ensure_one()
        if self.state != 'draft':
            raise UserError(
                _('Only a draft pickup can have its collection confirmed. '
                  '%(name)s is %(state)s.', name=self.name, state=self._state_label())
            )

    def _check_something_was_collected(self):
        """At least one line has to have survived the drop.

        A pickup the representative filled in nothing on is not a collection of
        nothing -- it is a pickup that was not filled in, and it must not be
        able to go out as one.
        """
        self.ensure_one()
        if not self.line_ids:
            raise UserError(
                _('Please enter the quantity collected on at least one line of '
                  '%(name)s before confirming the collection.', name=self.name)
            )

    def _drop_untouched_lines(self):
        """Remove the lines the representative left at zero.

        Every product line of the invoice is offered, so a zero says "not taking
        this one". Deleting those here is what stops the form asking the rep to
        hover a delete button over each row they did not fill in, and it keeps
        the ledger from counting a zero as reserved by this pickup.
        """
        self.ensure_one()
        untouched = self.line_ids.filtered(
            lambda line: _qty_is_zero(self.env, line.qty_collected))
        if untouched:
            untouched.unlink()
            self.invalidate_recordset(['line_ids', 'total_collected_qty'])
        return True

    def _check_collected_quantities(self):
        """Re-check every line against what is still collectable right now.

        The stored figures were computed when the line was typed; by the time
        collection is confirmed a colleague may have taken the same invoiced
        quantity. The whole check runs inside this transaction, with the
        invoice lines locked, so two reps confirming at the same moment cannot
        both win.
        """
        self.env.flush_all()
        self._refresh_invoice_positions(self.mapped('invoice_id'))
        self._lock_invoice_lines()
        credited = self._get_invoice_line_credited_map()
        reserved = self._get_invoice_line_reserved_map()
        for pickup in self:
            for line in pickup.line_ids:
                invoice_line = line.invoice_line_id
                if not invoice_line:
                    raise ValidationError(
                        _('Line %(name)s of %(pickup)s has no invoice line.',
                          name=line.id, pickup=pickup.name)
                    )
                if _qty_is_zero(self.env, line.qty_collected):
                    # Only reachable through a direct caller that has not gone
                    # through _drop_untouched_lines; a zero carries no goods.
                    continue
                # Every figure is brought into the unit the line uses before
                # they are compared, so a dozen and two are never added to
                # twenty-four and believed.
                uom = line.product_uom_id or invoice_line.product_uom_id
                invoice_uom = invoice_line.product_uom_id
                invoiced = convert(invoice_line.quantity or 0.0, invoice_uom, uom)
                credited_qty = convert(
                    credited.get(invoice_line.id, 0.0), invoice_uom, uom)
                reserved_qty = convert(
                    reserved.get(invoice_line.id, 0.0), invoice_uom, uom)
                eligible = invoiced - credited_qty - reserved_qty
                if _qty_gt(self.env, line.qty_collected, eligible):
                    raise ValidationError(
                        _('%(product)s on %(name)s: only %(eligible)s %(uom)s are '
                          'still collectable against invoice %(invoice)s '
                          '(%(invoiced)s invoiced, %(credited)s already credited, '
                          '%(reserved)s on other open pickups), but %(asked)s were '
                          'entered.',
                          product=line.product_id.display_name, name=pickup.name,
                          eligible=uom.round(eligible), uom=uom.name,
                          invoice=pickup.invoice_number,
                          invoiced=uom.round(invoiced),
                          credited=uom.round(credited_qty),
                          reserved=uom.round(reserved_qty),
                          asked=uom.round(line.qty_collected))
                    )

    def _lock_invoice_lines(self):
        """Take a row lock on the invoice lines so two confirmations serialise.

        Business logic stays in the ORM; this is only the transaction-level
        guard, the same pattern ``account.move.line`` uses before it reads a
        residual.
        """
        ids = self.invoice_id.invoice_line_ids.ids
        if ids:
            self.env.cr.execute(
                'SELECT id FROM account_move_line WHERE id IN %s FOR UPDATE',
                [tuple(ids)],
            )

    def action_confirm_collection(self):
        """Representative side: the goods are in the bag.

        Deliberately does nothing to stock and nothing to the books. It stamps
        who confirmed and when, and freezes the lines.
        """
        require_group(self.env, (REP, MANAGER), _(
            'Only a sales representative can confirm that goods were collected '
            'from a customer.'))
        for pickup in self:
            pickup._check_can_confirm_collection()
            # Drop first, ask second: "was anything collected at all" is only
            # answerable once the lines nobody filled in are gone.
            pickup._drop_untouched_lines()
            pickup._check_something_was_collected()
            pickup._check_collected_quantities()
        self.write({
            'state': 'collected',
            'collection_confirmed_by_id': self.env.user.id,
            'collection_date': fields.Datetime.now(),
        })
        return True

    def action_start_transit(self):
        require_group(self.env, (REP, MANAGER), _(
            'Only a sales representative can mark a pickup as in transit.'))
        for pickup in self:
            if pickup.state != 'collected':
                raise UserError(
                    _('Only a collected pickup can be put in transit. %(name)s is '
                      '%(state)s.', name=pickup.name, state=pickup._state_label())
                )
        self.write({'state': 'in_transit'})
        return True

    def action_start_receiving(self):
        """Store side: goods presented at the counter, open a receiving sheet."""
        require_group(self.env, (STORE, MANAGER), _(
            'Only store staff can open a goods receiving screen.'))
        self.ensure_one()
        if self.state not in ('collected', 'in_transit', 'partially_received'):
            raise UserError(
                _('%(name)s is %(state)s, so there is nothing for the store to '
                  'receive.', name=self.name, state=self._state_label())
            )
        if not self._get_outstanding_lines():
            raise UserError(
                _('Everything collected on %(name)s has already been presented '
                  'at the store.', name=self.name)
            )
        wizard = self.env['customer.goods.receipt.receive'].create({
            'pickup_id': self.id,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Store Receiving'),
            'res_model': 'customer.goods.receipt.receive',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def action_cancel_draft(self):
        self.ensure_one()
        require_group(self.env, (REP, MANAGER), _(
            'Only a sales representative can discard their own draft pickup.'))
        if self.state != 'draft':
            raise UserError(
                _('Only a draft pickup can be discarded. %(name)s is %(state)s.',
                  name=self.name, state=self._state_label())
            )
        return self.write({
            'state': 'cancel',
            'cancel_reason': _('Draft abandoned by the sales representative'),
            'cancel_date': fields.Datetime.now(),
        })

    def action_reset_to_draft(self):
        """Manager correction: send an un-received pickup back to the rep."""
        self.ensure_one()
        require_group(self.env, MANAGER, _(
            'Only a goods pickup manager can send a confirmed pickup back to '
            'draft.'))
        if self.state not in ('collected', 'in_transit', 'awaiting_receipt'):
            raise UserError(
                _('%(name)s is %(state)s; only an unreceived pickup can go back '
                  'to draft.', name=self.name, state=self._state_label())
            )
        if self.receipt_ids.filtered(lambda receipt: receipt.state == 'done'):
            raise UserError(
                _('%(name)s already has a validated store receipt. A manager must '
                  'reverse that receipt first.', name=self.name)
            )
        self.receipt_ids.filtered(lambda receipt: receipt.state == 'draft').unlink()
        return self.write({
            'state': 'draft', 'collection_confirmed_by_id': False,
        })

    # ------------------------------------------------------------- receiving
    @api.model
    def _refresh_invoice_positions(self, invoices):
        """Re-derive the invoice-position figures for every open pickup.

        Those figures depend on other people's pickups and on credit notes, so
        a value cached earlier in the same request can be out of date by the
        time somebody makes a decision. Every decision point refreshes first
        and then works from what is actually in the database.
        """
        invoices = invoices if invoices else self.browse()
        if not invoices:
            return True
        pickups = self.search([
            ('invoice_id', 'in', invoices.ids),
            ('state', 'not in', ('cancel',)),
        ])
        lines = pickups.mapped('line_ids')
        if not lines:
            return True
        names = ('invoiced_qty', 'credited_qty', 'outstanding_pickup_qty',
                 'eligible_qty')
        lines.invalidate_recordset(list(names))
        self.env.flush_all()
        return True

    def _get_outstanding_lines(self):
        """Pickup lines still waiting to be presented at the store."""
        self.ensure_one()
        outstanding = self.env['customer.goods.pickup.line']
        for line in self.line_ids:
            if _qty_gt(self.env, line.qty_collected, line.qty_received_total):
                outstanding |= line
        return outstanding

    def _action_refresh_receiving_state(self):
        """Recompute the pickup status from what the store has actually done.

        The four things the business cares about stay apart: collection lives
        in ``collection_date``, physical presentation in the receipts, stock
        acceptance in ``total_accepted_qty`` and the accounting credit on the
        credit note.
        """
        for pickup in self:
            if pickup.state in ('draft', 'cancel'):
                continue
            receipts = pickup.receipt_ids.filtered(lambda receipt: receipt.state == 'done')
            if not receipts:
                continue
            lines = receipts.line_ids
            received = sum(lines.mapped('qty_received'))
            accepted = sum(lines.mapped('qty_accepted'))
            rejected = sum(lines.mapped('qty_rejected'))
            collected = sum(pickup.line_ids.mapped('qty_collected'))
            if _qty_gt(self.env, collected, received):
                state = 'partially_received'
            elif _qty_gt(self.env, rejected, 0.0) and _qty_is_zero(self.env, accepted):
                state = 'rejected'
            elif _qty_gt(self.env, rejected, 0.0):
                state = 'partially_rejected'
            else:
                state = 'received'
            if state != pickup.state:
                pickup.state = state
        return True

    # ------------------------------------------- invoice-line quantity ledger
    def _get_posted_credit_by_pickup(self, pickups=None):
        """Return ``{invoice_line_id: {pickup_id: qty}}`` for posted credits.

        Only notes raised through this module appear here, because only they
        keep the exact link back to the receipt line they settle. Notes raised
        elsewhere are handled separately, by product, in
        ``_get_invoice_line_credited_map``.

        ``pickups`` narrows the search to a few pickups; leave it empty to cover
        every pickup of this invoice, which is what the eligibility arithmetic
        needs. A credit raised from one pickup is money the customer is no
        longer owed for that invoiced quantity, whoever raised it.
        """
        domain = [
            ('move_id.state', '=', 'posted'),
            ('display_type', '=', 'product'),
            ('goods_pickup_receipt_line_id', '!=', False),
        ]
        if pickups:
            domain.append(('goods_pickup_id', 'in', pickups.ids))
        result = defaultdict(lambda: defaultdict(float))
        for line in self.env['account.move.line'].sudo().search(domain):
            invoice_line = line.goods_pickup_receipt_line_id.invoice_line_id
            if not invoice_line:
                continue
            result[invoice_line.id][line.goods_pickup_id.id] += self._convert_qty(
                line.quantity, line.product_uom_id, invoice_line.product_uom_id)
        return result

    def _get_invoice_line_credited_map(self):
        """Return ``{invoice_line_id: qty}`` already covered by credit notes.

        Covers credit notes this module raised, which keep an exact line link,
        and credit notes raised elsewhere, which are allocated onto the
        invoice's own lines of the same product so that a foreign credit still
        shrinks what may be collected again.
        """
        self.ensure_one()
        invoice = self.invoice_id
        result = {}

        # 1. Credit notes raised through this module, via the store receipts.
        #    The search is a ledger lookup, not a view of somebody else's
        #    paperwork: it only ever returns the figures shown on this
        #    pickup's own lines.
        for invoice_line_id, by_pickup in self._get_posted_credit_by_pickup().items():
            result[invoice_line_id] = sum(by_pickup.values())

        # 2. Credit notes raised elsewhere: allocate per product, line by line.
        foreign = invoice.reversal_move_ids.filtered(
            lambda move: move.move_type == 'out_refund'
            and move.state == 'posted'
            and not move.goods_pickup_id
        )
        if foreign:
            by_product = {}
            for move in foreign:
                for line in move.line_ids:
                    if line.display_type == 'product' and line.product_id:
                        by_product.setdefault(line.product_id, []).append(line)
            for invoice_line in invoice.invoice_line_ids:
                if invoice_line.display_type != 'product' or not invoice_line.product_id:
                    continue
                pending = by_product.get(invoice_line.product_id)
                if not pending:
                    continue
                credited = result.get(invoice_line.id, 0.0)
                target = invoice_line.quantity or 0.0
                for foreign_line in pending:
                    if not _qty_compare(self.env, credited, target) < 0:
                        break
                    available = self._convert_qty(
                        foreign_line.quantity, foreign_line.product_uom_id,
                        invoice_line.product_uom_id)
                    credited += min(available, max(target - credited, 0.0))
                result[invoice_line.id] = credited
        return result

    def _get_invoice_line_reserved_map(self):
        """Return ``{invoice_line_id: qty}`` held by other open pickups.

        A pickup reserves its collected quantity for as long as that quantity
        can still lead to something: while it is on its way to the store, while
        the store is deciding on it, and while an accepted part is waiting for
        its credit note. Once a posted credit note covers the quantity it has
        stopped reserving, because ``_get_invoice_line_credited_map`` is
        already counting it as credited and the two must not be subtracted
        twice.

        This is what stops a second representative from claiming invoiced
        quantity that is physically in the building, waiting for an accountant,
        or sitting in quarantine — none of which has been credited yet.
        """
        self.ensure_one()
        reserved = {}
        lines = self.env['customer.goods.pickup.line'].sudo().search([
            ('invoice_line_id', 'in', self.invoice_id.invoice_line_ids.ids),
            ('pickup_id.state', 'not in', ('draft', 'cancel')),
            ('pickup_id', '!=', self.id),
            ('pickup_id.company_id', '=', self.company_id.id),
        ])
        if not lines:
            return reserved
        credits = self._get_posted_credit_by_pickup(lines.pickup_id)
        for line in lines:
            invoice_line = line.invoice_line_id
            qty = self._convert_qty(line.qty_collected, line.product_uom_id,
                                    invoice_line.product_uom_id)
            settled = credits.get(invoice_line.id, {}).get(line.pickup_id.id, 0.0)
            held = max(qty - settled, 0.0)
            reserved[invoice_line.id] = reserved.get(invoice_line.id, 0.0) + held
        return reserved

    @api.model
    def _convert_qty(self, qty, from_uom, to_uom):
        """Convert a quantity between two units, tolerating a missing unit."""
        if not from_uom or not to_uom or from_uom == to_uom:
            return qty or 0.0
        return from_uom._compute_quantity(
            qty or 0.0, to_uom, round=False, raise_if_failure=False)