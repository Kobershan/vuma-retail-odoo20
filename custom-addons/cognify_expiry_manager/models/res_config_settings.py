# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .stock_lot import (
    PARAM_ACTIVITY_NOTIFICATIONS,
    PARAM_BUS_NOTIFICATIONS,
    PARAM_EMAIL_NOTIFICATIONS,
    PARAM_NOTIFY_USERS,
)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cognify_notify_user_ids = fields.Many2many(
        'res.users', string='Expiry Alert Recipients',
        help="These users are told, on top of each product's Responsible user, "
             "when stock enters its alert window.")
    cognify_enable_bus_notifications = fields.Boolean(
        string='In-App Popup', default=True,
        help="Raises a notification popup on the recipient's screen.")
    cognify_enable_activity_notifications = fields.Boolean(
        string='Activity on the Lot', default=True,
        help="Posts a todo activity in the lot's chatter.")
    cognify_enable_email_notifications = fields.Boolean(
        string='Email', default=True,
        help="Sends an email to the recipients. Requires an outgoing mail server.")

    @api.model
    def get_values(self):
        res = super().get_values()
        params = self.env['ir.config_parameter'].sudo()
        res.update({
            'cognify_notify_user_ids': [(6, 0, self._configured_notify_user_ids().ids)],
            'cognify_enable_bus_notifications':
                params.get_bool(PARAM_BUS_NOTIFICATIONS, True),
            'cognify_enable_activity_notifications':
                params.get_bool(PARAM_ACTIVITY_NOTIFICATIONS, True),
            'cognify_enable_email_notifications':
                params.get_bool(PARAM_EMAIL_NOTIFICATIONS, True),
        })
        return res

    def set_values(self):
        res = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        params.set_str(PARAM_NOTIFY_USERS, repr(self.cognify_notify_user_ids.ids))
        params.set_bool(PARAM_BUS_NOTIFICATIONS, self.cognify_enable_bus_notifications)
        params.set_bool(PARAM_ACTIVITY_NOTIFICATIONS, self.cognify_enable_activity_notifications)
        params.set_bool(PARAM_EMAIL_NOTIFICATIONS, self.cognify_enable_email_notifications)

        # A slip showing an empty expiry column reads as "these goods never
        # expire", so turning off the column also turns off lot printing.
        if not self.group_expiry_date_on_delivery_slip:
            self.group_lot_on_delivery_slip = False
        return res

    @api.model
    def _configured_notify_user_ids(self):
        """Read the recipient list back out of the stored config parameter."""
        users = self.env['res.users']
        raw = self.env['ir.config_parameter'].sudo().get_str(PARAM_NOTIFY_USERS)
        if not raw:
            return users
        # Stored as repr([1, 2]); fall back to a plain comma list.
        cleaned = raw.strip()
        if cleaned.startswith('[') and cleaned.endswith(']'):
            cleaned = cleaned[1:-1]
        ids = [int(part) for part in cleaned.split(',') if part.strip().isdigit()]
        return users.browse(ids).exists()