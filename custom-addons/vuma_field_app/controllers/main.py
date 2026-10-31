# -*- coding: utf-8 -*-
"""Mobile PWA endpoints for Vuma drivers and sales representatives."""
import base64
import json
import logging
import uuid
from datetime import timedelta
from urllib.parse import quote

from odoo import Command, _, fields, http
from odoo.exceptions import AccessError, UserError
from odoo.http import Response, request
from odoo.tools import file_open

_logger = logging.getLogger(__name__)

FIELD_GROUPS = (
    'vuma_retail_ops.group_vuma_driver',
    'vuma_retail_ops.group_vuma_sales_rep',
    'vuma_retail_ops.group_vuma_ops_manager',
    'vuma_retail_ops.group_vuma_owner',
    'base.group_system',
)


class VumaFieldApp(http.Controller):

    # ------------------------------------------------------------------ auth
    def _is_manager(self):
        user = request.env.user
        return (
            user.has_group('vuma_retail_ops.group_vuma_ops_manager')
            or user.has_group('vuma_retail_ops.group_vuma_owner')
            or user.has_group('base.group_system')
        )

    def _is_field_user(self):
        user = request.env.user
        return any(user.has_group(group) for group in FIELD_GROUPS)

    def _login_redirect(self):
        return request.redirect(
            '/web/login?redirect=%s' % quote(request.httprequest.path))

    def _require_field_user(self):
        if request.env.user._is_public():
            return self._login_redirect()
        if not self._is_field_user():
            raise AccessError(_('This app is only available to field staff.'))
        return None

    def _json_response(self, data, status=200):
        return Response(
            json.dumps(data, default=str),
            status=status,
            content_type='application/json',
        )

    def _field_context(self, **values):
        context = {
            'user': request.env.user,
            'company': request.env.company,
            'is_manager': self._is_manager(),
            'is_rep': request.env.user.has_group(
                'vuma_retail_ops.group_vuma_sales_rep'),
            'is_driver': request.env.user.has_group(
                'vuma_retail_ops.group_vuma_driver'),
            'today': fields.Date.context_today(request.env.user),
        }
        context.update(values)
        return context

    def _pending_pods(self, limit=30):
        domain = [
            ('picking_type_id.code', '=', 'outgoing'),
            ('state', 'in', ('confirmed', 'assigned')),
            ('vuma_pod_state', '=', 'pending'),
        ]
        if not self._is_manager():
            domain += ['|',
                       ('vuma_trip_id.driver_id', '=', request.env.user.id),
                       ('user_id', '=', request.env.user.id)]
        return request.env['stock.picking'].search(
            domain, order='scheduled_date, id', limit=limit)

    def _driver_trips(self, limit=20):
        domain = [
            ('state', 'not in', ('closed', 'cancelled')),
            ('date', '>=', fields.Date.context_today(request.env.user)
             - timedelta(days=7)),
        ]
        if not self._is_manager():
            domain.append(('driver_id', '=', request.env.user.id))
        return request.env['vuma.delivery.trip'].search(
            domain, order='date, id', limit=limit)

    def _collection_invoices(self, limit=30):
        """Invoices a field user may collect.

        Drivers do not have accounting access, so the controller performs the
        lookup as sudo but scopes it to the customers on that driver's pending
        deliveries.  The resulting recordset is safe to render in the field
        app and is also used to authorise direct invoice URLs.
        """
        pods = self._pending_pods(limit=100)
        partner_ids = pods.mapped('partner_id').ids
        domain = [
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('amount_residual', '>', 0),
        ]
        if partner_ids:
            domain.append(('partner_id', 'in', partner_ids))
        elif not self._is_manager():
            # A driver with no assigned deliveries has no collection book.
            return request.env['account.move'].sudo().browse()
        return request.env['account.move'].sudo().search(
            domain, order='invoice_date_due, id', limit=limit)

    def _require_sales_user(self):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        user = request.env.user
        if not (user.has_group('vuma_retail_ops.group_vuma_sales_rep')
                or self._is_manager()):
            raise AccessError(_('This screen is only available to sales staff.'))
        return None

    def _customer_domain(self, query=None):
        domain = [
            ('customer_rank', '>', 0),
            ('active', '=', True),
            ('company_id', 'in', [False, request.env.company.id]),
        ]
        if query:
            domain += ['|', '|', '|', '|',
                       ('name', 'ilike', query),
                       ('phone', 'ilike', query),
                       ('email', 'ilike', query),
                       ('city', 'ilike', query),
                       ('vat', 'ilike', query)]
        return domain

    def _customer_record(self, partner_id):
        partner = request.env['res.partner'].sudo().browse(
            int(partner_id)).exists()
        if not partner or partner.customer_rank <= 0:
            return None
        return partner

    def _customer_moves(self, partner):
        return request.env['account.move'].sudo().search([
            ('partner_id', '=', partner.id),
            ('move_type', 'in', ('out_invoice', 'out_refund')),
            ('state', '=', 'posted'),
        ], order='invoice_date desc, id desc')

    def _customer_payments(self, partner):
        return request.env['account.payment'].sudo().search([
            ('partner_id', '=', partner.id),
            ('state', '=', 'posted'),
        ], order='date desc, id desc', limit=50)

    def _field_template(self, template, **values):
        return request.render(template, self._field_context(**values))

    # ------------------------------------------------------------------ pages
    @http.route(['/vuma/field', '/vuma/field/'], type='http', auth='user',
                website=False)
    def field_home(self, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        user = request.env.user
        trips = self._driver_trips()
        pending = self._pending_pods()
        expenses = request.env['vuma.trip.expense'].search([
            ('trip_id.driver_id', '=', user.id),
            ('state', 'in', ('draft', 'submitted')),
        ], limit=20)
        returns = request.env['vuma.delivery.return'].search([
            ('trip_id.driver_id', '=', user.id),
            ('state', 'in', ('draft', 'submitted')),
        ], limit=20)
        invoices = self._collection_invoices()
        is_rep = user.has_group('vuma_retail_ops.group_vuma_sales_rep')
        quotes = request.env['sale.order']
        if is_rep:
            quotes = request.env['sale.order'].sudo().search([
                ('user_id', '=', user.id),
                ('state', 'in', ('draft', 'sent')),
            ], limit=15)
        return self._field_template(
            'vuma_field_app.field_home',
            trips=trips, pending_pods=pending, expenses=expenses,
            returns=returns, invoices=invoices, quotes=quotes,
            is_driver=user.has_group('vuma_retail_ops.group_vuma_driver'),
            is_rep=is_rep,
            submitted=kwargs.get('submitted'),
        )

    @http.route('/vuma/field/trips', type='http', auth='user', website=False)
    def field_trips(self, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        return self._field_template(
            'vuma_field_app.field_trips', trips=self._driver_trips(limit=50))

    @http.route('/vuma/field/trip/<int:trip_id>', type='http', auth='user',
                website=False)
    def field_trip(self, trip_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        trip = request.env['vuma.delivery.trip'].browse(trip_id).exists()
        if not trip:
            return request.not_found()
        if not self._is_manager() and trip.driver_id != request.env.user:
            raise AccessError(_('This trip is not assigned to you.'))
        return self._field_template(
            'vuma_field_app.field_trip', trip=trip,
            pickings=trip.picking_ids, expenses=trip.expense_ids,
            returns=trip.return_ids)

    @http.route('/vuma/field/pod/<int:picking_id>', type='http', auth='user',
                website=False)
    def field_pod(self, picking_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        picking = request.env['stock.picking'].browse(picking_id).exists()
        if not picking:
            return request.not_found()
        if not self._is_manager() and picking.vuma_trip_id.driver_id \
                != request.env.user and picking.user_id != request.env.user:
            raise AccessError(_('This delivery is not assigned to you.'))
        return self._field_template(
            'vuma_field_app.field_pod', picking=picking,
            moves=picking.move_ids, trip=picking.vuma_trip_id)

    @http.route('/vuma/field/expense', type='http', auth='user', website=False)
    def field_expense(self, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        return self._field_template(
            'vuma_field_app.field_expense', trips=self._driver_trips(limit=30))

    @http.route('/vuma/field/return/<int:picking_id>', type='http',
                auth='user', website=False)
    def field_return(self, picking_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        picking = request.env['stock.picking'].browse(picking_id).exists()
        if not picking:
            return request.not_found()
        if not self._is_manager() and picking.vuma_trip_id.driver_id \
                != request.env.user and picking.user_id != request.env.user:
            raise AccessError(_('This delivery is not assigned to you.'))
        return self._field_template(
            'vuma_field_app.field_return', picking=picking,
            moves=picking.move_ids, trip=picking.vuma_trip_id)

    @http.route('/vuma/field/collections', type='http', auth='user',
                website=False)
    def field_collections(self, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        return self._field_template(
            'vuma_field_app.field_collections',
            invoices=self._collection_invoices())

    @http.route('/vuma/field/collection/<int:invoice_id>', type='http',
                auth='user', website=False)
    def field_collection(self, invoice_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        invoice = self._collection_invoices(limit=1000).filtered(
            lambda rec: rec.id == invoice_id)
        if not invoice:
            return request.not_found()
        return self._field_template(
            'vuma_field_app.field_collection', invoice=invoice)

    @http.route('/vuma/field/offline', type='http', auth='public',
                website=False)
    def field_offline(self, **kwargs):
        # The offline shell is public so the service worker can cache and
        # display it when the device has no connection.  It still needs the
        # layout variables used by the shared field-app template.
        return request.render(
            'vuma_field_app.field_offline', self._field_context())

    @http.route('/vuma/field/health', type='http', auth='user',
                website=False)
    def field_health(self, **kwargs):
        return self._json_response({
            'ok': True,
            'user': request.env.user.login,
            'company': request.env.company.name,
        })

    # ---------------------------------------------------------- customers
    @http.route('/vuma/field/customers', type='http', auth='user',
                website=False)
    def field_customers(self, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        query = (kwargs.get('q') or '').strip()
        customers = request.env['res.partner'].sudo().search(
            self._customer_domain(query), order='name', limit=50)
        return self._field_template(
            'vuma_field_app.field_customers', customers=customers, q=query)

    @http.route('/vuma/field/customer/new', type='http', auth='user',
                website=False, methods=['GET', 'POST'], csrf=False)
    def field_customer_new(self, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        if request.httprequest.method == 'POST':
            params = request.params
            name = (params.get('name') or '').strip()
            if not name:
                return self._field_template(
                    'vuma_field_app.field_customer_new', values=params,
                    error=_('Customer name is required.'))
            change = request.env['vuma.customer.change.request'].create({
                'request_type': 'new_customer',
                'requested_name': name,
                'requested_phone': params.get('phone') or False,
                'requested_email': params.get('email') or False,
                'requested_street': params.get('street') or False,
                'requested_city': params.get('city') or False,
                'requested_zip': params.get('zip') or False,
                'requested_vat': params.get('vat') or False,
                'company_id': request.env.company.id,
                'requested_by_id': request.env.user.id,
                'notes': params.get('notes') or False,
            })
            change.action_submit()
            return request.redirect('/vuma/field?submitted=customer')
        return self._field_template(
            'vuma_field_app.field_customer_new', values={}, error=False)

    @http.route('/vuma/field/customer/<int:partner_id>', type='http',
                auth='user', website=False)
    def field_customer(self, partner_id, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        partner = self._customer_record(partner_id)
        if not partner:
            return request.not_found()
        invoices = self._customer_moves(partner)
        linked = request.env['vuma.customer.link'].sudo().search([
            '|', ('partner_id', '=', partner.id),
            ('linked_partner_id', '=', partner.id),
        ])
        linked_partners = linked.mapped('partner_id') | linked.mapped(
            'linked_partner_id')
        linked_partners -= partner
        quotes = request.env['sale.order'].sudo().search([
            ('partner_id', '=', partner.id),
            ('state', 'in', ('draft', 'sent')),
        ], order='date_order desc', limit=10)
        return self._field_template(
            'vuma_field_app.field_customer', customer=partner,
            invoices=invoices, linked=linked_partners, quotes=quotes,
            balance=sum(invoices.mapped('amount_residual')),
            submitted=kwargs.get('submitted'))

    @http.route('/vuma/field/customer/<int:partner_id>/update',
                type='http', auth='user', website=False,
                methods=['GET', 'POST'], csrf=False)
    def field_customer_update(self, partner_id, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        partner = self._customer_record(partner_id)
        if not partner:
            return request.not_found()
        if request.httprequest.method == 'POST':
            params = request.params
            change = request.env['vuma.customer.change.request'].create({
                'partner_id': partner.id,
                'request_type': 'update_details',
                'requested_name': params.get('name') or False,
                'requested_phone': params.get('phone') or False,
                'requested_email': params.get('email') or False,
                'requested_street': params.get('street') or False,
                'requested_city': params.get('city') or False,
                'requested_zip': params.get('zip') or False,
                'requested_vat': params.get('vat') or False,
                'company_id': partner.company_id.id or request.env.company.id,
                'requested_by_id': request.env.user.id,
                'notes': params.get('notes') or False,
            })
            change.action_submit()
            return request.redirect(
                '/vuma/field/customer/%s?submitted=update' % partner.id)
        return self._field_template(
            'vuma_field_app.field_customer_update', customer=partner,
            error=False)

    @http.route('/vuma/field/quote/new/<int:partner_id>', type='http',
                auth='user', website=False, methods=['GET', 'POST'], csrf=False)
    def field_quote_new(self, partner_id, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        partner = self._customer_record(partner_id)
        if not partner:
            return request.not_found()
        products = request.env['product.product'].sudo().search([
            ('sale_ok', '=', True), ('active', '=', True),
        ], order='name', limit=200)
        if request.httprequest.method == 'POST':
            params = request.params
            lines = []
            for index in range(1, 4):
                product_id = params.get('product_%s' % index)
                if not product_id:
                    continue
                try:
                    product = request.env['product.product'].sudo().browse(
                        int(product_id)).exists()
                    quantity = float(params.get('qty_%s' % index) or 0.0)
                except (TypeError, ValueError):
                    product, quantity = False, 0.0
                if not product or quantity <= 0:
                    continue
                pricelist = partner.property_product_pricelist
                price = (pricelist._get_product_price(product, quantity)
                         if pricelist else product.lst_price)
                lines.append(Command.create({
                    'product_id': product.id,
                    'name': product.display_name,
                    'product_uom_qty': quantity,
                    'product_uom_id': product.uom_id.id,
                    'price_unit': price,
                }))
            if not lines:
                return self._field_template(
                    'vuma_field_app.field_quote_new', customer=partner,
                    products=products,
                    error=_('Choose a product and quantity for at least one line.'))
            order = request.env['sale.order'].create({
                'partner_id': partner.id,
                'company_id': request.env.company.id,
                'user_id': request.env.user.id,
                'origin': 'Vuma Field App',
                'note': params.get('notes') or False,
                'order_line': lines,
            })
            return request.redirect(
                '/vuma/field/customer/%s?submitted=quote&order=%s'
                % (partner.id, order.id))
        return self._field_template(
            'vuma_field_app.field_quote_new', customer=partner,
            products=products, error=False)

    @http.route('/vuma/field/customer/<int:partner_id>/statement',
                type='http', auth='user', website=False)
    def field_statement(self, partner_id, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        partner = self._customer_record(partner_id)
        if not partner:
            return request.not_found()
        invoices = self._customer_moves(partner)
        payments = self._customer_payments(partner)
        return self._field_template(
            'vuma_field_app.field_statement', customer=partner,
            invoices=invoices, payments=payments,
            balance=sum(invoices.mapped('amount_residual')))

    @http.route('/vuma/field/uplift/<int:invoice_id>', type='http',
                auth='user', website=False, methods=['GET', 'POST'], csrf=False)
    def field_uplift(self, invoice_id, **kwargs):
        redirect = self._require_sales_user()
        if redirect:
            return redirect
        invoice = request.env['account.move'].sudo().browse(
            int(invoice_id)).exists()
        if not invoice or invoice.move_type != 'out_invoice'                 or invoice.state != 'posted':
            return request.not_found()
        lines = invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product' and line.product_id)
        if request.httprequest.method == 'POST':
            params = request.params
            commands = []
            for line in lines:
                try:
                    quantity = float(params.get('qty_%s' % line.id) or 0.0)
                except (TypeError, ValueError):
                    quantity = 0.0
                if quantity <= 0:
                    continue
                commands.append(Command.create({
                    'invoice_line_id': line.id,
                    'product_id': line.product_id.id,
                    'quantity': quantity,
                    'uom_id': line.product_uom_id.id
                    or line.product_id.uom_id.id,
                    'price_unit': line.price_unit,
                    'reason': params.get('reason') or 'customer_request',
                }))
            if not commands:
                return self._field_template(
                    'vuma_field_app.field_uplift', invoice=invoice,
                    lines=lines, error=_('Enter a quantity for at least one line.'))
            photo = False
            upload = request.httprequest.files.get('photo')
            if upload and upload.filename:
                photo = base64.b64encode(upload.read()).decode()
            uplift = request.env['vuma.uplift.request'].create({
                'partner_id': invoice.partner_id.id,
                'invoice_id': invoice.id,
                'sales_rep_id': request.env.user.id,
                'company_id': invoice.company_id.id,
                'reason': params.get('reason') or 'customer_request',
                'notes': params.get('notes') or False,
                'photo': photo,
                'line_ids': commands,
            })
            uplift.action_submit()
            return request.redirect(
                '/vuma/field/customer/%s?submitted=uplift' % invoice.partner_id.id)
        return self._field_template(
            'vuma_field_app.field_uplift', invoice=invoice, lines=lines,
            error=False)

    # ------------------------------------------------------------- static PWA
    @http.route('/vuma/field/manifest.json', type='http', auth='public',
                website=False)
    def field_manifest(self, **kwargs):
        try:
            with file_open('vuma_field_app/static/manifest.json', 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            data = b'{}'
        return Response(
            data, content_type='application/manifest+json',
            headers=[('Cache-Control', 'public, max-age=3600')])

    @http.route('/vuma/field/sw.js', type='http', auth='public',
                website=False)
    def field_service_worker(self, **kwargs):
        try:
            with file_open(
                    'vuma_field_app/static/src/js/service_worker.js', 'rb') as f:
                data = f.read()
        except FileNotFoundError:
            data = b'// service worker unavailable\n'
        return Response(
            data, content_type='application/javascript',
            headers=[
                ('Service-Worker-Allowed', '/vuma/field/'),
                ('Cache-Control', 'no-cache'),
            ])

    # --------------------------------------------------------------- actions
    def _action_payload(self):
        content_type = request.httprequest.content_type or ''
        if 'application/json' in content_type:
            try:
                return json.loads(
                    request.httprequest.get_data(as_text=True) or '{}')
            except ValueError:
                return {}
        return dict(request.params)

    def _run_action(self, action_type, client_uuid=None, **payload):
        client_uuid = client_uuid or payload.pop('client_uuid', None) \
            or str(uuid.uuid4())
        payload.pop('csrf_token', None)
        action = request.env['vuma.field.action'].receive(
            action_type, client_uuid, payload)
        action.process()
        result = {}
        try:
            result = json.loads(action.result or '{}')
        except ValueError:
            result = {}
        return self._json_response({
            'ok': action.state in ('processed', 'duplicate'),
            'state': action.state,
            'error': action.error,
            'result': result,
            'client_uuid': action.client_uuid,
        })

    @http.route('/vuma/field/pod/<int:picking_id>/submit', type='http',
                auth='user', website=False, methods=['POST'], csrf=False)
    def field_pod_submit(self, picking_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        picking = request.env['stock.picking'].browse(picking_id).exists()
        if not picking:
            return self._json_response({'ok': False, 'error': 'Not found'}, 404)
        payload = self._action_payload()
        payload['picking_id'] = picking.id
        return self._run_action('pod', **payload)

    @http.route('/vuma/field/expense/submit', type='http', auth='user',
                website=False, methods=['POST'], csrf=False)
    def field_expense_submit(self, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        return self._run_action('expense', **self._action_payload())

    @http.route('/vuma/field/return/<int:picking_id>/submit', type='http',
                auth='user', website=False, methods=['POST'], csrf=False)
    def field_return_submit(self, picking_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        payload = self._action_payload()
        payload['picking_id'] = picking_id
        return self._run_action('return', **payload)

    @http.route('/vuma/field/collection/<int:invoice_id>/submit', type='http',
                auth='user', website=False, methods=['POST'], csrf=False)
    def field_collection_submit(self, invoice_id, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        invoice = self._collection_invoices(limit=1000).filtered(
            lambda rec: rec.id == invoice_id)
        if not invoice:
            return self._json_response({'ok': False, 'error': 'Not found'}, 404)
        payload = self._action_payload()
        payload['partner_id'] = invoice.partner_id.id
        payload['invoice_ids'] = [invoice.id]
        return self._run_action('collection', **payload)

    @http.route('/vuma/field/sync', type='http', auth='user', website=False,
                methods=['POST'], csrf=False)
    def field_sync(self, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        payload = self._action_payload()
        actions = payload.get('actions') or []
        results = []
        for item in actions:
            action_type = item.get('type')
            action_payload = item.get('payload') or {}
            client_uuid = item.get('uuid') or str(uuid.uuid4())
            action = request.env['vuma.field.action'].receive(
                action_type, client_uuid, action_payload)
            action.process()
            results.append({
                'uuid': client_uuid,
                'type': action_type,
                'ok': action.state in ('processed', 'duplicate'),
                'state': action.state,
                'error': action.error,
            })
        return self._json_response({'ok': True, 'results': results})

    @http.route('/vuma/field/trip/<int:trip_id>/<string:action>', type='http',
                auth='user', website=False, methods=['POST'], csrf=False)
    def field_trip_action(self, trip_id, action, **kwargs):
        redirect = self._require_field_user()
        if redirect:
            return redirect
        trip = request.env['vuma.delivery.trip'].browse(trip_id).exists()
        if not trip:
            return self._json_response({'ok': False, 'error': 'Not found'}, 404)
        if not self._is_manager() and trip.driver_id != request.env.user:
            return self._json_response(
                {'ok': False, 'error': 'Not your trip'}, 403)
        if action == 'dispatch':
            trip.action_dispatch()
        elif action == 'return':
            trip.action_return()
        elif action == 'close':
            trip.action_close()
        else:
            return self._json_response(
                {'ok': False, 'error': 'Unknown action'}, 400)
        return self._json_response({'ok': True, 'state': trip.state})
