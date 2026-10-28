# -*- coding: utf-8 -*-
"""Idempotent field-app actions and offline synchronisation."""
import base64
import json
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

_logger = logging.getLogger(__name__)


class VumaFieldAction(models.Model):
    _name = 'vuma.field.action'
    _description = 'Field App Action'
    _order = 'create_date desc, id desc'

    client_uuid = fields.Char(
        string='Client UUID', required=True, index=True, copy=False)
    action_type = fields.Selection(
        [('pod', 'Proof of Delivery'), ('expense', 'Trip Expense'),
         ('return', 'Delivery Return'), ('collection', 'Customer Collection'),
         ('checkin', 'Trip Check-in')],
        string='Action', required=True, index=True)
    user_id = fields.Many2one(
        'res.users', string='User', default=lambda self: self.env.user,
        required=True, index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    picking_id = fields.Many2one('stock.picking', string='Delivery')
    trip_id = fields.Many2one('vuma.delivery.trip', string='Trip')
    partner_id = fields.Many2one(
        'res.partner', string='Customer',
        domain="[('customer_rank', '>', 0)]")
    payload = fields.Text(string='Payload')
    state = fields.Selection(
        [('received', 'Received'), ('processed', 'Processed'),
         ('failed', 'Failed'), ('duplicate', 'Duplicate')],
        string='Status', default='received', required=True, index=True)
    result = fields.Text(string='Result')
    error = fields.Text(string='Error')
    processed_date = fields.Datetime(string='Processed On', readonly=True)

    _sql_constraints = [
        ('client_uuid_uniq', 'unique(client_uuid)',
         'This offline action has already been received.'),
    ]

    @api.model
    def receive(self, action_type, client_uuid, payload):
        """Create or return the existing action for a client UUID."""
        existing = self.search([('client_uuid', '=', client_uuid)], limit=1)
        if existing:
            existing.state = 'duplicate'
            return existing
        payload = payload or {}
        picking_id = payload.get('picking_id') or False
        trip_id = payload.get('trip_id') or False
        partner_id = payload.get('partner_id') or False
        return self.create({
            'client_uuid': client_uuid,
            'action_type': action_type,
            'user_id': self.env.user.id,
            'company_id': self.env.company.id,
            'picking_id': picking_id,
            'trip_id': trip_id,
            'partner_id': partner_id,
            'payload': json.dumps(payload, default=str),
        })

    def process(self):
        for action in self:
            if action.state in ('processed', 'duplicate'):
                continue
            try:
                payload = json.loads(action.payload or '{}')
                method = getattr(
                    action, '_process_%s' % action.action_type, None)
                if not method:
                    raise UserError(_('Unknown field action type.'))
                result = method(payload) or {}
                action.write({
                    'state': 'processed',
                    'result': json.dumps(result, default=str),
                    'processed_date': fields.Datetime.now(),
                    'error': False,
                })
            except Exception as exc:  # keep the action for retry / support
                _logger.exception('Field action %s failed', action.client_uuid)
                action.write({
                    'state': 'failed',
                    'error': str(exc),
                    'processed_date': fields.Datetime.now(),
                })
        return True

    def _check_own_trip(self, trip):
        user = self.env.user
        if trip.driver_id != user and not (
                user.has_group('vuma_retail_ops.group_vuma_ops_manager')
                or user.has_group('vuma_retail_ops.group_vuma_owner')
                or user.has_group('base.group_system')):
            raise AccessError(_('This trip is not assigned to you.'))

    def _check_own_picking(self, picking):
        user = self.env.user
        if picking.vuma_trip_id:
            self._check_own_trip(picking.vuma_trip_id)
        elif picking.user_id and picking.user_id != user and not (
                user.has_group('vuma_retail_ops.group_vuma_ops_manager')
                or user.has_group('base.group_system')):
            raise AccessError(_('This delivery is not assigned to you.'))

    def _process_pod(self, payload):
        picking = self.env['stock.picking'].browse(
            int(payload.get('picking_id') or self.picking_id.id or 0))
        if not picking.exists():
            raise UserError(_('The delivery no longer exists.'))
        self._check_own_picking(picking)
        signature = payload.get('signature') or ''
        if signature.startswith('data:'):
            signature = signature.split(',', 1)[-1]
        photo = payload.get('photo') or ''
        if photo.startswith('data:'):
            photo = photo.split(',', 1)[-1]
        picking.action_vuma_capture_pod({
            'name': payload.get('name'),
            'signature': signature,
            'photo': photo or False,
            'id_number': payload.get('id_number') or '',
            'damage': bool(payload.get('damage')),
            'latitude': payload.get('latitude') or 0.0,
            'longitude': payload.get('longitude') or 0.0,
            'notes': payload.get('notes') or '',
            'result': payload.get('result') or 'full',
        })
        return {'picking': picking.name, 'pod_state': picking.vuma_pod_state}

    def _process_expense(self, payload):
        trip = self.env['vuma.delivery.trip'].browse(
            int(payload.get('trip_id') or self.trip_id.id or 0))
        if not trip.exists():
            raise UserError(_('The trip no longer exists.'))
        self._check_own_trip(trip)
        receipt = payload.get('receipt') or ''
        if receipt.startswith('data:'):
            receipt = receipt.split(',', 1)[-1]
        expense = self.env['vuma.trip.expense'].create({
            'trip_id': trip.id,
            'date': payload.get('date') or fields.Date.context_today(self),
            'category': payload.get('category') or 'fuel',
            'description': payload.get('description') or _('Field expense'),
            'amount': float(payload.get('amount') or 0.0),
            'receipt': receipt or False,
            'notes': payload.get('notes') or '',
            'state': 'submitted',
        })
        return {'expense': expense.id, 'trip': trip.name}

    def _process_return(self, payload):
        trip = self.env['vuma.delivery.trip'].browse(
            int(payload.get('trip_id') or self.trip_id.id or 0))
        picking = self.env['stock.picking'].browse(
            int(payload.get('picking_id') or self.picking_id.id or 0))
        if not picking.exists():
            raise UserError(_('The delivery no longer exists.'))
        self._check_own_picking(picking)
        photo = payload.get('photo') or ''
        if photo.startswith('data:'):
            photo = photo.split(',', 1)[-1]
        return_rec = self.env['vuma.delivery.return'].create({
            'trip_id': trip.id or False,
            'picking_id': picking.id,
            'invoice_id': picking.sale_id.invoice_ids[:1].id or False,
            'partner_id': picking.partner_id.id,
            'company_id': picking.company_id.id,
            'product_id': int(payload.get('product_id')),
            'quantity': float(payload.get('quantity') or 0.0),
            'uom_id': int(payload.get('uom_id')) if payload.get('uom_id') else False,
            'price_unit': float(payload.get('price_unit') or 0.0),
            'reason': payload.get('reason') or 'other',
            'action': payload.get('action') or 'quarantine',
            'photo': photo or False,
            'notes': payload.get('notes') or '',
        })
        return_rec.action_submit()
        return {'return': return_rec.name, 'state': return_rec.state}

    def _process_collection(self, payload):
        partner = self.env['res.partner'].browse(
            int(payload.get('partner_id') or self.partner_id.id or 0))
        if not partner.exists():
            raise UserError(_('The customer no longer exists.'))
        journal = self.env['account.journal'].search([
            ('type', 'in', ('cash', 'bank')),
            ('company_id', '=', self.env.company.id),
        ], limit=1)
        if not journal:
            raise UserError(_('No cash or bank journal is configured.'))
        invoice_ids = payload.get('invoice_ids') or []
        if isinstance(invoice_ids, str):
            invoice_ids = [part for part in invoice_ids.split(',') if part.strip()]
        elif not isinstance(invoice_ids, (list, tuple)):
            invoice_ids = [invoice_ids]
        invoice_ids = [int(i) for i in invoice_ids if str(i).strip().isdigit()]
        receipt = self.env['vuma.cash.receipt'].sudo().create({
            'receipt_type': 'customer',
            'partner_id': partner.id,
            'employee_id': self.env.user.id,
            'amount': float(payload.get('amount') or 0.0),
            'journal_id': journal.id,
            'invoice_ids': [(6, 0, invoice_ids)],
            'received_by_id': self.env.user.id,
            'reference': payload.get('reference') or _('Field collection'),
            'notes': payload.get('notes') or '',
        })
        return {'receipt': receipt.name, 'receipt_id': receipt.id,
                'state': receipt.state}

    def _process_checkin(self, payload):
        trip = self.env['vuma.delivery.trip'].browse(
            int(payload.get('trip_id') or self.trip_id.id or 0))
        if trip.exists():
            self._check_own_trip(trip)
            trip.message_post(body=_(
                'Driver checked in at %(lat)s, %(lon)s. %(notes)s',
                lat=payload.get('latitude'), lon=payload.get('longitude'),
                notes=payload.get('notes') or ''))
            return {'trip': trip.name}
        return {'ok': True}
