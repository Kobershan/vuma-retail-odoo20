"""Physical custody of refused customer goods.

One record here is one quantity of one product that the store physically took
in and refused. It is the *custody* record; the proof that the goods exist is
the ``stock.move`` it points at and the quant that move created. Nothing here
writes a quant, and nothing here can turn refused goods into sellable stock: the
only way a quarantined quantity leaves the returns area is one of the
disposition actions, and each of those creates and validates a real transfer.

Quantities, and why they are not interchangeable
------------------------------------------------
``qty``
    what the store physically received and refused. Immutable.
``qty_disposed``
    how much of it a decision has already taken. A partial decision *splits*
    the record rather than editing it, so the original refusal is never
    rewritten and the history of why the goods were refused stays intact.
``qty_pending``
    ``qty - qty_disposed`` while the record is still held, zero once it is
    closed. This is the figure a physical count of the returns area should
    agree with.
``qty_released``
    what a manager re-inspected and put back into sellable stock. Only this
    becomes credit-eligible, and only because the release action demands an
    authorised approver and a recorded reason.
"""

import logging
from collections import defaultdict

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..access import MANAGER, require_group
from ..units import convert, greater as _qty_gt, is_zero as _qty_is_zero

_logger = logging.getLogger(__name__)

OPEN_STATES = ('quarantine', 'awaiting_customer')
CLOSED_STATES = ('released', 'returned', 'scrapped', 'written_off')

STATES = [
    ('quarantine', 'In Quarantine'),
    ('awaiting_customer', 'Awaiting Customer'),
    ('released', 'Re-inspected And Accepted'),
    ('returned', 'Returned To Customer'),
    ('scrapped', 'Scrapped / Disposed'),
    ('written_off', 'Written Off'),
]


class CustomerGoodsQuarantine(models.Model):
    """A refused quantity, and what happened to it afterwards."""

    _name = 'customer.goods.quarantine'
    _inherit = ['mail.thread', 'mail.activity.mixin']
    _description = 'Customer Goods Quarantine'
    _order = 'quarantine_date desc, id desc'
    _rec_names_search = ['name', 'receipt_id', 'receipt_id.invoice_number']
    _check_company_auto = True

    name = fields.Char(
        string='Reference', required=True, copy=False,
        default=lambda self: _('New'), index=True, tracking=True,
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )

    # -------------------------------------------------------------- lineage
    receipt_id = fields.Many2one(
        'customer.goods.receipt', string='Store Receipt', required=True,
        ondelete='cascade', index=True, tracking=True,
    )
    receipt_line_id = fields.Many2one(
        'customer.goods.receipt.line', string='Store Receipt Line', required=True,
        ondelete='cascade', index=True,
    )
    pickup_id = fields.Many2one(
        'customer.goods.pickup', related='receipt_id.pickup_id', store=True,
        index=True, readonly=True, string='Pickup',
    )
    partner_id = fields.Many2one(
        'res.partner', related='receipt_id.partner_id', store=True, index=True,
        readonly=True, string='Customer',
    )
    invoice_id = fields.Many2one(
        'account.move', related='receipt_id.invoice_id', store=True, index=True,
        readonly=True, string='Original Invoice',
    )
    invoice_number = fields.Char(
        related='receipt_id.invoice_number', store=True, readonly=True,
    )

    # ---------------------------------------------------------------- goods
    product_id = fields.Many2one(
        'product.product', related='receipt_line_id.product_id', store=True,
        index=True, readonly=True,
    )
    description = fields.Text(
        related='receipt_line_id.name', string='Description', readonly=True,
    )
    product_uom_id = fields.Many2one(
        'uom.uom', related='receipt_line_id.product_uom_id', store=True,
        readonly=True, string='Unit',
    )
    lot_id = fields.Many2one(
        'stock.lot', string='Lot / Serial', check_company=True, copy=False,
        domain="[('product_id', '=', product_id)]",
        help='Filled in from the receipt for tracked goods, so the refused lot '
             'or serial number stays traceable.',
    )
    rejection_reason = fields.Char(
        related='receipt_line_id.rejection_reason', store=True, readonly=True,
        string='Why It Was Refused',
    )
    price_unit = fields.Float(
        related='receipt_line_id.price_unit', digits='Product Price', readonly=True,
    )

    # ------------------------------------------------------------ quantities
    qty = fields.Float(
        string='Quantity Refused', digits='Product Unit', required=True,
        default=0.0,
        help='How much of this line the store physically refused. The quarantine '
             'stock move below moves exactly this quantity.',
    )
    qty_disposed = fields.Float(
        string='Decided', digits='Product Unit', default=0.0, copy=False,
        help='How much of the refused quantity a decision has already taken. A '
             'partial decision splits the record rather than editing it.',
    )
    qty_pending = fields.Float(
        string='Still Held', compute='_compute_quantities', store=True,
        digits='Product Unit',
        help='The refused quantity no decision has taken yet. A physical count '
             'of the returns area should agree with the sum of this column.',
    )
    qty_released = fields.Float(
        string='Re-inspected And Accepted', compute='_compute_quantities',
        store=True, digits='Product Unit',
        help='Refused goods a manager later inspected again and put back into '
             'sellable stock. Only this quantity can be credited.',
    )
    value = fields.Monetary(
        string='Value Still Held', compute='_compute_value',
        currency_field='currency_id',
    )

    # -------------------------------------------------------------- custody
    location_id = fields.Many2one(
        'stock.location', string='Physically At', required=True, copy=False,
        check_company=True, tracking=True, readonly=True,
        help='Where the goods are right now. Always inside the customer returns '
             'area while the store holds them, and a handover or disposal '
             'destination once it does not.',
    )
    state = fields.Selection(
        STATES, string='Custody Status', default='quarantine', required=True,
        copy=False, tracking=True, index=True,
    )
    quarantine_date = fields.Datetime(
        string='Quarantined On', default=fields.Datetime.now, readonly=True,
        copy=False, tracking=True,
    )
    quarantine_by_id = fields.Many2one(
        'res.users', string='Quarantined By', readonly=True, copy=False,
        default=lambda self: self.env.user,
    )

    # ----------------------------------------------------------- movements
    picking_id = fields.Many2one(
        'stock.picking', string='Quarantine Receipt', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The transfer that physically brought these goods into quarantine. '
             'This is the evidence that they are in the building.',
    )
    stock_move_id = fields.Many2one(
        'stock.move', string='Quarantine Stock Move', readonly=True, copy=False,
        ondelete='set null', index=True,
        help='The move that received the refused quantity into quarantine.',
    )
    last_picking_id = fields.Many2one(
        'stock.picking', string='Last Transfer', readonly=True, copy=False,
        ondelete='set null',
        help='The most recent transfer that moved these goods. Kept after they '
             'have left, so the custody chain stays readable.',
    )
    last_move_id = fields.Many2one(
        'stock.move', string='Last Stock Move', readonly=True, copy=False,
        ondelete='set null',
    )
    split_line_ids = fields.One2many(
        'customer.goods.quarantine', 'parent_quarantine_id',
        string='Split Off By A Decision',
    )

    # ----------------------------------------------------------- decisions
    disposition = fields.Selection(
        selection=[
            ('none', 'No Decision Yet'),
            ('awaiting_customer', 'Waiting For The Customer'),
            ('return_to_customer', 'Returned To Customer'),
            ('reinspect', 'Re-inspection And Acceptance'),
            ('scrap', 'Scrapped / Disposed'),
            ('other', 'Other Authorised Outcome'),
        ],
        string='Decision', default='none', required=True, tracking=True,
    )
    disposition_reason = fields.Text(string='Decision Reason', tracking=True)
    disposition_by_id = fields.Many2one(
        'res.users', string='Decided By', readonly=True, copy=False, tracking=True,
    )
    disposition_date = fields.Datetime(
        string='Decided On', readonly=True, copy=False, tracking=True,
    )
    handover_to = fields.Char(string='Handed To', copy=False)
    handover_reference = fields.Char(
        string='Handover Reference', copy=False,
        help='Delivery note, courier tracking number or receipt number.',
    )
    handover_date = fields.Datetime(string='Handed Over On', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='Authorised By', readonly=True, copy=False,
        help='The manager who authorised a re-inspection acceptance or a '
             'disposal. Recorded by the action, never typed in.',
    )
    approved_on = fields.Datetime(
        string='Authorised On', readonly=True, copy=False,
    )
    parent_quarantine_id = fields.Many2one(
        'customer.goods.quarantine', string='Split From', index=True, copy=False,
        ondelete='cascade',
    )

    # -------------------------------------------------------------- the guards
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                company = self.env['res.company'].browse(
                    vals.get('company_id') or self.env.company.id)
                vals['name'] = self.with_company(company).env['ir.sequence'] \
                    .next_by_code('customer.goods.quarantine') or _('New')
        return super().create(vals_list)

    def write(self, vals):
        # Both stamps record who took a decision. They are written by the
        # disposition actions through ``sudo``, and ``env.su`` is an attribute
        # of the environment rather than a context key, so a remote caller
        # cannot imitate it the way it could imitate ``with_context``.
        stamps = {'approved_by_id', 'approved_on',
                  'disposition_by_id', 'disposition_date'}
        if stamps & set(vals) and not self.env.su:
            raise UserError(_(
                'The decision and its approval are recorded by the disposition '
                'action itself and cannot be set by hand.'))
        anchors = {'receipt_id', 'receipt_line_id', 'qty', 'stock_move_id',
                   'parent_quarantine_id'}
        if anchors & set(vals) and not self.env.su:
            raise UserError(_(
                'A quarantine record is the custody trail of a physically '
                'received quantity and cannot be re-pointed or re-quantified.'))
        return super().write(vals)

    def unlink(self):
        if not self.env.su:
            raise UserError(_(
                'Quarantine records are the custody trail of goods the store '
                'physically refused and cannot be deleted.'))
        return super().unlink()

    @api.constrains('location_id', 'qty')
    def _check_quarantine_location(self):
        """Keep a returns-area location valid; leave the state machine to the rest.

        Only a location *inside* the returns structure is checked here. That is
        the case where the goods are being held, and a holding location inside a
        warehouse would defeat the whole design.

        A destination outside the returns area is the outcome of a disposition
        — handed back to the customer, or released into warehouse stock after a
        manager's re-inspection — and is deliberately not checked here: by then
        the store no longer holds the goods. ``location_id`` is read-only and
        the ``write`` guard refuses to move it for anybody but the module, so a
        user cannot pick a destination by hand.
        """
        for record in self:
            if not record.location_id:
                raise ValidationError(
                    _('Say where the refused goods physically are.'))
            if record.qty < 0:
                raise ValidationError(_('A refused quantity cannot be negative.'))
            if record.location_id._is_goods_return_location():
                record.location_id._check_goods_return_location_suitability()

    # -------------------------------------------------------------- computes
    @api.depends('state', 'qty', 'qty_disposed',
                 'split_line_ids.state', 'split_line_ids.qty')
    def _compute_quantities(self):
        children = self.mapped('split_line_ids')
        split_totals, released_totals = defaultdict(float), defaultdict(float)
        if children:
            domain = [('id', 'in', children.ids)]
            for parent, qty in children._read_group(
                    domain, ['parent_quarantine_id'], ['qty:sum']):
                split_totals[parent.id] += qty
            for parent, qty in children._read_group(
                    domain + [('state', '=', 'released')],
                    ['parent_quarantine_id'], ['qty:sum']):
                released_totals[parent.id] += qty
        for record in self:
            if record.state in CLOSED_STATES:
                record.qty_pending = 0.0
                record.qty_released = record.qty if record.state == 'released' else 0.0
            else:
                record.qty_pending = max(record.qty - record.qty_disposed, 0.0)
                record.qty_released = released_totals[record.id]
        return True

    @api.depends('price_unit', 'qty_pending')
    def _compute_value(self):
        for record in self:
            record.value = record.price_unit * record.qty_pending
        return True

    # ------------------------------------------------------------- internals
    def _state_label(self):
        self.ensure_one()
        selection = dict(self._fields['state']._description_selection(self.env))
        return selection.get(self.state) or self.state

    def _check_can_dispose(self, action):
        self.ensure_one()
        if _qty_is_zero(self.env, self.qty_pending):
            raise UserError(_(
                'Nothing of %(name)s is still held in the returns area.',
                name=self.name))
        if self.state not in OPEN_STATES:
            raise UserError(_(
                '%(name)s is already %(state)s; there is nothing left to '
                '%(action)s.', name=self.name, state=self._state_label(),
                action=action))
        return True

    def _check_disposition_quantity(self, quantity):
        self.ensure_one()
        if _qty_is_zero(self.env, quantity):
            raise UserError(
                _('Enter how many of %(name)s to act on.', name=self.name))
        if _qty_gt(self.env, quantity, self.qty_pending):
            raise UserError(_(
                'Only %(pending)s %(uom)s of %(name)s are still held.',
                pending=self.product_uom_id.round(self.qty_pending),
                uom=self.product_uom_id.name, name=self.name))
        return True

    def _split_for(self, quantity):
        """Take ``quantity`` out of this record, splitting it when it is partial.

        A partial decision must not rewrite the refusal that created the
        record, so the decided quantity becomes its own record carrying the
        terminal state, and the original simply stops covering it.
        """
        self.ensure_one()
        if _qty_is_zero(self.env, self.qty_pending - quantity):
            # The decided quantity covers everything still held. If the
            # record already gave pieces away through earlier splits, its
            # slice must shrink to that remainder, so the terminal record
            # only reports what it actually takes with it.
            if not _qty_is_zero(self.env, self.qty - quantity):
                self.sudo().write({'qty': quantity})
            return self
        split = self.sudo().create({
            'receipt_id': self.receipt_id.id,
            'receipt_line_id': self.receipt_line_id.id,
            'company_id': self.company_id.id,
            'lot_id': self.lot_id.id,
            'qty': quantity,
            'location_id': self.location_id.id,
            'state': self.state,
            'parent_quarantine_id': self.id,
            'picking_id': self.picking_id.id,
            'stock_move_id': self.stock_move_id.id,
        })
        self.sudo().write({'qty_disposed': self.qty_disposed + quantity})
        return split

    def _stamp(self, values):
        self.ensure_one()
        self.sudo().write(dict(values, disposition_date=fields.Datetime.now(),
                               disposition_by_id=self.env.user.id))
        return True

    def _internal_transfer_type(self):
        """The transfer type used to move refused goods between real locations.

        The warehouse's own *Internal Transfers* type is used when the shop has
        one, so the movement lands on the same transfer type an administrator
        would have chosen. The internal types are inactive by default in a
        standard install, so the search ignores ``active`` and falls back to the
        warehouse's configured type; without one, the first internal type of the
        company is taken. Whichever is used, the destination is an internal
        location, so the move never leaves or enters the company's books as a
        sale or a purchase.
        """
        company = self.company_id
        warehouse = self.receipt_id.warehouse_id
        picking_type = warehouse.int_type_id if warehouse else self.env['stock.picking.type']
        if not picking_type:
            picking_type = self.env['stock.picking.type'].with_context(
                active_test=False).sudo().search([
                    ('code', '=', 'internal'),
                    ('company_id', 'in', (False, company.id)),
                ], limit=1)
        if not picking_type:
            raise UserError(_(
                'No internal transfer type is configured for company %s, so '
                'quarantined goods cannot be moved.', company.display_name))
        return picking_type

    def _scrap_location(self):
        location = self.company_id.scrap_location_id
        if not location:
            raise UserError(_(
                'No inventory (scrap) location is configured for company %s, so '
                'disposal cannot be booked.', self.company_id.display_name))
        return location

    def _create_transfer(self, dest_location, quantity, reason, scrap=False):
        """Move ``quantity`` out of custody through a real validated transfer.

        Every disposition goes through here, so there is exactly one code path
        that moves refused goods, and it always leaves a validated
        ``stock.picking`` behind in the inventory history.
        """
        self.ensure_one()
        product = self.product_id
        move_uom = product.uom_id
        qty = convert(quantity, self.product_uom_id, move_uom)
        move_vals = {
            'description_picking_manual': reason,
            'product_id': product.id,
            'product_uom_qty': qty,
            'uom_id': move_uom.id,
            'location_id': self.location_id.id,
            'location_dest_id': dest_location.id,
            'price_unit': convert(self.price_unit, self.product_uom_id, product.uom_id),
        }
        if product.tracking and self.lot_id:
            move_vals['move_line_ids'] = [(0, 0, {
                'product_id': product.id,
                'uom_id': move_uom.id,
                'quantity': qty,
                'lot_id': self.lot_id.id,
                'lot_name': self.lot_id.name,
                'location_id': self.location_id.id,
                'location_dest_id': dest_location.id,
                'picked': True,
            })]
        if scrap:
            # Odoo 20 dropped the ``stock.scrap`` model: disposal is a move
            # flagged as a scrap and validated through the move itself. The
            # flag has to be set before validation, because that is when the
            # inventory-loss entry is produced.
            move_vals['is_scrap'] = True
        origin_location = self.location_id.complete_name
        picking = self.env['stock.picking'].sudo().create({
            'picking_type_id': self._internal_transfer_type().id,
            'partner_id': self.partner_id.id,
            'location_id': self.location_id.id,
            'location_dest_id': dest_location.id,
            'origin': self.name,
            'note': reason,
            'move_ids': [(0, 0, move_vals)],
        })
        moves = picking.move_ids
        moves._action_confirm(merge=False)
        moves.write({'picked': True})
        picking.with_context(cancel_backorder=True)._action_done()
        self.sudo().write({
            'last_picking_id': picking.id,
            'last_move_id': moves[:1].id,
            'location_id': dest_location.id,
        })
        self.message_post(body=_(
            '%(qty)s %(uom)s moved from %(origin)s to %(destination)s through '
            '%(picking)s. %(reason)s',
            qty=self.product_uom_id.round(quantity),
            uom=self.product_uom_id.name, origin=origin_location,
            destination=dest_location.complete_name, picking=picking.name,
            reason=reason))
        return picking

    def _customer_location(self):
        location = self.partner_id.property_stock_customer
        if not location:
            location = self.env['stock.location'].sudo().search([
                ('usage', '=', 'customer'),
                ('company_id', 'in', (False, self.company_id.id)),
            ], limit=1)
        if not location:
            raise UserError(_(
                'No customer stock location is configured for company %s.',
                self.company_id.display_name))
        return location

    # ----------------------------------------------------------- dispositions
    def action_stage_for_customer(self):
        """Move refused goods from the inspection bench to the returns cage."""
        require_group(self.env, ('customer_goods_pickup.group_goods_pickup_store_receiver',
                                 MANAGER), _(
            'Only store staff can stage refused goods for the customer.'))
        for record in self:
            record._check_can_dispose('stage')
            dest = record.env['stock.location']._get_awaiting_customer_location(
                record.company_id)
            qty = record.qty_pending
            target = record._split_for(qty)
            target._create_transfer(dest, qty, _(
                'Staged for collection by %(customer)s (quarantine %(name)s).',
                customer=record.partner_id.display_name, name=record.name))
            target._stamp({'state': 'awaiting_customer',
                           'disposition': 'awaiting_customer',
                           'disposition_reason': _(
                               'Staged for collection by the customer.')})
        return True

    def action_return_to_customer(self, handover_to=None, handover_reference=None):
        """Hand refused goods back, with the evidence of the handover."""
        require_group(self.env, ('customer_goods_pickup.group_goods_pickup_store_receiver',
                                 MANAGER), _(
            'Only store staff can hand refused goods back to a customer.'))
        for record in self:
            record._check_can_dispose('return')
            record._check_disposition_quantity(record.qty_pending)
            if not (handover_to or '').strip() \
                    and not (handover_reference or '').strip():
                raise UserError(_(
                    'Please record how and when the goods are handed over: who '
                    'takes them, and the delivery note or courier reference.'))
            note = _(
                'Handed back to %(customer)s on %(when)s%(reference)s '
                '(quarantine %(name)s).',
                customer=record.partner_id.display_name,
                when=fields.Date.to_string(fields.Date.context_today(record)),
                reference=' (%s)' % handover_reference if handover_reference else '',
                name=record.name)
            qty = record.qty_pending
            target = record._split_for(qty)
            target._create_transfer(record._customer_location(),
                                    qty, note)
            target._stamp({
                'state': 'returned',
                'disposition': 'return_to_customer',
                'disposition_reason': note,
                'handover_to': handover_to or record.partner_id.display_name,
                'handover_reference': handover_reference,
                'handover_date': fields.Datetime.now(),
            })
            record.message_post(body=_(
                'Returned to the customer. No credit note is raised: a return '
                'is not a credit.'))
        return True

    def action_reinspect_accept(self, reason=None, quantity=None):
        """Manager decision: the goods were fine after all — put them back.

        Only this path makes previously refused goods sellable, and only for a
        manager who has to say why.
        """
        require_group(self.env, MANAGER, _(
            'Only a goods pickup manager can accept quarantined goods after a '
            'second inspection.'))
        for record in self:
            record._check_can_dispose('re-inspect')
            reason = (reason or record.disposition_reason or '').strip()
            if not reason:
                raise UserError(_(
                    'Record why the goods were accepted on re-inspection of '
                    '%(name)s.', name=record.name))
            qty = record.qty_pending if quantity is None else quantity
            record._check_disposition_quantity(qty)
            target = record._split_for(qty)
            dest = record.receipt_id.dest_location_id
            target._create_transfer(dest, qty, _(
                'Re-inspected and accepted into %(location)s: %(reason)s',
                location=dest.display_name, reason=reason))
            target._stamp({
                'state': 'released',
                'disposition': 'reinspect',
                'disposition_reason': reason,
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })
            record.receipt_id.message_post(body=_(
                'Re-inspection: %(qty)s %(uom)s of %(product)s released back '
                'into %(location)s by %(user)s. %(reason)s',
                qty=record.product_uom_id.round(qty),
                uom=record.product_uom_id.name,
                product=record.product_id.display_name,
                location=dest.display_name,
                user=self.env.user.display_name, reason=reason))
        return True

    def action_scrap(self, reason=None, quantity=None):
        """Manager decision: dispose of the goods through Odoo's scrap move."""
        require_group(self.env, MANAGER, _(
            'Only a goods pickup manager can scrap quarantined goods.'))
        for record in self:
            record._check_can_dispose('scrap')
            reason = (reason or record.disposition_reason or '').strip()
            if not reason:
                raise UserError(_(
                    'Record why %(name)s is being disposed of.', name=record.name))
            qty = record.qty_pending if quantity is None else quantity
            record._check_disposition_quantity(qty)
            target = record._split_for(qty)
            target._create_transfer(
                record._scrap_location(), qty,
                _('Disposal of refused goods: %s', reason))
            target._stamp({
                'state': 'scrapped',
                'disposition': 'scrap',
                'disposition_reason': reason,
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })
        return True

    def action_record_other_disposition(self, reason=None, handover_to=None,
                                        handover_reference=None):
        """Manager decision: an authorised outcome the module does not model.

        The goods still have to go somewhere real, so this is a transfer to the
        customer location with the reason on the record — never a status change
        that leaves them sitting in a sellable location.
        """
        require_group(self.env, MANAGER, _(
            'Only a goods pickup manager can record another disposition.'))
        for record in self:
            record._check_can_dispose('close')
            reason = (reason or record.disposition_reason or '').strip()
            if not reason:
                raise UserError(_(
                    'Describe the outcome for %(name)s.', name=record.name))
            record._check_disposition_quantity(record.qty_pending)
            if not (handover_to or '').strip() \
                    and not (handover_reference or '').strip():
                raise UserError(_(
                    'Please record who takes the goods and a reference for the '
                    'handover of %(name)s.', name=record.name))
            qty = record.qty_pending
            target = record._split_for(qty)
            target._create_transfer(record._customer_location(), qty,
                                    _('Goods released by decision: %s', reason))
            target._stamp({
                'state': 'written_off',
                'disposition': 'other',
                'disposition_reason': reason,
                'handover_to': handover_to or record.partner_id.display_name,
                'handover_reference': handover_reference,
                'handover_date': fields.Datetime.now(),
                'approved_by_id': self.env.user.id,
                'approved_on': fields.Datetime.now(),
            })
        return True

    # ------------------------------------------------------------- navigation
    def action_view_picking(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Quarantine Transfer'),
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': (self.last_picking_id or self.picking_id).id,
            'target': 'current',
        }

    def action_view_receipt(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Store Receipt'),
            'res_model': 'customer.goods.receipt',
            'view_mode': 'form',
            'res_id': self.receipt_id.id,
            'target': 'current',
        }