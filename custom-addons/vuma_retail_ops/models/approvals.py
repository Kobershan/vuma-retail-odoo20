# -*- coding: utf-8 -*-
"""Approval rules that protect the money.

Two documents carry most of the risk: a purchase order commits cash, and a
credit note gives cash back. Both are value-banded here, with the bands
confirmed in the requirements blueprint.
"""
from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


def vuma_has_group(env, *xmlids):
    return any(env.user.has_group(xmlid) for xmlid in xmlids)


class VumaApprovalMixin(models.AbstractModel):
    _name = 'vuma.approval.mixin'
    _description = 'Vuma Approval Mixin'

    approval_state = fields.Selection(
        [
            ('draft', 'Draft'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
            ('cancelled', 'Cancelled'),
        ],
        string='Approval', default='draft', copy=False, index=True)
    submitted_by_id = fields.Many2one(
        'res.users', string='Submitted By', readonly=True, copy=False)
    submitted_date = fields.Datetime(
        string='Submitted On', readonly=True, copy=False)
    approved_by_id = fields.Many2one(
        'res.users', string='Approved By', readonly=True, copy=False)
    approved_date = fields.Datetime(
        string='Approved On', readonly=True, copy=False)
    rejected_by_id = fields.Many2one(
        'res.users', string='Rejected By', readonly=True, copy=False)
    rejected_date = fields.Datetime(
        string='Rejected On', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason', copy=False)

    def action_submit(self):
        for rec in self:
            if rec.approval_state not in ('draft', 'rejected'):
                raise UserError(_('Only a draft or rejected record can be submitted.'))
            rec.write({
                'approval_state': 'pending',
                'submitted_by_id': self.env.user.id,
                'submitted_date': fields.Datetime.now(),
                'rejection_reason': False,
            })
            self.env['vuma.audit.log'].log(rec, 'approve', note='Submitted for approval')
        return True

    def action_approve(self):
        for rec in self:
            rec._vuma_check_approver()
            rec.write({
                'approval_state': 'approved',
                'approved_by_id': self.env.user.id,
                'approved_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(rec, 'approve')
        return True

    def action_reject(self):
        for rec in self:
            rec._vuma_check_approver()
            rec.write({
                'approval_state': 'rejected',
                'rejected_by_id': self.env.user.id,
                'rejected_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(rec, 'reject', note=rec.rejection_reason)
        return True

    def _vuma_check_approver(self):
        if not vuma_has_group(
                self.env, 'vuma_retail_ops.group_vuma_ops_manager',
                'vuma_retail_ops.group_vuma_finance_manager',
                'vuma_retail_ops.group_vuma_owner', 'base.group_system'):
            raise AccessError(_('You are not allowed to approve this record.'))


class PurchaseOrder(models.Model):
    _inherit = 'purchase.order'

    vuma_approval_state = fields.Selection(
        [
            ('not_required', 'Not Required'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        string='Vuma Approval', default='not_required', copy=False,
        tracking=True, index=True)
    vuma_approval_level = fields.Selection(
        [
            ('buyer', 'Buyer (up to R10,000)'),
            ('operations', 'Operations Manager (up to R50,000)'),
            ('finance', 'Financial Manager (up to R150,000)'),
            ('owner', 'Owner + Finance (above R150,000)'),
        ],
        string='Approval Band', compute='_compute_vuma_approval_band',
        store=True, readonly=True)
    vuma_approved_by_id = fields.Many2one(
        'res.users', string='First Approved By', readonly=True, copy=False)
    vuma_approved_date = fields.Datetime(
        string='First Approved On', readonly=True, copy=False)
    vuma_second_approved_by_id = fields.Many2one(
        'res.users', string='Second Approved By', readonly=True, copy=False)
    vuma_second_approved_date = fields.Datetime(
        string='Second Approved On', readonly=True, copy=False)
    vuma_approval_note = fields.Text(string='Approval Note', copy=False)
    vuma_rejection_reason = fields.Text(string='Rejection Reason', copy=False)
    vuma_second_approval_required = fields.Boolean(
        string='Second Approval Required', compute='_compute_vuma_approval_band',
        store=True, readonly=True)
    vuma_is_emergency = fields.Boolean(string='Emergency Purchase')
    vuma_emergency_reason = fields.Char(string='Emergency Reason')
    vuma_emergency_deadline = fields.Datetime(
        string='Retrospective Approval Due', readonly=True, copy=False)

    @api.depends('amount_total')
    def _compute_vuma_approval_band(self):
        for order in self:
            amount = abs(order.amount_total or 0.0)
            if amount <= 10000:
                level = 'buyer'
            elif amount <= 50000:
                level = 'operations'
            elif amount <= 150000:
                level = 'finance'
            else:
                level = 'owner'
            order.vuma_approval_level = level
            order.vuma_second_approval_required = amount > 150000

    @api.model_create_multi
    def create(self, vals_list):
        orders = super().create(vals_list)
        for order in orders:
            if order.vuma_approval_state == 'not_required':
                order.vuma_approval_state = 'pending'
        return orders

    def write(self, vals):
        res = super().write(vals)
        if {'order_line', 'amount_total'} & set(vals):
            for order in self:
                if order.vuma_approval_state == 'approved':
                    order.vuma_approval_state = 'pending'
                    order.vuma_approved_by_id = False
                    order.vuma_approved_date = False
                    order.vuma_second_approved_by_id = False
                    order.vuma_second_approved_date = False
        return res

    def _approval_allowed(self):
        self.ensure_one()
        if self.vuma_approval_state in ('not_required', 'approved'):
            return super()._approval_allowed()
        # An emergency purchase may be confirmed before approval, but the
        # retrospective approval deadline is 24 hours. Once that deadline
        # passes the normal block applies again.
        if self.vuma_is_emergency and self.vuma_emergency_deadline \
                and self.vuma_emergency_deadline >= fields.Datetime.now():
            return super()._approval_allowed()
        return False

    def _vuma_primary_group(self):
        self.ensure_one()
        return {
            'buyer': ('purchase.group_purchase_user',),
            'operations': ('vuma_retail_ops.group_vuma_ops_manager',),
            'finance': ('vuma_retail_ops.group_vuma_finance_manager',),
            # The owner band is a two-key control: either the owner or the
            # financial manager may give the first approval, and the second
            # key must come from the other role.
            'owner': ('vuma_retail_ops.group_vuma_owner',
                      'vuma_retail_ops.group_vuma_finance_manager'),
        }.get(self.vuma_approval_level, ('purchase.group_purchase_user',))

    def _vuma_has_role(self, user, role_group):
        return bool(user and user.has_group(role_group))

    def action_vuma_submit_approval(self):
        for order in self:
            if order.state not in ('draft', 'sent', 'to approve'):
                raise UserError(_('This purchase order can no longer be submitted.'))
            order.write({
                'vuma_approval_state': 'pending',
                'vuma_approved_by_id': False,
                'vuma_approved_date': False,
                'vuma_second_approved_by_id': False,
                'vuma_second_approved_date': False,
                'vuma_rejection_reason': False,
            })
            self.env['vuma.audit.log'].log(order, 'approve', note='Submitted for approval')
        return True

    def action_vuma_approve(self):
        for order in self:
            if order.vuma_approval_state == 'approved':
                continue
            if not vuma_has_group(self.env, *order._vuma_primary_group()):
                raise AccessError(_(
                    'This purchase order must be approved by the %s.',
                    dict(order._fields['vuma_approval_level'].selection)[
                        order.vuma_approval_level]))
            first_approval = not order.vuma_approved_by_id
            if first_approval:
                order.write({
                    'vuma_approved_by_id': self.env.user.id,
                    'vuma_approved_date': fields.Datetime.now(),
                })
                self.env['vuma.audit.log'].log(order, 'approve', note='First approval')
                # A two-key order remains pending after the first key.  The
                # second call is made by another user and is validated below.
                if order.vuma_second_approval_required:
                    continue
            if order.vuma_second_approval_required:
                if not vuma_has_group(
                        self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                        'vuma_retail_ops.group_vuma_owner'):
                    raise AccessError(_(
                        'A purchase order above R150,000 also needs financial '
                        'manager approval.'))
                if not order.vuma_second_approved_by_id:
                    if order.vuma_approved_by_id == self.env.user:
                        raise UserError(_(
                            'The second approval must be given by a different user.'))
                    first = order.vuma_approved_by_id
                    first_owner = self._vuma_has_role(
                        first, 'vuma_retail_ops.group_vuma_owner')
                    first_finance = self._vuma_has_role(
                        first, 'vuma_retail_ops.group_vuma_finance_manager')
                    current_owner = self._vuma_has_role(
                        self.env.user, 'vuma_retail_ops.group_vuma_owner')
                    current_finance = self._vuma_has_role(
                        self.env.user, 'vuma_retail_ops.group_vuma_finance_manager')
                    if not ((first_owner or current_owner)
                            and (first_finance or current_finance)):
                        raise AccessError(_(
                            'A purchase order above R150,000 needs both the owner '
                            'and the financial manager to approve it.'))
                    order.write({
                        'vuma_second_approved_by_id': self.env.user.id,
                        'vuma_second_approved_date': fields.Datetime.now(),
                    })
                    self.env['vuma.audit.log'].log(
                        order, 'approve', note='Second approval')
            order.vuma_approval_state = 'approved'
            if order.state == 'to approve':
                order.button_approve()
        return True

    def action_vuma_reject(self):
        for order in self:
            order.vuma_approval_state = 'rejected'
            self.env['vuma.audit.log'].log(
                order, 'reject', note=order.vuma_rejection_reason)
        return True

    def action_vuma_emergency(self):
        for order in self:
            if not order.vuma_is_emergency:
                raise UserError(_('Tick Emergency Purchase first.'))
            if not (order.vuma_emergency_reason or '').strip():
                raise UserError(_('Give the reason for the emergency purchase.'))
            order.vuma_emergency_deadline = fields.Datetime.add(
                fields.Datetime.now(), hours=24)
            self.env['vuma.audit.log'].log(
                order, 'override', note='Emergency purchase: %s' % order.vuma_emergency_reason)
        return True


class AccountMove(models.Model):
    _inherit = 'account.move'

    vuma_credit_approval_state = fields.Selection(
        [
            ('not_required', 'Not Required'),
            ('pending', 'Pending Approval'),
            ('approved', 'Approved'),
            ('rejected', 'Rejected'),
        ],
        string='Credit Note Approval', default='not_required', copy=False,
        tracking=True, index=True)
    vuma_credit_approval_level = fields.Selection(
        [
            ('finance', 'Financial Manager (up to R25,000)'),
            ('owner', 'Owner (above R25,000)'),
        ],
        string='Credit Approval Band', compute='_compute_vuma_credit_band',
        store=True, readonly=True)
    vuma_credit_approved_by_id = fields.Many2one(
        'res.users', string='Credit Approved By', readonly=True, copy=False)
    vuma_credit_approved_date = fields.Datetime(
        string='Credit Approved On', readonly=True, copy=False)
    vuma_credit_rejection_reason = fields.Text(
        string='Credit Rejection Reason', copy=False)
    vuma_credit_override_reason = fields.Text(string='Credit Override Reason')
    vuma_supplier_claim_id = fields.Many2one(
        'vuma.supplier.claim', string='Supplier Claim', readonly=True, copy=False)
    vuma_rebate_claim_id = fields.Many2one(
        'vuma.rebate.claim', string='Rebate Claim', readonly=True, copy=False)

    @api.depends('amount_total')
    def _compute_vuma_credit_band(self):
        for move in self:
            move.vuma_credit_approval_level = (
                'owner' if abs(move.amount_total or 0.0) > 25000 else 'finance')

    @api.model_create_multi
    def create(self, vals_list):
        moves = super().create(vals_list)
        for move in moves:
            if move.move_type in ('out_refund', 'in_refund') \
                    and move.vuma_credit_approval_state == 'not_required':
                move.vuma_credit_approval_state = (
                    'pending' if abs(move.amount_total or 0.0) > 5000
                    else 'approved')
                if move.vuma_credit_approval_state == 'approved':
                    move.vuma_credit_approved_by_id = self.env.user.id
                    move.vuma_credit_approved_date = fields.Datetime.now()
        return moves

    def _vuma_credit_source_linked(self):
        self.ensure_one()
        return bool(
            self.reversed_entry_id
            or self.invoice_origin
            or self.ref
            or getattr(self, 'vuma_delivery_return_id', False)
            or self.vuma_supplier_claim_id
            or self.vuma_rebate_claim_id
        )

    def action_post(self):
        for move in self:
            if move.move_type in ('out_refund', 'in_refund'):
                if abs(move.amount_total or 0.0) > 5000 \
                        and move.vuma_credit_approval_state not in ('approved', 'not_required') \
                        and not move.vuma_credit_override_reason:
                    raise UserError(_(
                        'Credit note %(name)s for %(amount).2f needs approval before '
                        'it can be posted.', name=move.name, amount=move.amount_total))
                if not move._vuma_credit_source_linked():
                    raise UserError(_(
                        'Every credit note must link to an invoice, a delivery '
                        'return, a supplier claim or a rebate claim before it can '
                        'be posted.'))
        return super().action_post()

    def action_vuma_approve_credit(self):
        for move in self:
            if move.move_type not in ('out_refund', 'in_refund'):
                raise UserError(_('Only customer or supplier credit notes need this approval.'))
            required = move.vuma_credit_approval_level
            if required == 'owner':
                allowed = vuma_has_group(self.env, 'vuma_retail_ops.group_vuma_owner')
            else:
                allowed = vuma_has_group(
                    self.env, 'vuma_retail_ops.group_vuma_finance_manager',
                    'vuma_retail_ops.group_vuma_owner')
            if not allowed:
                raise AccessError(_('You are not allowed to approve this credit note.'))
            move.write({
                'vuma_credit_approval_state': 'approved',
                'vuma_credit_approved_by_id': self.env.user.id,
                'vuma_credit_approved_date': fields.Datetime.now(),
            })
            self.env['vuma.audit.log'].log(move, 'approve', note='Credit note approved')
        return True

    def action_vuma_reject_credit(self):
        for move in self:
            move.vuma_credit_approval_state = 'rejected'
            self.env['vuma.audit.log'].log(
                move, 'reject', note=move.vuma_credit_rejection_reason)
        return True
