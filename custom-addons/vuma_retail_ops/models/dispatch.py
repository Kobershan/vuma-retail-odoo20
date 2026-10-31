# -*- coding: utf-8 -*-
"""Dispatch: trips, proof of delivery, returns and delivery KPIs."""
import secrets

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .access_control import vuma_require
from .approvals import vuma_has_group


class StockPickingPOD(models.Model):
    _inherit = 'stock.picking'

    vuma_pod_state = fields.Selection(
        [('pending', 'Pending'), ('captured', 'Captured'), ('waived', 'Waived')],
        string='POD', default='pending', copy=False, tracking=True, index=True)
    vuma_pod_name = fields.Char(string='Received By (Name)')
    vuma_pod_signature = fields.Binary(string='Customer Signature', attachment=True)
    vuma_pod_signed_on = fields.Datetime(string='Signed On', readonly=True, copy=False)
    vuma_pod_signed_by_id = fields.Many2one(
        'res.users', string='Signed By', readonly=True, copy=False)
    vuma_pod_photo = fields.Image(
        string='Delivery Photo', max_width=1600, max_height=1600)
    vuma_pod_latitude = fields.Float(string='Latitude', digits=(10, 7))
    vuma_pod_longitude = fields.Float(string='Longitude', digits=(10, 7))
    vuma_pod_notes = fields.Text(string='POD Notes')
    vuma_pod_id_number = fields.Char(string='Receiver ID Number')
    vuma_pod_damage = fields.Boolean(string='Damage / Refusal')
    vuma_pod_waiver_reason = fields.Text(string='POD Waiver Reason')
    vuma_pod_token = fields.Char(
        string='POD Token', default=lambda self: secrets.token_urlsafe(24),
        copy=False, readonly=True)
    vuma_delivery_result = fields.Selection(
        [('pending', 'Pending'), ('full', 'Delivered in Full'),
         ('partial', 'Part Delivered'), ('refused', 'Refused'),
         ('failed', 'Failed')],
        string='Delivery Result', default='pending', copy=False, tracking=True)
    vuma_failure_reason = fields.Char(string='Failure Reason')
    vuma_trip_id = fields.Many2one(
        'vuma.delivery.trip', string='Delivery Trip', index=True, copy=False)
    vuma_route_sequence = fields.Integer(string='Route Sequence', default=10)
    vuma_return_picking_ids = fields.One2many(
        'stock.picking', 'vuma_origin_picking_id', string='Return Pickings')
    vuma_origin_picking_id = fields.Many2one(
        'stock.picking', string='Original Delivery', readonly=True, copy=False)
    vuma_delivery_return_id = fields.Many2one(
        'vuma.delivery.return', string='Delivery Return', readonly=True, copy=False)

    @api.depends('picking_type_code', 'state')
    def _compute_vuma_pod_required(self):
        for picking in self:
            picking.vuma_pod_required = (
                picking.picking_type_code == 'outgoing'
                and picking.state not in ('done', 'cancel'))

    vuma_pod_required = fields.Boolean(
        string='POD Required', compute='_compute_vuma_pod_required')

    def action_vuma_capture_pod(self, vals=None):
        """Capture POD from the web controller, app or backend.

        ``vals`` uses friendly keys so the controller does not have to know
        Odoo field names: name, signature, photo, latitude, longitude, notes,
        result.
        """
        vals = vals or {}
        for picking in self:
            name = (vals.get('name') or '').strip()
            signature = vals.get('signature')
            result = vals.get('result') or 'full'
            photo = vals.get('photo') or picking.vuma_pod_photo
            damage = bool(vals.get('damage')) or result in ('refused', 'failed')
            if not name:
                raise UserError(_('The name of the person receiving the goods is required.'))
            if not signature:
                raise UserError(_('The customer signature is required.'))
            if damage and not photo:
                raise UserError(_(
                    'A photo is required for a damaged or refused delivery.'))
            picking.write({
                'vuma_pod_name': name,
                'vuma_pod_signature': signature,
                'vuma_pod_id_number': vals.get('id_number') or False,
                'vuma_pod_signed_on': fields.Datetime.now(),
                'vuma_pod_signed_by_id': self.env.user.id,
                'vuma_pod_photo': photo or False,
                'vuma_pod_latitude': vals.get('latitude') or picking.vuma_pod_latitude,
                'vuma_pod_longitude': vals.get('longitude') or picking.vuma_pod_longitude,
                'vuma_pod_notes': vals.get('notes') or picking.vuma_pod_notes,
                'vuma_pod_damage': damage,
                'vuma_pod_state': 'captured',
                'vuma_delivery_result': result,
            })
            if result in ('failed', 'refused'):
                failure = self.env['vuma.delivery.failure'].search([
                    ('picking_id', '=', picking.id),
                    ('state', '=', 'open'),
                ], limit=1)
                if not failure:
                    self.env['vuma.delivery.failure'].create({
                        'picking_id': picking.id,
                        'trip_id': picking.vuma_trip_id.id or False,
                        'company_id': picking.company_id.id,
                        'reason': 'refused' if result == 'refused' else 'other',
                        'notes': vals.get('notes') or _('Recorded during POD'),
                        'photo': photo or False,
                    })
            self.env['vuma.audit.log'].log(picking, 'post', note='POD captured')
        return True

    def action_vuma_print_pod(self):
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_pod').report_action(self)

    def action_vuma_waive_pod(self):
        for picking in self:
            if not vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_ops_manager',
                    'vuma_retail_ops.group_vuma_finance_manager',
                    'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
                raise AccessError(_('Only a manager may waive proof of delivery.'))
            if not (picking.vuma_pod_waiver_reason or '').strip():
                raise UserError(_('Give the reason for waiving the POD.'))
            picking.write({
                'vuma_pod_state': 'waived',
                'vuma_pod_signed_on': fields.Datetime.now(),
                'vuma_pod_signed_by_id': self.env.user.id,
                'vuma_delivery_result': picking.vuma_delivery_result
                                   if picking.vuma_delivery_result != 'pending'
                                   else 'full',
            })
            self.env['vuma.audit.log'].log(
                picking, 'override', note=picking.vuma_pod_waiver_reason)
        return True

    def action_vuma_pod_link(self):
        self.ensure_one()
        base_url = self.env['ir.config_parameter'].sudo().get_param('web.base.url')
        return {
            'type': 'ir.actions.act_url',
            'target': 'new',
            'url': '%s/vuma/pod/%s/%s' % (base_url, self.id, self.vuma_pod_token),
        }

    def button_validate(self):
        require_pod = self.env['ir.config_parameter'].sudo().get_param(
            'vuma_retail_ops.require_pod', '1')
        if require_pod == '1' and not self.env.context.get('vuma_skip_pod'):
            for picking in self:
                if picking.picking_type_code == 'outgoing' \
                        and picking.state not in ('done', 'cancel') \
                        and picking.vuma_pod_state not in ('captured', 'waived'):
                    raise UserError(_(
                        'Delivery %(name)s cannot be validated without proof of '
                        'delivery. Capture the customer signature or get a '
                        'manager waiver.', name=picking.name))
        return super().button_validate()

    @api.model
    def _cron_purge_expired_media(self):
        """Apply the POPIA photo-retention rule without deleting POD records.

        POD signatures and documents stay for seven years; only optional photos
        are cleared after the configured retention period (12 months by default).
        """
        months = int(self.env['ir.config_parameter'].sudo().get_param(
            'vuma.retention.photo_months', '12'))
        cutoff = fields.Datetime.subtract(fields.Datetime.now(), months=months)
        pickings = self.search([
            ('vuma_pod_photo', '!=', False),
            ('vuma_pod_signed_on', '!=', False),
            ('vuma_pod_signed_on', '<', cutoff),
        ])
        if pickings:
            pickings.write({'vuma_pod_photo': False})
        returns = self.env['vuma.delivery.return'].search([
            ('photo', '!=', False), ('create_date', '<', cutoff),
        ])
        if returns:
            returns.write({'photo': False})
        if pickings or returns:
            self.env['ir.logging'].sudo().create({
                'name': 'vuma.retention',
                'type': 'server',
                'level': 'info',
                'message': 'POPIA photo retention: cleared %s POD photos and %s return photos.'
                           % (len(pickings), len(returns)),
                'path': 'vuma_retail_ops.dispatch',
                'func': '_cron_purge_expired_media',
                'line': '0',
            })
        return len(pickings) + len(returns)

    def action_vuma_email_pod(self):
        self.ensure_one()
        if not self.partner_id.email:
            raise UserError(_('The customer has no email address.'))
        template = self.env.ref('vuma_retail_ops.mail_template_pod')
        mail_id = template.send_mail(self.id, force_send=False)
        mail = self.env['mail.mail'].sudo().browse(mail_id)
        mail.write({
            'email_to': self.partner_id.email,
            'email_from': self.company_id.email or self.env.user.email or False,
        })
        self.message_post(body=_('POD emailed to %s.', self.partner_id.email))
        return True


class AccountMovePOD(models.Model):
    _inherit = 'account.move'

    vuma_delivery_return_id = fields.Many2one(
        'vuma.delivery.return', string='Delivery Return', readonly=True,
        copy=False)
    vuma_pod_picking_ids = fields.Many2many(
        'stock.picking', string='Proofs of Delivery',
        compute='_compute_vuma_pod_pickings')
    vuma_pod_count = fields.Integer(
        string='POD Count', compute='_compute_vuma_pod_pickings')

    @api.depends('invoice_line_ids.sale_line_ids.order_id')
    def _compute_vuma_pod_pickings(self):
        for move in self:
            orders = move.invoice_line_ids.sale_line_ids.order_id
            pickings = self.env['stock.picking'].search([
                ('sale_id', 'in', orders.ids),
                ('picking_type_id.code', '=', 'outgoing'),
            ]) if orders else self.env['stock.picking']
            move.vuma_pod_picking_ids = pickings
            move.vuma_pod_count = len(pickings)

    def action_vuma_view_pods(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Proofs of Delivery'),
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.vuma_pod_picking_ids.ids)],
        }


class VumaDeliveryTrip(models.Model):
    _name = 'vuma.delivery.trip'
    _description = 'Delivery Trip'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'driver_id', 'vehicle')

    name = fields.Char(
        string='Trip', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    date = fields.Date(
        string='Trip Date', default=fields.Date.context_today, required=True,
        index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True, index=True)
    driver_id = fields.Many2one(
        'res.users', string='Driver', required=True, index=True)
    vehicle = fields.Char(string='Vehicle')
    town = fields.Char(string='Town / Route', index=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('loading', 'Loading'),
         ('dispatched', 'Dispatched'), ('returned', 'Returned'),
         ('closed', 'Closed'), ('cancelled', 'Cancelled')],
        string='Status', default='draft', required=True, tracking=True, index=True)
    departure_datetime = fields.Datetime(string='Departed', copy=False)
    return_datetime = fields.Datetime(string='Returned', copy=False)
    picking_ids = fields.Many2many(
        'stock.picking', 'vuma_trip_picking_rel', 'trip_id', 'picking_id',
        string='Deliveries', copy=False)
    picking_count = fields.Integer(compute='_compute_picking_counts')
    pod_pending_count = fields.Integer(compute='_compute_picking_counts')
    expected_cash = fields.Monetary(
        string='Expected Cash', currency_field='currency_id')
    cash_collected = fields.Monetary(
        string='Cash Collected', currency_field='currency_id', copy=False,
        compute='_compute_variance', store=True, readonly=False)
    cash_returned = fields.Monetary(
        string='Cash Returned', currency_field='currency_id', copy=False)
    total_expenses = fields.Monetary(
        string='Expenses', compute='_compute_total_expenses', store=True,
        currency_field='currency_id')
    variance = fields.Monetary(
        string='Variance', compute='_compute_variance', store=True,
        currency_field='currency_id')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    float_amount = fields.Monetary(
        string='Float Issued', currency_field='currency_id')
    float_id = fields.Many2one(
        'vuma.trip.float', string='Trip Float', readonly=True, copy=False)
    remittance_id = fields.Many2one(
        'cognify.remittance', string='Remittance', readonly=True, copy=False)
    expense_ids = fields.One2many(
        'vuma.trip.expense', 'trip_id', string='Expenses', copy=True)
    return_ids = fields.One2many(
        'vuma.delivery.return', 'trip_id', string='Returns', copy=True)
    failure_ids = fields.One2many(
        'vuma.delivery.failure', 'trip_id', string='Failed Deliveries', copy=True)
    max_stops = fields.Integer(
        string='Maximum Stops', default=0,
        help='Leave zero for no stop limit. Otherwise dispatch is blocked above this number.')
    max_load_weight = fields.Float(
        string='Maximum Load (kg)', default=0.0,
        help='Leave zero for no weight limit.')
    planned_weight = fields.Float(
        string='Planned Load (kg)', compute='_compute_planned_weight', store=True)
    notes = fields.Text(string='Notes')

    @api.depends('picking_ids', 'picking_ids.move_ids',
                 'picking_ids.move_ids.product_uom_qty',
                 'picking_ids.move_ids.product_id.weight')
    def _compute_planned_weight(self):
        for trip in self:
            trip.planned_weight = sum(
                (move.product_uom_qty or 0.0)
                * (move.product_id.weight or 0.0)
                for move in trip.picking_ids.move_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.delivery.trip') or _('New')
        return super().create(vals_list)

    @api.depends('picking_ids', 'picking_ids.vuma_pod_state')
    def _compute_picking_counts(self):
        for trip in self:
            trip.picking_count = len(trip.picking_ids)
            trip.pod_pending_count = len(trip.picking_ids.filtered(
                lambda p: p.state not in ('done', 'cancel')
                and p.vuma_pod_state == 'pending'))

    @api.depends('expense_ids.amount', 'expense_ids.state')
    def _compute_total_expenses(self):
        for trip in self:
            trip.total_expenses = sum(trip.expense_ids.filtered(
                lambda e: e.state == 'verified').mapped('amount'))

    @api.depends('float_amount', 'total_expenses', 'cash_returned',
                 'remittance_id.total_received')
    def _compute_variance(self):
        for trip in self:
            collected = trip.remittance_id.total_received or 0.0
            trip.cash_collected = collected
            trip.variance = (trip.float_amount or 0.0) + collected \
                - (trip.total_expenses or 0.0) - (trip.cash_returned or 0.0)

    def action_load_deliveries(self):
        for trip in self:
            domain = [
                ('company_id', '=', trip.company_id.id),
                ('picking_type_code', '=', 'outgoing'),
                ('state', 'in', ('confirmed', 'assigned')),
                ('vuma_trip_id', '=', False),
            ]
            if trip.town:
                domain.append(('partner_id.city', 'ilike', trip.town))
            pickings = self.env['stock.picking'].search(domain, limit=50)
            trip.picking_ids = [Command.set(pickings.ids)]
        return True

    def action_dispatch(self):
        for trip in self:
            if not trip.picking_ids:
                raise UserError(_('Load the deliveries for this trip first.'))
            if trip.max_stops and len(trip.picking_ids) > trip.max_stops:
                raise UserError(_(
                    'This trip has %(count)s stops, above the vehicle limit '
                    'of %(limit)s.',
                    count=len(trip.picking_ids), limit=trip.max_stops))
            if trip.max_load_weight and trip.planned_weight > trip.max_load_weight:
                raise UserError(_(
                    'The planned load is %(load).2f kg, above the vehicle '
                    'limit of %(limit).2f kg.',
                    load=trip.planned_weight, limit=trip.max_load_weight))
            for sequence, picking in enumerate(trip.picking_ids, start=1):
                picking.write({
                    'vuma_trip_id': trip.id,
                    'vuma_route_sequence': sequence,
                })
            trip.write({
                'state': 'dispatched',
                'departure_datetime': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(trip, 'post', note='Trip dispatched')
        return True

    def action_return(self):
        self.write({'state': 'returned', 'return_datetime': fields.Datetime.now()})
        return True

    def action_create_float(self):
        for trip in self:
            if trip.float_id:
                raise UserError(_('A float already exists for this trip.'))
            if trip.float_amount <= 0:
                raise UserError(_('Enter the float amount first.'))
            trip.float_id = self.env['vuma.trip.float'].create({
                'employee_id': trip.driver_id.id,
                'trip_id': trip.id,
                'date': trip.date,
                'company_id': trip.company_id.id,
                'amount_issued': trip.float_amount,
                'notes': _('Float for trip %s', trip.name),
            })
            trip.float_id.action_issue()
        return True

    def action_create_remittance(self):
        for trip in self:
            if trip.remittance_id:
                raise UserError(_('A remittance already exists for this trip.'))
            trip.remittance_id = self.env['cognify.remittance'].create({
                'rep_id': trip.driver_id.id,
                'date': trip.date,
                'company_id': trip.company_id.id,
                'trip_id': trip.id,
                'notes': _('Collections for trip %s', trip.name),
            })
        return True

    def action_close(self):
        for trip in self:
            pending = trip.picking_ids.filtered(
                lambda p: p.state not in ('done', 'cancel')
                and p.vuma_pod_state == 'pending')
            if pending:
                raise UserError(_(
                    'Capture POD or waive it for: %s',
                    ', '.join(pending.mapped('name'))))
            unverified = trip.expense_ids.filtered(
                lambda e: e.state in ('draft', 'submitted'))
            if unverified:
                raise UserError(_(
                    'Verify or reject the trip expenses before closing: %s',
                    ', '.join(unverified.mapped('description'))))
            if trip.remittance_id and trip.remittance_id.state \
                    not in ('verified', 'credited'):
                raise UserError(_(
                    'The remittance must be verified by the cash office before '
                    'the trip can close.'))
            variance = trip.variance
            if abs(variance) > 0.01 and not trip.env.context.get('vuma_allow_variance'):
                self.env['vuma.cash.exception'].create({
                    'trip_id': trip.id,
                    'exception_type': 'shortage' if variance < 0 else 'overage',
                    'amount': abs(variance),
                    'description': _('Trip float variance on %s', trip.name),
                    'company_id': trip.company_id.id,
                })
            trip.state = 'closed'
            if trip.float_id:
                trip.float_id.action_close()
            self.env['vuma.audit.log'].log(
                trip, 'post', note='Trip closed, variance %.2f' % variance)
        return True

    def action_view_pickings(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'list,form',
            'domain': [('id', 'in', self.picking_ids.ids)],
        }


class VumaTripExpense(models.Model):
    _name = 'vuma.trip.expense'
    _description = 'Trip Expense'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date desc, id desc'
    _vuma_audit_fields = ('state', 'amount')

    trip_id = fields.Many2one(
        'vuma.delivery.trip', string='Trip', required=True, index=True,
        ondelete='cascade')
    date = fields.Date(
        string='Date', default=fields.Date.context_today, required=True)
    category = fields.Selection(
        [('fuel', 'Fuel'), ('toll', 'Toll'), ('parking', 'Parking'),
         ('food', 'Food / Allowance'), ('accommodation', 'Accommodation'),
         ('maintenance', 'Vehicle Maintenance'), ('loading', 'Loading Fee'),
         ('other', 'Other')],
        string='Category', required=True, default='fuel')
    description = fields.Char(string='Description', required=True)
    amount = fields.Monetary(
        string='Amount', currency_field='currency_id', required=True)
    currency_id = fields.Many2one(
        related='trip_id.currency_id', store=True, readonly=True)
    receipt = fields.Image(string='Receipt / Photo', max_width=2000, max_height=2000)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'),
         ('verified', 'Verified'), ('rejected', 'Rejected')],
        string='Status', default='draft', required=True, tracking=True)
    verified_by_id = fields.Many2one(
        'res.users', string='Verified By', readonly=True, copy=False)
    verified_date = fields.Datetime(
        string='Verified On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason')
    notes = fields.Text(string='Notes')

    def action_submit(self):
        self.write({'state': 'submitted'})
        return True

    def action_verify(self):
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_cashier',
                'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Trip expenses are verified by the cash office.'))
        for expense in self:
            if expense.state != 'submitted':
                raise UserError(_('Only a submitted expense can be verified.'))
            if expense.trip_id.driver_id == self.env.user \
                    or expense.create_uid == self.env.user:
                raise AccessError(_(
                    'A driver cannot verify their own trip expense.'))
        self.write({
            'state': 'verified',
            'verified_by_id': self.env.user.id,
            'verified_date': fields.Datetime.now(),
        })
        self.env['vuma.audit.log'].log(self, 'approve')
        return True

    def action_reject(self):
        self.state = 'rejected'
        return True


class VumaDeliveryReturn(models.Model):
    _name = 'vuma.delivery.return'
    _description = 'Delivery Return'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'create_date desc, id desc'
    _vuma_audit_fields = ('state', 'quantity')

    name = fields.Char(
        string='Return', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    trip_id = fields.Many2one(
        'vuma.delivery.trip', string='Trip', index=True, ondelete='cascade')
    picking_id = fields.Many2one(
        'stock.picking', string='Delivery', index=True)
    invoice_id = fields.Many2one(
        'account.move', string='Invoice', index=True,
        domain="[('move_type','=','out_invoice')]")
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank', '>', 0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    product_id = fields.Many2one(
        'product.product', string='Product', required=True)
    quantity = fields.Float(
        string='Quantity', digits='Product Unit of Measure', required=True)
    uom_id = fields.Many2one(
        'uom.uom', string='Unit', required=True,
        default=lambda self: self.env.ref('uom.product_uom_unit', raise_if_not_found=False))
    price_unit = fields.Monetary(
        string='Unit Price', currency_field='currency_id')
    reason = fields.Selection(
        [('refused', 'Refused'), ('damaged', 'Damaged'),
         ('wrong', 'Wrong Product'), ('short', 'Short'), ('other', 'Other')],
        string='Reason', required=True, default='refused')
    action = fields.Selection(
        [('credit', 'Credit Only'), ('replace', 'Replace'),
         ('quarantine', 'Quarantine Stock'), ('scrap', 'Scrap')],
        string='Action', required=True, default='quarantine')
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'),
         ('approved', 'Approved'), ('credited', 'Credited'),
         ('rejected', 'Rejected')],
        string='Status', default='draft', required=True, tracking=True)
    photo = fields.Image(string='Photo', max_width=1600, max_height=1600)
    notes = fields.Text(string='Notes')
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason')
    credit_note_id = fields.Many2one(
        'account.move', string='Credit Note', readonly=True, copy=False)
    stock_return_picking_id = fields.Many2one(
        'stock.picking', string='Stock Return', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.delivery.return') or _('New')
        return super().create(vals_list)

    @api.onchange('product_id', 'invoice_id')
    def _onchange_product(self):
        if self.product_id:
            self.uom_id = self.product_id.uom_id
            self.price_unit = self.product_id.lst_price
        if self.invoice_id and self.product_id and not self.price_unit:
            line = self.invoice_id.invoice_line_ids.filtered(
                lambda l: l.product_id == self.product_id)
            if line:
                self.price_unit = line[:1].price_unit

    def action_submit(self):
        for record in self:
            if record.quantity <= 0:
                raise UserError(_('The return quantity must be greater than zero.'))
            if record.reason in ('refused', 'damaged', 'wrong') and not record.photo:
                raise UserError(_(
                    'A photo is required for a refused, damaged or wrong return.'))
        self.write({'state': 'submitted'})
        return True

    def action_approve(self):
        vuma_require(self.env, 'action.approve',
                     _('Delivery returns are approved by operations.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_ops_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Delivery returns are approved by operations.'))
        for record in self:
            if record.state != 'submitted':
                raise UserError(_('Only a submitted return can be approved.'))
            if record.reason in ('refused', 'damaged', 'wrong') and not record.photo:
                raise UserError(_(
                    'A photo is required for a refused, damaged or wrong return.'))
        self.write({
            'state': 'approved',
            'approved_by_id': self.env.user.id,
            'approved_date': fields.Datetime.now(),
        })
        self.env['vuma.audit.log'].log(self, 'approve')
        return True

    def action_create_stock_return(self):
        for record in self:
            if record.stock_return_picking_id:
                raise UserError(_('A stock return already exists.'))
            if record.state not in ('approved', 'credited'):
                raise UserError(_('Approve the return first.'))
            if not record.picking_id:
                raise UserError(_('Link the original delivery first.'))
            if record.picking_id.state != 'done':
                raise UserError(_('Only a completed delivery can be returned.'))
            return_picking = record.picking_id._create_return()
            return_picking.vuma_origin_picking_id = record.picking_id.id
            return_picking.vuma_delivery_return_id = record.id
            move = return_picking.move_ids.filtered(
                lambda m: m.product_id == record.product_id)
            if not move:
                raise UserError(_('The product is not on the original delivery.'))
            move.product_uom_qty = record.quantity
            if record.action == 'quarantine':
                quarantine = self.env['stock.location'].search([
                    ('company_id', '=', record.company_id.id),
                    ('vuma_is_quarantine', '=', True),
                ], limit=1)
                if quarantine:
                    return_picking.location_dest_id = quarantine.id
            elif record.action == 'scrap':
                scrap_location = record.company_id.scrap_location_id
                if scrap_location:
                    return_picking.location_dest_id = scrap_location.id
            record.stock_return_picking_id = return_picking.id
            return_picking.message_post(body=_(
                'Return from delivery %(delivery)s: %(qty).2f x %(product)s',
                delivery=record.picking_id.name, qty=record.quantity,
                product=record.product_id.display_name))
        return True

    def action_create_credit_note(self):
        for record in self:
            if record.credit_note_id:
                raise UserError(_('A credit note already exists.'))
            if record.state not in ('approved', 'credited'):
                raise UserError(_('Approve the return before crediting it.'))
            tax_ids = record.product_id.taxes_id.filtered(
                lambda t: t.company_id == record.company_id).ids
            move = self.env['account.move'].with_company(record.company_id).create({
                'move_type': 'out_refund',
                'partner_id': record.partner_id.id,
                'invoice_date': fields.Date.context_today(self),
                'date': fields.Date.context_today(self),
                'company_id': record.company_id.id,
                'invoice_origin': record.name,
                'reversed_entry_id': record.invoice_id.id or False,
                'vuma_delivery_return_id': record.id,
                'invoice_line_ids': [Command.create({
                    'product_id': record.product_id.id,
                    'name': _('Return %(name)s: %(reason)s', name=record.name,
                              reason=dict(record._fields['reason'].selection)[record.reason]),
                    'quantity': record.quantity,
                    'price_unit': record.price_unit or record.product_id.lst_price,
                    'tax_ids': [Command.set(tax_ids)],
                })],
                'vuma_credit_approval_state': 'pending',
            })
            if abs(move.amount_total or 0.0) <= 5000:
                move.write({
                    'vuma_credit_approval_state': 'approved',
                    'vuma_credit_approved_by_id': self.env.user.id,
                    'vuma_credit_approved_date': fields.Datetime.now(),
                })
            record.write({'credit_note_id': move.id, 'state': 'credited'})
            self.env['vuma.audit.log'].log(record, 'post', note='Credit note %s' % move.name)
        return True

    def action_view_credit_note(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.credit_note_id.id,
        }

    def action_view_stock_return(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'stock.picking',
            'view_mode': 'form',
            'res_id': self.stock_return_picking_id.id,
        }


class VumaDeliveryFailure(models.Model):
    _name = 'vuma.delivery.failure'
    _description = 'Failed Delivery'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'create_date desc, id desc'
    _vuma_audit_fields = ('state', 'reschedule_date')

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    trip_id = fields.Many2one(
        'vuma.delivery.trip', string='Trip', index=True, ondelete='cascade')
    picking_id = fields.Many2one(
        'stock.picking', string='Delivery', required=True, index=True)
    partner_id = fields.Many2one(
        related='picking_id.partner_id', string='Customer', store=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    reason = fields.Selection(
        [('closed', 'Customer Closed'), ('refused', 'Refused'),
         ('address', 'Wrong Address'), ('access', 'No Access'),
         ('payment', 'Payment Not Ready'), ('other', 'Other')],
        string='Reason', required=True, default='closed')
    notes = fields.Text(string='Notes')
    photo = fields.Image(string='Photo', max_width=1600, max_height=1600)
    reschedule_date = fields.Date(string='Reschedule For')
    state = fields.Selection(
        [('open', 'Open'), ('rescheduled', 'Rescheduled'),
         ('cancelled', 'Cancelled')],
        string='Status', default='open', required=True, tracking=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.delivery.failure') or _('New')
        return super().create(vals_list)

    def action_reschedule(self):
        for record in self:
            if not record.reschedule_date:
                raise UserError(_('Choose the reschedule date.'))
            record.state = 'rescheduled'
            record.picking_id.scheduled_date = fields.Datetime.to_datetime(
                record.reschedule_date)
            record.picking_id.message_post(body=_(
                'Delivery rescheduled for %s after a failed attempt.',
                record.reschedule_date))
        return True


class StockLocationQuarantine(models.Model):
    _inherit = 'stock.location'

    vuma_is_quarantine = fields.Boolean(string='Quarantine Location')


class VumaDeliveryKPI(models.Model):
    _name = 'vuma.delivery.kpi'
    _description = 'Delivery KPI'
    _auto = False
    _log_access = False
    _order = 'date desc, company_id'

    date = fields.Date(string='Date', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    trip_id = fields.Many2one('vuma.delivery.trip', string='Trip', readonly=True)
    delivery_count = fields.Integer(string='Deliveries', readonly=True)
    on_time_count = fields.Integer(string='On Time', readonly=True)
    late_count = fields.Integer(string='Late', readonly=True)
    pod_count = fields.Integer(string='POD Captured', readonly=True)
    failed_count = fields.Integer(string='Failed', readonly=True)
    return_count = fields.Integer(string='Returns', readonly=True)
    on_time_rate = fields.Float(string='On Time %', readonly=True)
    pod_rate = fields.Float(string='POD %', readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, 'vuma_delivery_kpi')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW vuma_delivery_kpi AS (
                SELECT
                    row_number() OVER () AS id,
                    DATE(sp.date_done) AS date,
                    sp.company_id AS company_id,
                    sp.partner_id AS partner_id,
                    sp.vuma_trip_id AS trip_id,
                    COUNT(DISTINCT sp.id) AS delivery_count,
                    COUNT(DISTINCT sp.id) FILTER (
                        WHERE sp.date_done <= sp.scheduled_date) AS on_time_count,
                    COUNT(DISTINCT sp.id) FILTER (
                        WHERE sp.scheduled_date IS NOT NULL
                          AND sp.date_done > sp.scheduled_date) AS late_count,
                    COUNT(DISTINCT sp.id) FILTER (
                        WHERE sp.vuma_pod_state IN ('captured', 'waived')) AS pod_count,
                    COUNT(DISTINCT sp.id) FILTER (
                        WHERE sp.vuma_delivery_result = 'failed') AS failed_count,
                    COUNT(DISTINCT r.id) AS return_count,
                    CASE WHEN COUNT(DISTINCT sp.id) > 0 THEN
                        COUNT(DISTINCT sp.id) FILTER (
                            WHERE sp.date_done <= sp.scheduled_date) * 100.0 / COUNT(DISTINCT sp.id)
                    ELSE 0 END AS on_time_rate,
                    CASE WHEN COUNT(DISTINCT sp.id) > 0 THEN
                        COUNT(DISTINCT sp.id) FILTER (
                            WHERE sp.vuma_pod_state IN ('captured', 'waived')) * 100.0 / COUNT(DISTINCT sp.id)
                    ELSE 0 END AS pod_rate
                FROM stock_picking sp
                LEFT JOIN vuma_delivery_return r ON r.picking_id = sp.id
                LEFT JOIN stock_picking_type spt ON spt.id = sp.picking_type_id
                WHERE spt.code = 'outgoing'
                  AND sp.state = 'done'
                  AND sp.date_done IS NOT NULL
                GROUP BY DATE(sp.date_done), sp.company_id, sp.partner_id, sp.vuma_trip_id
            )
        """)
