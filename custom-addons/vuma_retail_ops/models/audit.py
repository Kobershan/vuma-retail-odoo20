# -*- coding: utf-8 -*-
"""Small audit trail shared by the operations models.

The point is not to duplicate Odoo's chatter. It is to keep a queryable record
of the decisions that change money or control: approvals, credit overrides,
agreement changes, cash variances and deletions.
"""
from odoo import api, fields, models


class VumaAuditLog(models.Model):
    _name = 'vuma.audit.log'
    _description = 'Vuma Audit Log'
    _order = 'date desc, id desc'
    _rec_names_search = ['model', 'res_name', 'note']

    date = fields.Datetime(
        string='When', default=fields.Datetime.now, required=True, index=True)
    user_id = fields.Many2one(
        'res.users', string='User', default=lambda self: self.env.user,
        required=True, index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', default=lambda self: self.env.company,
        index=True)
    model = fields.Char(string='Model', required=True, index=True)
    res_id = fields.Integer(string='Record ID', required=True, index=True)
    res_name = fields.Char(string='Record')
    action = fields.Selection(
        [
            ('create', 'Created'),
            ('write', 'Changed'),
            ('delete', 'Deleted'),
            ('approve', 'Approved'),
            ('reject', 'Rejected'),
            ('post', 'Posted'),
            ('override', 'Override'),
            ('login', 'Login'),
        ],
        string='Action', required=True, index=True)
    field_name = fields.Char(string='Field')
    old_value = fields.Text(string='Old Value')
    new_value = fields.Text(string='New Value')
    note = fields.Text(string='Note')

    @api.model
    def log(self, record, action, field_name=None, old_value=None,
            new_value=None, note=None):
        """Write one audit row for ``record``."""
        if not record:
            return self
        try:
            name = record.display_name
        except Exception:
            name = False
        return self.sudo().create({
            'model': record._name,
            'res_id': record.id,
            'res_name': name,
            'action': action,
            'field_name': field_name,
            'old_value': False if old_value is None else str(old_value),
            'new_value': False if new_value is None else str(new_value),
            'note': note,
            'company_id': getattr(record, 'company_id', False) and record.company_id.id
                          or self.env.company.id,
        })


class VumaAuditMixin(models.AbstractModel):
    _name = 'vuma.audit.mixin'
    _description = 'Vuma Audit Mixin'

    _vuma_audit_fields = ()

    def write(self, vals):
        before = {}
        fields_to_log = [f for f in self._vuma_audit_fields if f in vals]
        if fields_to_log:
            for rec in self:
                before[rec.id] = {f: rec[f] for f in fields_to_log}
        result = super().write(vals)
        for rec in self:
            for field_name in fields_to_log:
                old = before.get(rec.id, {}).get(field_name)
                new = rec[field_name]
                if old != new:
                    self.env['vuma.audit.log'].log(
                        rec, 'write', field_name=field_name,
                        old_value=old, new_value=new)
        return result

    def unlink(self):
        for rec in self:
            self.env['vuma.audit.log'].log(rec, 'delete')
        return super().unlink()
