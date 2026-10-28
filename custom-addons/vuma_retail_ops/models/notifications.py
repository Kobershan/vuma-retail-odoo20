# -*- coding: utf-8 -*-
"""In-app, email and SMS-ready notifications for Vuma approvals.

The database may not have an outgoing mail server or SMS gateway during
development.  The notification is therefore always written to the record's
chatter (live in-app notification) and an outgoing email is queued when the
recipients have an address.  Connecting the mail/SMS gateway at go-live makes
the same hooks deliver externally without changing the business workflow.
"""
import html

from odoo import _, api, models


class VumaNotification(models.AbstractModel):
    _name = 'vuma.notification'
    _description = 'Vuma Notification Dispatcher'

    @api.model
    def _group_users(self, group_xmlids):
        groups = self.env['res.groups']
        for xmlid in group_xmlids:
            group = self.env.ref(xmlid, raise_if_not_found=False)
            if group:
                groups |= group
        users = groups.all_user_ids.filtered(
            lambda user: user.active and user != self.env.user)
        return users

    @api.model
    def notify(self, records, group_xmlids, subject, body, force_email=False):
        """Post a live notification and queue the email fallback.

        Returns the number of recipients reached in the chatter.
        """
        users = self._group_users(group_xmlids)
        partners = users.mapped('partner_id').filtered(lambda p: p.active)
        emails = [email for email in users.mapped('email') if email]
        safe_body = html.escape(body or subject or '')
        count = 0
        for record in records:
            if not record:
                continue
            if hasattr(record, 'message_post'):
                record.message_post(
                    body=body,
                    subject=subject,
                    partner_ids=partners.ids,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
                count += len(partners)
            if emails:
                self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'body_html': '<p>%s</p>' % safe_body,
                    'email_to': ','.join(emails),
                    'model': record._name,
                    'res_id': record.id,
                })
        return count

    @api.model
    def notify_user(self, records, user, subject, body):
        if not user:
            return 0
        return self.notify(
            records, [], subject, body) if not user.partner_id else \
            self._notify_partners(records, user.partner_id, subject, body)

    @api.model
    def _notify_partners(self, records, partner, subject, body):
        safe_body = html.escape(body or subject or '')
        for record in records:
            if hasattr(record, 'message_post'):
                record.message_post(
                    body=body, subject=subject,
                    partner_ids=partner.ids,
                    message_type='notification',
                    subtype_xmlid='mail.mt_note',
                )
            if partner.email:
                self.env['mail.mail'].sudo().create({
                    'subject': subject,
                    'body_html': '<p>%s</p>' % safe_body,
                    'email_to': partner.email,
                    'model': record._name,
                    'res_id': record.id,
                })
        return 1


class CognifyRemittanceNotify(models.Model):
    _inherit = 'cognify.remittance'

    def action_submit(self):
        result = super().action_submit()
        self.env['vuma.notification'].notify(
            self,
            ['vuma_retail_ops.group_vuma_cashier',
             'vuma_retail_ops.group_vuma_finance_manager',
             'vuma_retail_ops.group_vuma_owner'],
            _('Remittance submitted for verification'),
            _('%(names)s was submitted by %(user)s and is waiting for the cash office.',
              names=', '.join(self.mapped('name')), user=self.env.user.name),
        )
        return result

    def action_verify(self):
        result = super().action_verify()
        self.env['vuma.notification'].notify(
            self,
            ['vuma_retail_ops.group_vuma_finance_manager',
             'vuma_retail_ops.group_vuma_owner'],
            _('Remittance verified'),
            _('%(names)s has been verified by %(user)s.',
              names=', '.join(self.mapped('name')), user=self.env.user.name),
        )
        return result

    def action_reject(self):
        result = super().action_reject()
        self.env['vuma.notification'].notify(
            self,
            ['vuma_retail_ops.group_vuma_ops_manager',
             'vuma_retail_ops.group_vuma_owner'],
            _('Remittance rejected'),
            _('%(names)s was rejected by %(user)s.',
              names=', '.join(self.mapped('name')), user=self.env.user.name),
        )
        return result
