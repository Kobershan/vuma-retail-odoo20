# -*- coding: utf-8 -*-
import logging

from odoo import _, api, fields, models
from odoo.tools.misc import format_date
from odoo.tools.safe_eval import safe_eval

_logger = logging.getLogger(__name__)

# Bus notification type listened to by static/src/js/expiry_alert_plugin.js
BUS_NOTIFICATION_TYPE = 'cognify_expiry_manager.expiry_alert'

PARAM_NOTIFY_USERS = 'cognify_expiry_manager.notify_user_ids'
PARAM_BUS_NOTIFICATIONS = 'cognify_expiry_manager.enable_bus_notifications'
PARAM_ACTIVITY_NOTIFICATIONS = 'cognify_expiry_manager.enable_activity_notifications'
PARAM_EMAIL_NOTIFICATIONS = 'cognify_expiry_manager.enable_email_notifications'


class StockLot(models.Model):
    _inherit = 'stock.lot'

    expiry_days_left = fields.Integer(
        string='Days Left', compute='_compute_expiry_status',
        help="Whole days until the expiry date. Negative once the goods are expired.")
    expiry_status = fields.Selection(
        [('none', 'No expiry date'), ('ok', 'OK'), ('expiring', 'Expiring Soon'), ('expired', 'Expired')],
        string='Expiry Status', compute='_compute_expiry_status', default='none',
        search='_search_expiry_status')
    expiry_alert_window = fields.Char(
        string='Alert Window', compute='_compute_expiry_alert_window',
        help="The alert lead time configured on the product, e.g. '3 Months'.")
    responsible_id = fields.Many2one(
        'res.users', string='Responsible', related='product_id.responsible_id',
        store=False, readonly=True,
        help="Who gets told about this lot. Comes from the product.")

    @api.depends('expiration_date', 'alert_date')
    def _compute_expiry_status(self):
        today = fields.Date.context_today(self)
        for lot in self:
            if not lot.expiration_date:
                lot.expiry_status = 'none'
                lot.expiry_days_left = 0
                continue
            lot.expiry_days_left = (lot.expiration_date.date() - today).days
            if lot.expiry_days_left < 0:
                lot.expiry_status = 'expired'
            elif lot.alert_date and lot.alert_date.date() <= today:
                lot.expiry_status = 'expiring'
            else:
                lot.expiry_status = 'ok'

    @api.depends('product_id.expiry_alert_value', 'product_id.expiry_alert_unit',
                 'product_id.alert_time')
    def _compute_expiry_alert_window(self):
        for lot in self:
            template = lot.product_id.product_tmpl_id
            if template.expiry_alert_window:
                lot.expiry_alert_window = template.expiry_alert_window
            elif template.alert_time:
                lot.expiry_alert_window = _('%d day(s)', template.alert_time)
            else:
                lot.expiry_alert_window = ''

    def _search_expiry_status(self, operator, value):
        """Make the computed status searchable.

        ``expiry_status`` cannot be stored because it depends on what day it is
        today, and a stored copy would silently go stale every midnight. This
        rewrites the status filter into an equivalent domain over the stored
        ``expiration_date`` and ``alert_date`` columns so the Expiry Alerts menu,
        its filters and its group-by all work.

        :param str operator: the comparison operator
        :param value: the status the operator is applied to
        :return: a domain, or ``NotImplemented`` for an unknown operator
        """
        if operator not in ('=', '!='):
            return NotImplemented

        today = fields.Date.today()
        no_date = [('expiration_date', '=', False)]
        dated = [
            ('expiration_date', '!=', False),
            ('expiration_date', '>', today),
            '|', ('alert_date', '=', False), ('alert_date', '>', today),
        ]
        expiring = [
            ('expiration_date', '!=', False),
            ('expiration_date', '>', today),
            ('alert_date', '<=', today),
        ]
        expired = [
            ('expiration_date', '!=', False),
            ('expiration_date', '<=', today),
        ]
        domains = {
            'none': no_date,
            'ok': dated,
            'expiring': expiring,
            'expired': expired,
        }
        if value not in domains:
            return [('id', 'in', [])]

        wanted = ['none', 'ok', 'expiring', 'expired']
        if operator == '=':
            selected = domains[value]
        else:
            # '!=' against a single value means "every status but this one",
            # which has to be expressed as the union of the other three.
            others = [key for key in wanted if key != value]
            selected = ['|'] * (len(others) - 1)
            for key in others:
                selected += domains[key]
        return selected

    # ------------------------------------------------------------------
    # Alert date: calendar-aware instead of a flat day count
    # ------------------------------------------------------------------
    @api.depends('product_id', 'product_id.expiry_alert_value',
                 'product_id.expiry_alert_unit', 'product_id.alert_time',
                 'expiration_date')
    def _compute_dates(self):
        """Let ``product_expiry`` compute the dates, then fix up the alert date.

        Best-before and removal dates are shelf-life properties and stay on
        Odoo's flat day counts. ``alert_date`` is the one the user wants to
        express as "3 months", so it is recomputed here through the product's
        value + unit pair, which uses real calendar arithmetic.
        """
        super()._compute_dates()
        for lot in self:
            if not lot.product_id.use_expiration_date or not lot.expiration_date:
                continue
            lot.alert_date = lot.product_id.product_tmpl_id._expiry_alert_date(lot.expiration_date)

    def _reset_expiry_alert_flags(self, products):
        """Clear "already alerted" on lots of ``products`` that still hold stock.

        Called when a product's alert window changes, so lots that just came
        inside the window are told about again, and lots that dropped outside it
        can be told later if the window widens again.
        """
        if not products:
            return
        lots = self.search([
            ('product_id', 'in', products.ids),
            ('expiration_date', '!=', False),
        ])
        if not lots:
            return
        quants = self.env['stock.quant'].search([
            ('lot_id', 'in', lots.ids),
            ('quantity', '>', 0),
            ('location_id.usage', '=', 'internal'),
        ])
        quants.mapped('lot_id').write({'product_expiry_reminded': False})

    # ------------------------------------------------------------------
    # Alerting
    # ------------------------------------------------------------------
    @api.model
    def _expiry_alert_recipients(self, lots):
        """Return the users who should hear about the given lots.

        The product's responsible user comes first, so a specific product can
        be routed to one person, followed by anyone listed in the settings.
        """
        if not lots:
            return self.env['res.users'].browse()
        users = lots.product_id.mapped('responsible_id')
        # Read through ir.config_parameter rather than odoo.tools.config: config
        # is a process-wide snapshot taken at boot, so a value saved a moment ago
        # in this same transaction would be invisible to it.
        configured = self.env['ir.config_parameter'].sudo().get_str(PARAM_NOTIFY_USERS)
        if configured:
            try:
                users |= self.env['res.users'].browse(safe_eval(configured))
            except Exception:  # pragma: no cover - defensive, bad config value
                _logger.warning("%s contains an invalid user id list: %r",
                                PARAM_NOTIFY_USERS, configured)
        return users

    @api.model
    def _notify_expiry_alerts(self):
        """Alert on every lot that has entered its alert window.

        Driven by this module's scheduled action. Each lot is only alerted once
        per window, so running the job hourly is safe.

        :return: the lots that were alerted
        :rtype: stock.lot
        """
        lots = self.search([
            ('expiration_date', '!=', False),
            ('alert_date', '!=', False),
            ('alert_date', '<=', fields.Datetime.now()),
            ('product_expiry_reminded', '=', False),
        ])
        if not lots:
            return lots

        # Only lots still physically sitting in an internal location are worth
        # an alert: anything already delivered or scrapped is not a problem.
        quants = self.env['stock.quant'].search([
            ('lot_id', 'in', lots.ids),
            ('quantity', '>', 0),
            ('location_id.usage', '=', 'internal'),
        ])
        lots = quants.mapped('lot_id')
        if not lots:
            return lots

        recipients = self._expiry_alert_recipients(lots)
        for lot in lots:
            self._notify_single_expiry_alert(lot, recipients)

        lots.write({'product_expiry_reminded': True})
        return lots

    def _notify_single_expiry_alert(self, lot, recipients):
        """Fire each notification channel that the settings have enabled."""
        params = self.env['ir.config_parameter'].sudo()
        if params.get_bool(PARAM_ACTIVITY_NOTIFICATIONS, True):
            self._notify_expiry_activity(lot, recipients)
        if params.get_bool(PARAM_BUS_NOTIFICATIONS, True):
            self._notify_expiry_bus(lot, recipients)
        if params.get_bool(PARAM_EMAIL_NOTIFICATIONS, True):
            self._notify_expiry_email(lot, recipients)

    def _notify_expiry_activity(self, lot, recipients):
        """Post a todo activity on the lot's chatter."""
        lot.activity_schedule(
            'mail.mail_activity_data_todo',
            user_id=recipients[:1].id or 1,
            summary=_("Expiry alert: %s", lot.product_id.display_name),
            note=self._expiry_alert_note(lot),
        )

    def _notify_expiry_bus(self, lot, recipients):
        """Push an in-app notification popup to the recipients."""
        if not recipients:
            return
        payload = {
            'title': _("Expiry alert: %s", lot.product_id.display_name),
            'body': self._expiry_alert_note(lot),
            'type': 'warning',
            'sticky': True,
            'action': self._expiry_alert_action(),
        }
        for user in recipients:
            self.env['bus.bus']._sendone(user.partner_id, BUS_NOTIFICATION_TYPE, payload)

    def _notify_expiry_email(self, lot, recipients):
        """Email the recipients that have an address on file."""
        partners = recipients.mapped('partner_id').filtered('email')
        if not partners:
            return
        template = self.env.ref('cognify_expiry_manager.mail_template_expiry_alert')
        lot.message_post_with_source(
            template,
            force_send=True,
            email_layout_xmlid='mail.mail_notification_light',
            partner_ids=partners.ids,
        )

    def _expiry_alert_note(self, lot):
        """One-line explanation of how urgent this lot is."""
        days_left = lot.expiry_days_left
        if days_left < 0:
            headline = _("expired %d day(s) ago") % abs(days_left)
        elif days_left == 0:
            headline = _("expires today")
        else:
            headline = _("expires in %d day(s)") % days_left
        return _(
            "%(qty)s of %(product)s (lot %(lot)s) %(headline)s, on %(date)s.",
            qty=lot.product_qty,
            product=lot.product_id.display_name,
            lot=lot.name,
            headline=headline,
            date=format_date(self.env, lot.expiration_date),
        )

    # ------------------------------------------------------------------
    # Helpers for the views
    # ------------------------------------------------------------------
    @api.model
    def _expiry_alert_action(self):
        """Client action opened by the notification button and the menu."""
        return {
            'name': _("Expiry Alerts"),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.lot',
            'views': [[self.env.ref('cognify_expiry_manager.stock_lot_view_expiry_tree').id, 'list']],
            'domain': self._expiring_lot_domain(),
            'context': {'create': False},
        }

    @api.model
    def _expiring_lot_domain(self, extra_domain=None):
        """Domain for "stock that is in or past its alert window".

        :param list extra_domain: further clauses, ANDed on top
        :return: list
        """
        now = fields.Datetime.now()
        domain = [
            ('product_qty', '>', 0),
            ('expiration_date', '!=', False),
            '|', ('alert_date', '<=', now), ('expiration_date', '<=', now),
        ]
        return domain + (extra_domain or [])

    def action_scan_expiry_alerts(self):
        """Re-run the scan now, from the Expiry Alerts menu."""
        lots = self._notify_expiry_alerts()
        return {
            'name': _('Expiry Alerts'),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.lot',
            'views': [[self.env.ref('cognify_expiry_manager.stock_lot_view_expiry_tree').id, 'list']],
            'domain': [('id', 'in', lots.ids)],
            'context': {'create': False},
        }