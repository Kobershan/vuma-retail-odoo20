# -*- coding: utf-8 -*-
"""Sales control: order checking, revisions, linked customers and uplifts."""
import json

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .access_control import vuma_require
from .approvals import vuma_has_group


class ResPartner(models.Model):
    _inherit = 'res.partner'

    vuma_link_ids = fields.One2many(
        'vuma.customer.link', 'partner_id', string='Linked Customers')
    vuma_linked_by_ids = fields.One2many(
        'vuma.customer.link', 'linked_partner_id', string='Linked From')
    vuma_group_credit_limit = fields.Monetary(
        string='Group Credit Limit', compute='_compute_vuma_group_credit',
        currency_field='currency_id')
    vuma_group_exposure = fields.Monetary(
        string='Group Exposure', compute='_compute_vuma_group_credit',
        currency_field='currency_id')
    vuma_group_overdue = fields.Monetary(
        string='Group Overdue', compute='_compute_vuma_group_credit',
        currency_field='currency_id')
    vuma_debtor_rating = fields.Selection(
        [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')],
        string='Debtor Rating', compute='_compute_vuma_rating')
    vuma_popia_consent = fields.Boolean(string='POPIA Consent')
    vuma_popia_consent_date = fields.Date(string='POPIA Consent Date')
    vuma_account_closed_date = fields.Date(string='Account Closed Date')
    vuma_credit_override_until = fields.Datetime(
        string='Credit Override Until')
    vuma_credit_override_reason = fields.Text(string='Credit Override Reason')

    def _vuma_group_partners(self):
        """Return the full connected customer group, not just one hop.

        A group can be recorded as A linked to B and B linked to C.  The
        exposure of A must include C, otherwise a group can be fragmented to
        stay under its credit limit.
        """
        self.ensure_one()
        group = self
        frontier = self
        seen = set(self.ids)
        while frontier:
            links = self.env['vuma.customer.link'].search([
                '|', ('partner_id', 'in', frontier.ids),
                ('linked_partner_id', 'in', frontier.ids),
            ])
            new_partners = (links.mapped('partner_id')
                            | links.mapped('linked_partner_id')) - group
            new_partners = new_partners.filtered(lambda p: p.id not in seen)
            if not new_partners:
                break
            seen.update(new_partners.ids)
            group |= new_partners
            frontier = new_partners
        return group

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        if not self.env.context.get('vuma_credit_limit_sync'):
            for partner, vals in zip(partners, vals_list):
                if 'credit_limit' in vals:
                    partner._vuma_share_credit_limit(
                        vals['credit_limit'] or 0.0)
        return partners

    def write(self, vals):
        res = super().write(vals)
        if 'credit_limit' in vals \
                and not self.env.context.get('vuma_credit_limit_sync'):
            self._vuma_share_credit_limit(vals['credit_limit'] or 0.0)
        return res

    def _vuma_share_credit_limit(self, credit_limit):
        """Keep one credit limit for the debtor in every trading company.

        ``credit_limit`` is company-dependent in Odoo: writing it while Trade
        Rite is active would leave Siyaya DC on the old value. Vuma manages a
        debtor once across both companies, so the limit entered on the Vuma
        Credit & Links tab is copied into every company's slice. Odoo's own
        credit warnings and the management reports then see the same limit
        whichever company is active.
        """
        companies = self.env['res.company'].sudo().search([])
        for partner in self.sudo():
            for company in companies:
                if company == self.env.company:
                    continue  # already written by super().write()
                partner.with_company(company).with_context(
                    vuma_credit_limit_sync=True,
                ).credit_limit = credit_limit

    @api.depends('credit_limit', 'credit', 'vuma_link_ids', 'vuma_linked_by_ids')
    def _compute_vuma_group_credit(self):
        # A debtor's limit is one number covering Trade Rite and Siyaya DC
        # together, so exposure must be totalled across every company, not
        # just the company the user happens to be working in. Otherwise a
        # customer could stay under the limit by splitting orders between the
        # two companies.
        companies = self.env['res.company'].sudo().search([])
        for partner in self:
            group = partner._vuma_group_partners()
            partner.vuma_group_credit_limit = sum(group.mapped('credit_limit'))
            exposure = 0.0
            for member in group.sudo():
                for company in companies:
                    member_company = member.with_company(company)
                    exposure += member_company.credit or 0.0
                    exposure += member_company.credit_to_invoice or 0.0
            partner.vuma_group_exposure = exposure
            partner.vuma_group_overdue = sum(
                getattr(p, 'vuma_overdue_amount', 0.0) or 0.0 for p in group)

    def _compute_vuma_rating(self):
        for partner in self:
            rating = self.env['vuma.debtor.rating'].search([
                ('partner_id', '=', partner.id),
                ('state', '=', 'confirmed'),
            ], order='date desc, id desc', limit=1)
            partner.vuma_debtor_rating = rating.override_rating or rating.rating \
                if rating else False

    def _vuma_credit_override_valid(self):
        self.ensure_one()
        return bool(
            self.vuma_credit_override_until
            and self.vuma_credit_override_until >= fields.Datetime.now())


class VumaCustomerLink(models.Model):
    _name = 'vuma.customer.link'
    _description = 'Linked Customer'
    _inherit = ['mail.thread']
    _order = 'partner_id, linked_partner_id'

    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    linked_partner_id = fields.Many2one(
        'res.partner', string='Linked Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    relation_type = fields.Selection(
        [('director', 'Same Director'), ('owner', 'Same Owner'),
         ('partner', 'Same Partner'), ('address', 'Same Address'),
         ('trading', 'Trading Name'), ('other', 'Other')],
        string='Relationship', required=True, default='director')
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company)
    notes = fields.Char(string='Notes')

    @api.constrains('partner_id', 'linked_partner_id')
    def _check_link(self):
        for link in self:
            if link.partner_id == link.linked_partner_id:
                raise ValidationError(_('A customer cannot be linked to itself.'))


class VumaCustomerChangeRequest(models.Model):
    _name = 'vuma.customer.change.request'
    _description = 'Customer Change Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'create_date desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', index=True,
        domain="[('customer_rank','>',0)]")
    request_type = fields.Selection(
        [('new_customer', 'New Customer'), ('update_details', 'Update Details'),
         ('bank_details', 'Banking Details'), ('credit_terms', 'Credit Terms'),
         ('address', 'Address Change')],
        string='Request Type', required=True, default='update_details')
    requested_name = fields.Char(string='Name')
    requested_phone = fields.Char(string='Phone')
    requested_email = fields.Char(string='Email')
    requested_street = fields.Char(string='Street')
    requested_city = fields.Char(string='City')
    requested_zip = fields.Char(string='Postal Code')
    requested_vat = fields.Char(string='VAT Number')
    requested_credit_limit = fields.Monetary(
        string='Requested Credit Limit', currency_field='currency_id',
        help='The limit applies to the customer in Trade Rite and Siyaya DC '
             'together.')
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    requested_by_id = fields.Many2one(
        'res.users', string='Requested By', default=lambda self: self.env.user,
        required=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Submitted'),
         ('approved', 'Approved'), ('rejected', 'Rejected')],
        string='Status', default='draft', required=True, tracking=True)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason')
    notes = fields.Text(string='Notes')

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.customer.change') or _('New')
        return super().create(vals_list)

    def action_submit(self):
        self.write({'state': 'submitted'})
        self.env['vuma.notification'].notify(
            self,
            ['vuma_retail_ops.group_vuma_ops_manager',
             'vuma_retail_ops.group_vuma_finance_manager',
             'vuma_retail_ops.group_vuma_owner'],
            _('Customer change waiting for approval'),
            _('%(name)s for %(partner)s was submitted by %(user)s.',
              name=', '.join(self.mapped('name')),
              partner=', '.join(filter(None, self.mapped('partner_id.name')))
              or ', '.join(filter(None, self.mapped('requested_name'))),
              user=self.env.user.name),
        )
        return True

    def _check_approver(self):
        vuma_require(self.env, 'action.approve',
                     _('You are not allowed to approve this change.'))
        if self.request_type in ('bank_details', 'credit_terms'):
            allowed = vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system')
        else:
            allowed = vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_ops_manager',
                'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system')
        if not allowed:
            raise AccessError(_('You are not allowed to approve this change.'))

    def action_approve(self):
        for request in self:
            request._check_approver()
            partner = request.partner_id
            vals = {}
            if request.requested_name:
                vals['name'] = request.requested_name
            if request.requested_phone:
                vals['phone'] = request.requested_phone
            if request.requested_email:
                vals['email'] = request.requested_email
            if request.requested_street:
                vals['street'] = request.requested_street
            if request.requested_city:
                vals['city'] = request.requested_city
            if request.requested_zip:
                vals['zip'] = request.requested_zip
            if request.requested_vat:
                vals['vat'] = request.requested_vat
            if request.requested_credit_limit:
                vals['credit_limit'] = request.requested_credit_limit
            if request.request_type == 'new_customer' and not partner:
                partner = self.env['res.partner'].create(dict(
                    vals, customer_rank=1, company_id=request.company_id.id))
                request.partner_id = partner
            elif partner and vals:
                partner.write(vals)
            request.write({
                'state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(
                partner, 'write', note='Approved customer change %s' % request.name)
            self.env['vuma.notification'].notify_user(
                request, request.requested_by_id,
                _('Customer change approved'),
                _('%(name)s was approved by %(user)s.',
                  name=request.name, user=self.env.user.name),
            )
        return True

    def action_reject(self):
        for request in self:
            request._check_approver()
            request.state = 'rejected'
            self.env['vuma.notification'].notify_user(
                request, request.requested_by_id,
                _('Customer change rejected'),
                _('%(name)s was rejected by %(user)s.',
                  name=request.name, user=self.env.user.name),
            )
        return True


class VumaUpliftRequest(models.Model):
    _name = 'vuma.uplift.request'
    _description = 'Uplift / Credit Request'
    _inherit = ['mail.thread', 'mail.activity.mixin', 'vuma.audit.mixin']
    _order = 'request_date desc, id desc'
    _vuma_audit_fields = ('state',)

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    invoice_id = fields.Many2one(
        'account.move', string='Invoice', required=True, index=True,
        domain="[('move_type','=','out_invoice'),('state','=','posted')]")
    sales_rep_id = fields.Many2one(
        'res.users', string='Sales Representative', required=True,
        default=lambda self: self.env.user)
    request_date = fields.Datetime(
        string='Requested On', default=fields.Datetime.now, required=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    reason = fields.Selection(
        [('customer_request', 'Customer Request'), ('damaged', 'Damaged'),
         ('wrong_item', 'Wrong Item'), ('not_fit', 'Not Needed'),
         ('quality', 'Quality Issue'), ('duplicate', 'Duplicate Purchase'),
         ('other', 'Other')],
        string='Reason', required=True, default='customer_request')
    notes = fields.Text(string='Notes')
    photo = fields.Image(string='Photo', max_width=1600, max_height=1600)
    state = fields.Selection(
        [('draft', 'Draft'), ('submitted', 'Waiting Approval'),
         ('approved', 'Approved - Collect'), ('rejected', 'Rejected'),
         ('collected', 'Collected'), ('closed', 'Closed')],
        string='Status', default='draft', required=True, tracking=True,
        index=True)
    approver_id = fields.Many2one(
        'res.users', string='Approver', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason')
    pickup_id = fields.Many2one(
        'customer.goods.pickup', string='Goods Pickup', readonly=True, copy=False)
    line_ids = fields.One2many(
        'vuma.uplift.request.line', 'request_id', string='Items', copy=True)
    line_count = fields.Integer(compute='_compute_line_count')
    total_qty = fields.Float(
        string='Total Quantity', compute='_compute_line_count',
        digits='Product Unit of Measure')

    @api.depends('line_ids.quantity')
    def _compute_line_count(self):
        for request in self:
            request.line_count = len(request.line_ids)
            request.total_qty = sum(request.line_ids.mapped('quantity'))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.uplift.request') or _('New')
        return super().create(vals_list)

    @api.onchange('invoice_id')
    def _onchange_invoice_id(self):
        if self.invoice_id:
            self.partner_id = self.invoice_id.partner_id
            self.company_id = self.invoice_id.company_id
            self.line_ids = [Command.create({
                'invoice_line_id': line.id,
                'product_id': line.product_id.id,
                'quantity': 0.0,
                'price_unit': line.price_unit,
                'uom_id': line.product_uom_id.id or line.product_id.uom_id.id,
                'reason': self.reason,
            }) for line in self.invoice_id.invoice_line_ids
                if line.display_type == 'product' and line.product_id]

    def action_submit(self):
        for request in self:
            if not request.line_ids.filtered(lambda l: l.quantity > 0):
                raise UserError(_('Enter the quantity being returned on at least one line.'))
            request.write({'state': 'submitted'})
            request.message_post(body=_('Uplift request submitted for live approval.'))
            self.env['vuma.audit.log'].log(request, 'approve', note='Submitted')
            self.env['vuma.notification'].notify(
                request,
                ['vuma_retail_ops.group_vuma_ops_manager',
                 'vuma_retail_ops.group_vuma_owner'],
                _('Uplift request waiting for approval'),
                _('%(name)s for %(partner)s was submitted by %(user)s.',
                  name=request.name, partner=request.partner_id.name,
                  user=self.env.user.name),
            )
        return True

    def _check_approver(self):
        vuma_require(self.env, 'action.approve',
                     _('Uplift requests are approved by the operations manager.'))
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_ops_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('Uplift requests are approved by the operations manager.'))

    def action_approve(self):
        for request in self:
            request._check_approver()
            warehouse = self.env['stock.warehouse'].search(
                [('company_id', '=', request.company_id.id)], limit=1)
            if not warehouse:
                raise UserError(_('No warehouse is configured for this company.'))
            pickup = self.env['customer.goods.pickup'].create({
                'partner_id': request.partner_id.id,
                'invoice_id': request.invoice_id.id,
                'sales_rep_id': request.sales_rep_id.id,
                'company_id': request.company_id.id,
                'warehouse_id': warehouse.id,
                'dest_location_id': warehouse.lot_stock_id.id,
                'pickup_reason': 'customer_request' if request.reason == 'customer_request'
                                 else 'other',
                'pickup_reason_notes': request.reason,
                'notes': request.notes,
                'vuma_uplift_request_id': request.id,
            })
            for line in request.line_ids.filtered(lambda l: l.quantity > 0):
                pickup_line = pickup.line_ids.filtered(
                    lambda pl: pl.invoice_line_id == line.invoice_line_id)
                if pickup_line:
                    pickup_line.qty_collected = min(
                        line.quantity, pickup_line.eligible_qty or line.quantity)
                    pickup_line.return_reason = line.reason
            request.write({
                'state': 'approved',
                'approver_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
                'pickup_id': pickup.id,
            })
            request.message_post(body=_(
                'Approved. Pickup %(pickup)s is ready for the rep to confirm.',
                pickup=pickup.name))
            self.env['vuma.audit.log'].log(request, 'approve')
            self.env['vuma.notification'].notify_user(
                request, request.sales_rep_id,
                _('Uplift request approved'),
                _('%(name)s was approved. Pickup %(pickup)s is ready.',
                  name=request.name, pickup=pickup.name),
            )
        return True

    def action_reject(self):
        for request in self:
            request._check_approver()
            request.state = 'rejected'
            self.env['vuma.audit.log'].log(request, 'reject', note=request.rejection_reason)
            self.env['vuma.notification'].notify_user(
                request, request.sales_rep_id,
                _('Uplift request rejected'),
                _('%(name)s was rejected by %(user)s.',
                  name=request.name, user=self.env.user.name),
            )
        return True

    def action_view_pickup(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'res_model': 'customer.goods.pickup',
            'view_mode': 'form',
            'res_id': self.pickup_id.id,
        }


class VumaUpliftRequestLine(models.Model):
    _name = 'vuma.uplift.request.line'
    _description = 'Uplift Request Line'

    request_id = fields.Many2one(
        'vuma.uplift.request', required=True, ondelete='cascade', index=True)
    invoice_line_id = fields.Many2one(
        'account.move.line', string='Invoice Line', required=True)
    product_id = fields.Many2one(
        'product.product', string='Product', required=True)
    quantity = fields.Float(
        string='Quantity', digits='Product Unit of Measure')
    uom_id = fields.Many2one('uom.uom', string='Unit', required=True)
    price_unit = fields.Monetary(
        string='Unit Price', currency_field='currency_id')
    currency_id = fields.Many2one(
        related='request_id.currency_id', store=True, readonly=True)
    reason = fields.Selection(
        [('customer_request', 'Customer Request'), ('damaged', 'Damaged'),
         ('wrong_item', 'Wrong Item'), ('not_fit', 'Not Needed'),
         ('quality', 'Quality Issue'), ('duplicate', 'Duplicate Purchase'),
         ('other', 'Other')],
        string='Reason', default='customer_request')
    notes = fields.Char(string='Notes')
    invoiced_qty = fields.Float(
        string='Invoiced', related='invoice_line_id.quantity', readonly=True,
        digits='Product Unit of Measure')


class CustomerGoodsPickupUplift(models.Model):
    _inherit = 'customer.goods.pickup'

    vuma_uplift_request_id = fields.Many2one(
        'vuma.uplift.request', string='Uplift Request', readonly=True, copy=False)


class SaleOrderChecks(models.Model):
    _inherit = 'sale.order'

    vuma_check_state = fields.Selection(
        [('not_run', 'Not Checked'), ('passed', 'Passed'),
         ('warning', 'Warning'), ('blocked', 'Blocked')],
        string='Order Check', default='not_run', copy=False, tracking=True)
    vuma_check_date = fields.Datetime(string='Checked On', readonly=True, copy=False)
    vuma_check_user_id = fields.Many2one(
        'res.users', string='Checked By', readonly=True, copy=False)
    vuma_check_notes = fields.Text(string='Check Notes', readonly=True, copy=False)
    vuma_stock_ok = fields.Boolean(string='Stock Available', readonly=True, copy=False)
    vuma_credit_ok = fields.Boolean(string='Credit Available', readonly=True, copy=False)
    vuma_margin_ok = fields.Boolean(string='Margin Acceptable', readonly=True, copy=False)
    vuma_duplicate_ok = fields.Boolean(string='No Duplicate', readonly=True, copy=False)
    vuma_credit_override_by_id = fields.Many2one(
        'res.users', string='Credit Override By', readonly=True, copy=False)
    vuma_credit_override_until = fields.Datetime(
        string='Credit Override Until', readonly=True, copy=False)
    vuma_credit_override_reason = fields.Text(
        string='Credit Override Reason', readonly=True, copy=False)
    vuma_revision_ids = fields.One2many(
        'vuma.quote.revision', 'order_id', string='Quote Revisions')
    vuma_revision_count = fields.Integer(compute='_compute_vuma_revision_count')

    @api.depends('vuma_revision_ids')
    def _compute_vuma_revision_count(self):
        for order in self:
            order.vuma_revision_count = len(order.vuma_revision_ids)


    def action_vuma_repeat_order(self):
        """Create a fresh quotation from a previous order in one action."""
        self.ensure_one()
        new_order = self.copy({
            'date_order': fields.Datetime.now(),
            'origin': _('Repeat of %s', self.name),
            'client_order_ref': self.client_order_ref,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Repeated Quotation'),
            'res_model': 'sale.order',
            'view_mode': 'form',
            'res_id': new_order.id,
            'target': 'current',
        }

    def _vuma_check_order(self):
        self.ensure_one()
        notes = []
        stock_ok = True
        for line in self.order_line.filtered(
                lambda l: l.display_type == 'product' and l.product_id.is_storable):
            if line.product_id.free_qty < line.product_uom_qty:
                stock_ok = False
                notes.append(_(
                    'Short stock: %(product)s needs %(need).2f, available %(free).2f.',
                    product=line.product_id.display_name,
                    need=line.product_uom_qty,
                    free=line.product_id.free_qty))
        partner = self.partner_id.commercial_partner_id
        credit_ok = True
        if partner.vuma_credit_hold and not partner._vuma_credit_override_valid():
            credit_ok = False
            notes.append(_('Customer is on credit hold for %(amount).2f overdue.',
                           amount=partner.vuma_overdue_amount))
        limit = partner.vuma_group_credit_limit or partner.credit_limit
        exposure = partner.vuma_group_exposure
        if limit and (exposure + self.amount_total) > limit:
            credit_ok = False
            notes.append(_(
                'Credit limit exceeded: exposure %(exposure).2f + order %(order).2f '
                'is above the limit of %(limit).2f.',
                exposure=exposure, order=self.amount_total, limit=limit))
        margin_ok = True
        margin = getattr(self, 'margin', 0.0) or 0.0
        margin_percent = getattr(self, 'margin_percent', 0.0) or 0.0
        if margin < 0 or (margin_percent and margin_percent < 5):
            margin_ok = False
            notes.append(_('Margin is below the 5%% floor (%(pct).2f%%).',
                           pct=margin_percent))
        duplicate_ok = True
        duplicate = self.search([
            ('id', '!=', self.id),
            ('partner_id', '=', self.partner_id.id),
            ('company_id', '=', self.company_id.id),
            ('amount_total', '=', self.amount_total),
            ('state', 'in', ('draft', 'sent', 'sale')),
            ('date_order', '>=', fields.Datetime.subtract(
                fields.Datetime.now(), days=1)),
        ], limit=1)
        if duplicate:
            duplicate_ok = False
            notes.append(_('Possible duplicate of %s.', duplicate.name))
        details_ok = bool(
            partner.name
            and (partner.phone or partner.email)
            and (partner.street or partner.street2)
        )
        if not details_ok:
            notes.append(_(
                'Customer master data is incomplete: add a contact number/email '
                'and a delivery address before dispatch.'))
        if not credit_ok:
            state = 'blocked'
        elif not (stock_ok and margin_ok and duplicate_ok and details_ok):
            state = 'warning'
        else:
            state = 'passed'
        self.write({
            'vuma_check_state': state,
            'vuma_check_date': fields.Datetime.now(),
            'vuma_check_user_id': self.env.user.id,
            'vuma_check_notes': '\n'.join(notes) or _('All checks passed.'),
            'vuma_stock_ok': stock_ok,
            'vuma_credit_ok': credit_ok,
            'vuma_margin_ok': margin_ok,
            'vuma_duplicate_ok': duplicate_ok,
        })
        return state

    def action_vuma_run_checks(self):
        for order in self:
            order._vuma_check_order()
        return True

    def action_vuma_override_credit(self):
        for order in self:
            if not vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_owner',
                    'base.group_system'):
                raise AccessError(_('Credit overrides are an owner decision.'))
            if not (order.vuma_credit_override_reason or '').strip():
                raise UserError(_('Give a reason for the credit override.'))
            order.write({
                'vuma_credit_override_by_id': self.env.user.id,
                'vuma_credit_override_until': fields.Datetime.add(
                    fields.Datetime.now(), days=7),
                'vuma_check_state': 'warning',
            })
            # The limit applies to the whole linked customer group, so the
            # override must travel with the group rather than only this debtor.
            for partner in order.partner_id._vuma_group_partners():
                partner.write({
                    'vuma_credit_override_until': order.vuma_credit_override_until,
                    'vuma_credit_override_reason': order.vuma_credit_override_reason,
                })
            self.env['vuma.audit.log'].log(
                order, 'override', note=order.vuma_credit_override_reason)
            self.env['vuma.notification'].notify(
                order,
                ['vuma_retail_ops.group_vuma_finance_manager',
                 'vuma_retail_ops.group_vuma_owner'],
                _('Credit override granted'),
                _('%(order)s for %(partner)s has a credit override by %(user)s until %(until)s.',
                  order=order.name, partner=order.partner_id.name,
                  user=self.env.user.name,
                  until=order.vuma_credit_override_until),
            )
        return True

    def action_vuma_save_revision(self):
        for order in self:
            revision = len(order.vuma_revision_ids) + 1
            snapshot = [{
                'product': line.product_id.display_name,
                'quantity': line.product_uom_qty,
                'price_unit': line.price_unit,
                'discount': line.discount,
                'subtotal': line.price_subtotal,
            } for line in order.order_line if line.display_type == 'product']
            self.env['vuma.quote.revision'].create({
                'order_id': order.id,
                'revision_no': revision,
                'amount_untaxed': order.amount_untaxed,
                'amount_total': order.amount_total,
                'currency_id': order.currency_id.id,
                'snapshot': json.dumps(snapshot, indent=2, default=str),
                'note': _('Saved revision %s', revision),
            })
        return True

    def action_confirm(self):
        for order in self:
            if order.state in ('draft', 'sent'):
                if order.vuma_check_state == 'not_run':
                    order._vuma_check_order()
                if order.vuma_check_state == 'blocked' \
                        and not order.partner_id._vuma_credit_override_valid():
                    raise UserError(_(
                        'Order %(order)s is blocked by the order check. '
                        'Resolve the credit issue or get an owner override.',
                        order=order.name))
                order.action_vuma_save_revision()
        return super().action_confirm()


class VumaQuoteRevision(models.Model):
    _name = 'vuma.quote.revision'
    _description = 'Quote Revision Snapshot'
    _order = 'order_id, revision_no desc'

    order_id = fields.Many2one(
        'sale.order', required=True, ondelete='cascade', index=True)
    revision_no = fields.Integer(string='Revision', required=True)
    date = fields.Datetime(
        string='Saved On', default=fields.Datetime.now, required=True)
    user_id = fields.Many2one(
        'res.users', string='Saved By', default=lambda self: self.env.user)
    amount_untaxed = fields.Monetary(
        string='Untaxed', currency_field='currency_id')
    amount_total = fields.Monetary(
        string='Total', currency_field='currency_id')
    currency_id = fields.Many2one('res.currency', string='Currency')
    snapshot = fields.Text(string='Lines Snapshot')
    note = fields.Char(string='Note')


class VumaConsolidatedInvoiceWizard(models.TransientModel):
    _name = 'vuma.consolidated.invoice.wizard'
    _description = 'Consolidated Invoice Wizard'

    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        domain="[('customer_rank','>',0)]")
    date_from = fields.Date(string='From', required=True)
    date_to = fields.Date(string='To', required=True)
    company_ids = fields.Many2many(
        'res.company', string='Companies', required=True,
        default=lambda self: self.env.companies.ids)
    group_by_town = fields.Boolean(string='Group by Town')
    invoice_date = fields.Date(
        string='Invoice Date', default=fields.Date.context_today, required=True)

    def action_create_invoices(self):
        self.ensure_one()
        orders = self.env['sale.order'].search([
            ('partner_id', 'child_of', self.partner_id.id),
            ('company_id', 'in', self.company_ids.ids),
            ('state', '=', 'sale'),
            ('invoice_status', '=', 'to invoice'),
            ('date_order', '>=', fields.Datetime.to_datetime(self.date_from)),
            ('date_order', '<=', fields.Datetime.to_datetime(self.date_to).replace(
                hour=23, minute=59, second=59)),
        ])
        if not orders:
            raise UserError(_('No confirmed orders are waiting to be invoiced.'))
        invoices = self.env['account.move']
        groups = {}
        for order in orders:
            key = (order.company_id.id, order.partner_id.id,
                   (order.partner_shipping_id.city or '') if self.group_by_town else '')
            groups.setdefault(key, self.env['sale.order'])
            groups[key] |= order
        for (company_id, partner_id, town), group in groups.items():
            company = self.env['res.company'].browse(company_id)
            lines = []
            for order in group:
                for line in order.order_line.filtered(
                        lambda l: l.display_type == 'product'
                        and l.qty_to_invoice > 0):
                    lines.append(Command.create({
                        'product_id': line.product_id.id,
                        'name': line.name,
                        'quantity': line.qty_to_invoice,
                        'price_unit': line.price_unit,
                        'discount': line.discount,
                        'tax_ids': [Command.set(line.tax_id.ids)],
                        'sale_line_ids': [Command.link(line.id)],
                    }))
            if not lines:
                continue
            invoice = self.env['account.move'].with_company(company).create({
                'move_type': 'out_invoice',
                'partner_id': partner_id,
                'invoice_date': self.invoice_date,
                'date': self.invoice_date,
                'company_id': company_id,
                'invoice_origin': ', '.join(group.mapped('name')),
                'invoice_line_ids': lines,
            })
            invoices |= invoice
        if not invoices:
            raise UserError(_('There was nothing left to invoice in that period.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Consolidated Invoices'),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', invoices.ids)],
        }


class VumaStandingOrder(models.Model):
    _name = 'vuma.standing.order'
    _description = 'Standing Order'
    _inherit = ['mail.thread', 'vuma.audit.mixin']
    _order = 'date_next, id'
    _vuma_audit_fields = ('state', 'date_next')

    name = fields.Char(
        string='Reference', default=lambda self: _('New'), readonly=True,
        copy=False, required=True)
    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True, index=True,
        domain="[('customer_rank','>',0)]")
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        required=True)
    user_id = fields.Many2one(
        'res.users', string='Salesperson', default=lambda self: self.env.user)
    date_start = fields.Date(
        string='Start Date', default=fields.Date.context_today, required=True)
    date_next = fields.Date(
        string='Next Order Date', default=fields.Date.context_today,
        required=True, index=True)
    interval = fields.Selection(
        [('weekly', 'Weekly'), ('biweekly', 'Every two weeks'),
         ('monthly', 'Monthly'), ('quarterly', 'Quarterly'),
         ('annual', 'Annual')],
        string='Frequency', default='monthly', required=True)
    state = fields.Selection(
        [('active', 'Active'), ('paused', 'Paused'), ('closed', 'Closed')],
        string='Status', default='active', required=True, tracking=True)
    note = fields.Text(string='Delivery / order notes')
    line_ids = fields.One2many(
        'vuma.standing.order.line', 'standing_id', string='Lines', copy=True)
    last_order_id = fields.Many2one(
        'sale.order', string='Last Generated Order', readonly=True, copy=False)
    last_generated = fields.Date(string='Last Generated', readonly=True, copy=False)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                vals['name'] = self.env['ir.sequence'].next_by_code(
                    'vuma.standing.order') or _('New')
        return super().create(vals_list)

    def _next_date(self, current):
        self.ensure_one()
        mapping = {
            'weekly': relativedelta(weeks=1),
            'biweekly': relativedelta(weeks=2),
            'monthly': relativedelta(months=1),
            'quarterly': relativedelta(months=3),
            'annual': relativedelta(years=1),
        }
        return current + mapping.get(self.interval, relativedelta(months=1))

    def action_generate_order(self):
        for standing in self:
            if standing.state != 'active':
                continue
            if not standing.line_ids:
                raise UserError(_('Add at least one product to the standing order.'))
            partner = standing.partner_id
            pricelist = partner.property_product_pricelist
            lines = []
            for line in standing.line_ids.filtered(lambda l: l.product_id):
                quantity = line.product_uom_qty or 1.0
                price = (pricelist._get_product_price(
                    line.product_id, quantity) if pricelist
                    else line.product_id.lst_price)
                lines.append(Command.create({
                    'product_id': line.product_id.id,
                    'name': line.product_id.display_name,
                    'product_uom_qty': quantity,
                    'product_uom_id': line.product_id.uom_id.id,
                    'price_unit': price,
                }))
            order = self.env['sale.order'].create({
                'partner_id': partner.id,
                'company_id': standing.company_id.id,
                'user_id': (standing.user_id or self.env.user).id,
                'origin': _('Standing order %s', standing.name),
                'note': standing.note,
                'order_line': lines,
            })
            today = fields.Date.context_today(self)
            standing.write({
                'last_order_id': order.id,
                'last_generated': today,
                'date_next': standing._next_date(max(today, standing.date_next)),
            })
            self.env['vuma.audit.log'].log(
                standing, 'create', note='Generated order %s' % order.name)
        return True

    @api.model
    def _cron_generate_due_orders(self):
        today = fields.Date.context_today(self)
        due = self.search([
            ('state', '=', 'active'), ('date_next', '<=', today),
        ])
        for standing in due:
            standing.action_generate_order()
        return len(due)


class VumaStandingOrderLine(models.Model):
    _name = 'vuma.standing.order.line'
    _description = 'Standing Order Line'

    standing_id = fields.Many2one(
        'vuma.standing.order', required=True, ondelete='cascade', index=True)
    product_id = fields.Many2one(
        'product.product', string='Product', required=True,
        domain="[('sale_ok','=',True)]")
    product_uom_qty = fields.Float(
        string='Quantity', default=1.0, required=True,
        digits='Product Unit of Measure')
    note = fields.Char(string='Line Note')
