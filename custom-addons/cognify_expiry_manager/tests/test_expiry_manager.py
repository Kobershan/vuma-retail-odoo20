"""End-to-end verification of cognify_expiry_manager.

Runs the full path the user described:
    1. calendar arithmetic behind the "alert N months ahead" window
    2. per-product alert windows in DIFFERENT units (1 day vs 3 months)
    3. receiving goods with a supplier expiry date on the purchase order
    4. precedence on the receipt: override > supplier date > shelf life
    5. FEFO: the lot closest to expiry is the one that gets picked
    6. the picking slip renders with the expiry columns
    7. the alert scan fires all four channels, exactly once per window
    8. changing a window re-arms the alerts

Dates are all derived from `fields.Date.today()` so the suite never rots.

Every test runs inside a savepoint that Odoo rolls back, so the database is
left exactly as it was found.
"""
import datetime
from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tests import TransactionCase, tagged
from odoo.tools import mute_logger
from odoo.tools.safe_eval import safe_eval

from ..expiry_period import alert_date_from_expiration, period_description, shift_months


@tagged('post_install', '-at_install')
class TestExpiryManager(TransactionCase):
    """Covers the goods-received -> in-stock -> expiring-stock workflow."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Expiry Test Supplier', 'email': 'supplier@example.com'})
        cls.manager = cls.env['res.users'].search(
            [('all_group_ids', 'in', [cls.env.ref('stock.group_stock_manager').id])], limit=1)
        assert cls.manager, "this database needs at least one stock manager to run these tests"
        # The stock manager on a fresh database is usually OdooBot, which has no
        # email address. Give it one so the email channel is genuinely exercised
        # rather than silently skipped for lack of a recipient.
        if not cls.manager.partner_id.email:
            cls.manager.partner_id.email = 'stock.manager@example.com'

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _days(self, n):
        """A date `n` days from today, as a plain Date."""
        return fields.Date.today() + timedelta(days=n)

    def _make_product(self, name, shelf_life_days, alert_value, alert_unit, responsible=None):
        """A storable, lot-tracked product with expiry dates switched on."""
        return self.env['product.template'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'use_expiration_date': True,
            'expiration_time': shelf_life_days,
            'use_time': 0,
            'removal_time': 0,
            'alert_time': 0,
            'fefo_expiration_date': True,
            'expiry_alert_value': alert_value,
            'expiry_alert_unit': alert_unit,
            'responsible_id': responsible.id if responsible else False,
            'categ_id': self.env.ref('product.product_category_goods').id,
            'list_price': 10.0,
            'standard_price': 5.0,
        })

    def _po(self, product, expiry_date=None, qty=10.0):
        """Create and confirm a purchase order for one product.

        ``product`` is a *template*. ``purchase.order.line.product_id`` points at
        a ``product.product``, so the variant id has to be resolved here: handing
        over the template id would silently "work" whenever the two id sequences
        happen to line up.
        """
        variant = product.product_variant_ids[0]
        self.assertEqual(variant.product_tmpl_id, product)
        po = self.env['purchase.order'].create({
            'partner_id': self.vendor.id,
            'company_id': self.company.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [(0, 0, {
                'product_id': variant.id,
                'product_qty': qty,
                'uom_id': variant.uom_id.id,
                'price_unit': 5.0,
                'date_planned': fields.Datetime.now(),
                'supplier_expiration_date': expiry_date or '',
            })],
        })
        po.button_confirm()
        return po, po.picking_ids

    def _receive(self, product, lot_name=None, expiry_date=None, qty=10.0, override=None):
        """Buy, receive and validate one lot the way the clerk would.

        ``lot_name`` may be omitted for a product that is not lot-tracked, in
        which case no lot/serial number is typed.
        """
        po, picking = self._po(product, expiry_date, qty)
        picking.action_assign()
        for line in picking.move_line_ids:
            if lot_name:
                line.lot_name = lot_name
            line.quantity = qty
            line.picked = True
            if override:
                line.cognify_expiration_override = override
        picking.with_context(skip_expired=True).button_validate()
        return po, picking

    def _lot(self, name):
        lot = self.env['stock.lot'].search([('name', '=', name)])
        self.assertTrue(lot, f"lot {name} should exist")
        return lot

    def _sell(self, product, qty):
        """Confirm a customer order so a delivery gets reserved.

        The order is deliberately an ordinary one. A user in the Mixed Basket
        group gets `vuma_is_mixed_basket` defaulted on every new quote, and a
        mixed quote is a shell that splits into the two trading companies on
        confirm -- which is not what this test is about, and which would refuse
        a plain order in this company. The flag is only touched when the module
        that defines it is installed.
        """
        partner = self.env['res.partner'].create({'name': 'Expiry Test Customer'})
        vals = {'partner_id': partner.id}
        if 'vuma_is_mixed_basket' in self.env['sale.order']._fields:
            vals['vuma_is_mixed_basket'] = False
        order = self.env['sale.order'].create(vals)
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': product.product_variant_ids[0].id,
            'product_uom_qty': qty,
            'price_unit': 10.0,
        })
        order.action_confirm()
        picking = order.picking_ids
        picking.action_assign()
        return picking

    # ------------------------------------------------------------------
    # 1. Calendar arithmetic
    # ------------------------------------------------------------------
    def test_calendar_arithmetic(self):
        """'3 months' must mean three calendar months, not 90 days."""
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 8, 15), 3, 'months'),
            datetime.datetime(2026, 5, 15))

        # Month-end clamping: 3 months before 31 Aug is 31 May, not 31 Feb.
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 8, 31), 3, 'months'),
            datetime.datetime(2026, 5, 31))

        # Crossing a year boundary in both directions.
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 2, 10), 3, 'months'),
            datetime.datetime(2025, 11, 10))
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 2, 10), 1, 'years'),
            datetime.datetime(2025, 2, 10))
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 2, 10), 18, 'months'),
            datetime.datetime(2024, 8, 10))

        # Weeks and days stay exact.
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 8, 15), 2, 'weeks'),
            datetime.datetime(2026, 8, 1))
        self.assertEqual(
            alert_date_from_expiration(datetime.datetime(2026, 8, 15), 1, 'days'),
            datetime.datetime(2026, 8, 14))

        # Shift helper directly.
        self.assertEqual(shift_months(datetime.datetime(2026, 3, 31), -3),
                         datetime.datetime(2025, 12, 31))
        self.assertEqual(shift_months(datetime.datetime(2026, 1, 15), -1),
                         datetime.datetime(2025, 12, 15))

        # Human readable summary shown on the product form.
        self.assertEqual(period_description(3, 'months'), '3 Months')
        self.assertEqual(period_description(1, 'days'), '1 Day')
        self.assertEqual(period_description(2, 'years'), '2 Years')
        self.assertEqual(period_description(0, 'days'), '')

        # No window means no alert at all.
        self.assertFalse(alert_date_from_expiration(datetime.datetime(2026, 8, 15), 0, 'months'))

    # ------------------------------------------------------------------
    # 2. Per-product alert windows in different units
    # ------------------------------------------------------------------
    def test_alert_windows_per_product_in_different_units(self):
        """The headline requirement: one product alerts a day ahead, another 3 months."""
        dairy = self._make_product('Expiry Test Dairy', 30, 1, 'days', self.manager)
        vaccine = self._make_product('Expiry Test Vaccine', 730, 3, 'months', self.manager)

        self.assertEqual(dairy.expiry_alert_window, '1 Day')
        self.assertEqual(vaccine.expiry_alert_window, '3 Months')

        expiry = datetime.datetime(2026, 8, 31, 12, 0, 0)
        dairy_lot = self.env['stock.lot'].create({
            'name': 'DAIRY-AUG', 'product_id': dairy.product_variant_ids[0].id,
            'expiration_date': expiry})
        vaccine_lot = self.env['stock.lot'].create({
            'name': 'VAC-AUG', 'product_id': vaccine.product_variant_ids[0].id,
            'expiration_date': expiry})

        # 1 day before vs 3 calendar months before. Three months before 31 Aug is
        # 31 May -- NOT the 2 June a flat 90-day subtraction would produce.
        self.assertEqual(dairy_lot.alert_date, datetime.datetime(2026, 8, 30, 12, 0, 0))
        self.assertEqual(vaccine_lot.alert_date, datetime.datetime(2026, 5, 31, 12, 0, 0))
        self.assertNotEqual(vaccine_lot.alert_date, expiry - timedelta(days=90))

        # And the lot form reports the window it is actually using.
        self.assertEqual(dairy_lot.expiry_alert_window, '1 Day')
        self.assertEqual(vaccine_lot.expiry_alert_window, '3 Months')

    def test_alert_window_falls_back_to_standard_alert_time(self):
        """Lots created before this module keep the plain day-count behaviour."""
        product = self._make_product('Expiry Test Legacy', 100, 0, 'days')
        product.alert_time = 10  # the standard product_expiry field, 10 days
        lot = self.env['stock.lot'].create({
            'name': 'LEGACY-1', 'product_id': product.product_variant_ids[0].id,
            'expiration_date': datetime.datetime(2026, 8, 15)})
        self.assertEqual(lot.alert_date, datetime.datetime(2026, 8, 5))
        self.assertEqual(lot.expiry_alert_window, '10 day(s)')

    def test_negative_alert_window_is_rejected(self):
        product = self._make_product('Expiry Test Negative', 30, 0, 'days')
        with self.assertRaises(ValidationError):
            product.expiry_alert_value = -5

    # ------------------------------------------------------------------
    # 3. Supplier expiry date on the PO line reaches the lot
    # ------------------------------------------------------------------
    def test_supplier_expiry_date_lands_on_the_lot(self):
        """The date typed on the purchase order is the date the lot ends up with."""
        product = self._make_product('Expiry Test Received', 365, 7, 'days', self.manager)
        supplier_date = self._days(200)

        po, _picking = self._receive(product, 'RCV-0001', expiry_date=supplier_date)

        self.assertEqual(po.order_line.supplier_expiration_date, supplier_date,
                         "the PO line must store the supplier's printed date")

        lot = self._lot('RCV-0001')
        self.assertEqual(fields.Date.to_date(lot.expiration_date), supplier_date,
                         "the supplier's date must beat the shelf-life calculation")

        # 7 days of warning applied to the received lot.
        self.assertEqual(fields.Date.to_date(lot.alert_date),
                         supplier_date - timedelta(days=7))

        # Stock really is on hand and really is expiry-tracked.
        self.assertGreater(lot.product_qty, 0)
        self.assertEqual(lot.expiry_status, 'ok')
        self.assertEqual(lot.expiry_days_left, 200)

        # The PO line also shows which window applies to that line.
        self.assertEqual(po.order_line.expiry_alert_window, '7 Days')

    def test_supplier_expiry_date_overrides_shelf_life(self):
        """Precedence on the receipt: override > supplier date > shelf life."""
        product = self._make_product('Expiry Test Precedence', 10, 1, 'days', self.manager)

        # (a) Nothing typed anywhere -> Odoo's shelf-life calculation.
        self._receive(product, 'PREC-A')
        lot_a = self._lot('PREC-A')
        self.assertAlmostEqual(
            (lot_a.expiration_date - fields.Datetime.now()).days, 10, delta=1,
            msg="with no supplier date the shelf life must decide")

        # (b) Supplier date typed -> it wins over the shelf life.
        self._receive(product, 'PREC-B', expiry_date=self._days(120))
        lot_b = self._lot('PREC-B')
        self.assertEqual(fields.Date.to_date(lot_b.expiration_date), self._days(120))

        # (c) Manual override on the operation -> it wins outright.
        override = datetime.datetime.combine(self._days(300), datetime.time.min)
        self._receive(product, 'PREC-C', expiry_date=self._days(120), override=override)
        lot_c = self._lot('PREC-C')
        self.assertEqual(lot_c.expiration_date, override,
                         "the manual override must beat the supplier date")

    def test_receiving_without_supplier_date_still_tracks_expiry(self):
        """A receipt with no supplier date must still produce a tracked lot."""
        product = self._make_product('Expiry Test No Date', 45, 2, 'weeks', self.manager)
        self._receive(product, 'NODATE-1')
        lot = self._lot('NODATE-1')
        self.assertTrue(lot.expiration_date, "shelf life must still produce a date")
        self.assertAlmostEqual((lot.expiration_date - lot.alert_date).days, 14, delta=1,
                               msg="2 weeks of warning must be applied")

    def test_purchase_line_rejects_a_past_supplier_date(self):
        """A mistyped year on the PO line must be caught before goods arrive."""
        product = self._make_product('Expiry Test Guard', 30, 1, 'days', self.manager)
        with self.assertRaises(ValidationError):
            self._po(product, expiry_date=self._days(-30))

        # A future date on the same product must still be accepted.
        po, _picking = self._po(product, expiry_date=self._days(30))
        self.assertTrue(po.order_line.supplier_expiration_date)

    # ------------------------------------------------------------------
    # 4. FEFO: closest to expiry goes out first
    # ------------------------------------------------------------------
    def test_fefo_picks_closest_expiry_first(self):
        """The core ask: goods closest to expiry must be picked first."""
        product = self._make_product('Expiry Test FEFO', 365, 5, 'days', self.manager)

        # Three batches with three different expiry dates, received out of order.
        self._receive(product, 'FEFO-LATE', expiry_date=self._days(300), qty=10)
        self._receive(product, 'FEFO-SOON', expiry_date=self._days(30), qty=10)
        self._receive(product, 'FEFO-MID', expiry_date=self._days(150), qty=10)

        # Switch the category to FEFO, exactly as the product's button does.
        product.action_apply_fefo()
        self.assertEqual(product.categ_id.removal_strategy_id.method, 'fefo')

        picking = self._sell(product, 10)
        reserved = picking.move_line_ids.mapped('lot_id')
        self.assertTrue(reserved, "FEFO must reserve a specific lot")
        self.assertEqual(
            reserved[:1].name, 'FEFO-SOON',
            "FEFO must take the batch with the closest expiry date, "
            "not the one that arrived first")

        # And that really is the closest lot still on hand.
        in_stock = self.env['stock.lot'].search([
            ('product_id', '=', product.product_variant_ids[0].id),
            ('product_qty', '>', 0)])
        closest = min(in_stock, key=lambda l: l.expiration_date)
        self.assertEqual(reserved[:1], closest)

    def test_fefo_sort_is_expiry_first(self):
        """Quants are ordered by removal/expiry date when FEFO is on."""
        order = self.env['stock.quant']._get_removal_strategy_order('fefo')
        self.assertIn('removal_date', order)
        self.assertIn('expiration_date', order)
        # Other strategies must be left exactly as stock defines them.
        self.assertEqual(self.env['stock.quant']._get_removal_strategy_order('fifo'),
                         'in_date ASC, id')
        self.assertEqual(self.env['stock.quant']._get_removal_strategy_order('lifo'),
                         'in_date DESC, id DESC')

    def test_apply_fefo_sets_removal_date_equal_to_expiry(self):
        """'Pick by closest expiry' must make removal date and expiry date agree."""
        product = self._make_product('Expiry Test FeFoRemov', 365, 5, 'days', self.manager)
        product.removal_time = 30  # a "pull it off the shelf" window
        product.action_apply_fefo()
        self.assertEqual(product.removal_time, 0)
        lot = self.env['stock.lot'].create({
            'name': 'FEFO-REM-1', 'product_id': product.product_variant_ids[0].id,
            'expiration_date': datetime.datetime(2026, 8, 31)})
        self.assertEqual(lot.removal_date, lot.expiration_date)

    # ------------------------------------------------------------------
    # 5. The picking slip shows the expiry date
    # ------------------------------------------------------------------
    def test_delivery_slip_shows_expiry_date(self):
        """The printed slip must carry the expiry date and the days left."""
        product = self._make_product('Expiry Test Slip', 365, 5, 'days', self.manager)
        self._receive(product, 'SLIP-1', expiry_date=self._days(30), qty=10)
        picking = self._sell(product, 3)
        picking.action_assign()

        # Both columns are gated behind a group, exactly as Odoo intends: the
        # slip only shows lot numbers and expiry dates when asked to.
        self.env.user.group_ids += self.env.ref('stock.group_lot_on_delivery_slip')
        self.env.user.group_ids += self.env.ref('product_expiry.group_expiry_date_on_delivery_slip')
        picking.with_context(skip_expired=True).button_validate()
        lot = self._lot('SLIP-1')

        html = self._render_slip(picking)

        self.assertIn('Expiration Date', html,
                      "product_expiry's Expiration Date column must be printed")
        self.assertIn('Days Left', html,
                      "this module adds a Days Left column next to it")
        self.assertIn('SLIP-1', html, "the lot must be named on the slip")
        self.assertIn('30', html, "the remaining shelf life must reach the slip")

        # And a slip with no expiry-tracked goods must be unchanged.
        plain = self.env['product.template'].create({
            'name': 'Expiry Test Slip Plain', 'type': 'consu', 'is_storable': True,
            'categ_id': self.env.ref('product.product_category_goods').id})
        self._receive(plain, qty=2)
        plain_picking = self._sell(plain, 1)
        plain_picking.action_assign()
        plain_picking.button_validate()
        plain_html = self._render_slip(plain_picking)
        self.assertNotIn('Days Left', plain_html,
                         "a slip of non-perishable goods must not grow an expiry column")

    def _render_slip(self, picking):
        """Render one validated picking's delivery slip to HTML.

        ``stock.report_delivery_document`` is the per-document template, so it
        expects the picking itself as ``o`` rather than a list to iterate. The
        report pipeline cannot be used here because it needs reportlab's PDF
        rasteriser, which this environment does not have.
        """
        return str(self.env['ir.qweb']._render(
            'stock.report_delivery_document',
            {'docs': picking, 'doc_ids': picking.ids, 'doc_model': 'stock.picking',
             'object': picking, 'o': picking, 'lang': 'en_US'}))

    # ------------------------------------------------------------------
    # 6. Alerts fire on every channel, exactly once
    # ------------------------------------------------------------------
    @mute_logger('odoo.addons.base.models.ir_mail_mail')
    def test_alert_scan_fires_all_channels_once(self):
        """Activity + in-app popup + email + dashboard, and no duplicates."""
        product = self._make_product('Expiry Test Alert', 200, 5, 'days', self.manager)
        # Expires in 30 days but the window is only 5 days, so it is not due yet.
        self._receive(product, 'ALERT-SOON', expiry_date=self._days(30), qty=5)
        lot = self._lot('ALERT-SOON')
        self.assertEqual(lot.expiry_status, 'ok')
        self.assertEqual(lot.expiry_days_left, 30)
        self.assertNotIn(lot, self.env['stock.lot']._notify_expiry_alerts(),
                         "a lot outside its window must not alert")

        # A lot with no stock at all must be ignored even when due.
        stale = self.env['stock.lot'].create({
            'name': 'ALERT-EMPTY', 'product_id': product.product_variant_ids[0].id,
            'expiration_date': fields.Datetime.now() - timedelta(days=1)})
        stale.product_expiry_reminded = False

        params = self.env['ir.config_parameter'].sudo()
        params.set_bool('cognify_expiry_manager.enable_email_notifications', True)
        params.set_str('cognify_expiry_manager.notify_user_ids', repr([self.manager.id]))

        # Move the stock into its window: 45 days of warning covers 30 days left.
        bus_before = len(self.env.cr.precommit.data.get('bus.bus.values', []))
        product.expiry_alert_value = 45
        lot.invalidate_recordset(['expiry_status', 'expiry_days_left'])
        self.assertEqual(lot.expiry_status, 'expiring', "must now read as Expiring Soon")

        alerted = self.env['stock.lot']._notify_expiry_alerts()
        self.assertIn(lot, alerted, "a lot inside its window must alert")
        self.assertNotIn(stale, alerted, "a lot with no stock on hand must not alert")

        # Channel 1 -- an activity on the lot's chatter.
        activity = lot.activity_ids.filtered(lambda a: a.summary.startswith('Expiry alert'))
        self.assertTrue(activity, "an activity must be scheduled on the lot")
        self.assertEqual(activity.user_id, self.manager,
                         "the activity belongs to the product's responsible user")
        self.assertIn('ALERT-SOON', activity.note)

        # Channel 2 -- the in-app popup. bus.bus rows are only written on
        # commit, so read the payload queued on this transaction. Only look at
        # what this scan added, since the queue is shared across the whole
        # cursor and not reset by the per-test savepoint.
        queued = self.env.cr.precommit.data.get('bus.bus.values', [])[bus_before:]
        payloads = [msg for _chan, kind, msg in queued
                    if kind == 'cognify_expiry_manager.expiry_alert']
        self.assertTrue(payloads, "a bus notification must be queued")
        self.assertIn('Expiry alert', payloads[0]['title'])
        self.assertEqual(payloads[0]['action']['res_model'], 'stock.lot',
                         "the popup's button must open the Expiry Alerts list")

        # Channel 3 -- the outgoing email. The mail template's subject names the
        # day count, which is what tells it apart from the activity's own mail.
        mail = self.env['mail.mail'].search([('subject', 'like', 'expires in')], limit=1)
        self.assertTrue(mail, "an email must be created for the lot")
        self.assertIn('ALERT-SOON', mail.subject)
        self.assertIn(self.manager.partner_id, mail.recipient_ids,
                      "the responsible user must be a recipient")

        # Channel 4 -- the dashboard.
        action = self.env.ref('cognify_expiry_manager.action_expiry_alerts')
        menu_domain = safe_eval(
            action.domain, self.env['ir.actions.actions']._get_eval_context())
        self.assertIn(lot, self.env['stock.lot'].search(Domain(menu_domain)),
                      "the expiring lot must appear on the Expiry Alerts menu")

        # ... and never twice for the same window.
        self.assertTrue(lot.product_expiry_reminded, "the lot must be flagged as reminded")
        self.assertNotIn(lot, self.env['stock.lot']._notify_expiry_alerts(),
                         "a lot must only alert once per window")

    @mute_logger('odoo.addons.base.models.ir_mail_mail')
    def test_alert_channels_can_be_switched_off_individually(self):
        """Each channel is governed by its own setting."""
        product = self._make_product('Expiry Test Channels', 30, 1, 'days', self.manager)
        self._receive(product, 'CHAN-1', expiry_date=self._days(1), qty=5)
        lot = self._lot('CHAN-1')

        params = self.env['ir.config_parameter'].sudo()
        params.set_bool('cognify_expiry_manager.enable_bus_notifications', False)
        params.set_bool('cognify_expiry_manager.enable_activity_notifications', False)
        params.set_bool('cognify_expiry_manager.enable_email_notifications', False)
        bus_before = len(self.env.cr.precommit.data.get('bus.bus.values', []))

        self.assertIn(lot, self.env['stock.lot']._notify_expiry_alerts())

        self.assertFalse(lot.activity_ids.filtered(lambda a: a.summary.startswith('Expiry alert')),
                         "the activity channel must respect its switch")
        queued = self.env.cr.precommit.data.get('bus.bus.values', [])[bus_before:]
        self.assertFalse([m for _c, kind, m in queued
                          if kind == 'cognify_expiry_manager.expiry_alert'],
                         "the popup channel must respect its switch")
        self.assertFalse(self.env['mail.mail'].search([('subject', 'like', 'expires in')]),
                         "the email channel must respect its switch")
        # Still counted, still on the dashboard.
        self.assertTrue(lot.product_expiry_reminded)
        menu_domain = safe_eval(
            self.env.ref('cognify_expiry_manager.action_expiry_alerts').domain,
            self.env['ir.actions.actions']._get_eval_context())
        self.assertIn(lot, self.env['stock.lot'].search(Domain(menu_domain)))

    def test_expired_lot_is_reported_and_flagged(self):
        """Already-expired stock shows as Expired, with a negative day count."""
        product = self._make_product('Expiry Test Expired', 30, 1, 'days', self.manager)
        # Receive legitimately, then let the goods age past their date in storage.
        self._receive(product, 'GONE-1', expiry_date=self._days(10), qty=3)
        lot = self._lot('GONE-1')
        lot.expiration_date = datetime.datetime.combine(self._days(-5), datetime.time.min)
        lot.invalidate_recordset(['expiry_status', 'expiry_days_left'])

        self.assertEqual(lot.expiry_status, 'expired')
        self.assertEqual(lot.expiry_days_left, -5)

        # The search filter must find it, and its negation must exclude it.
        self.assertIn(lot, self.env['stock.lot'].search([('expiry_status', '=', 'expired')]))
        self.assertNotIn(lot, self.env['stock.lot'].search([('expiry_status', '!=', 'expired')]))

        # A lot of a product that does not use expiry dates has no date at all,
        # which is its own searchable status.
        plain_product = self.env['product.template'].create({
            'name': 'Expiry Test No Expiry', 'type': 'consu', 'is_storable': True,
            'tracking': 'lot', 'use_expiration_date': False,
            'categ_id': self.env.ref('product.product_category_goods').id})
        plain = self.env['stock.lot'].create({
            'name': 'NOEXPIRY-1', 'product_id': plain_product.product_variant_ids[0].id})
        self.assertFalse(plain.expiration_date)
        self.assertEqual(plain.expiry_status, 'none')
        self.assertIn(plain, self.env['stock.lot'].search([('expiry_status', '=', 'none')]))
        self.assertNotIn(plain, self.env['stock.lot'].search([('expiry_status', '=', 'ok')]))
        self.assertIn(plain, self.env['stock.lot'].search([('expiry_status', '!=', 'expired')]))

    # ------------------------------------------------------------------
    # 7. Changing the window re-arms the alerts
    # ------------------------------------------------------------------
    def test_changing_the_window_rearms_the_alert(self):
        """Narrowing or widening a product's window must re-arm its lots."""
        product = self._make_product('Expiry Test Rearm', 60, 10, 'days', self.manager)
        self._receive(product, 'REARM-1', qty=5)
        lot = self._lot('REARM-1')

        # Wide window: out of range, so the scan leaves it alone.
        lot.product_expiry_reminded = False
        self.assertNotIn(lot, self.env['stock.lot']._notify_expiry_alerts(),
                         "a lot outside its window must not alert")

        # Widen it to 90 days, which puts a 60-day lot inside its window. The
        # write must clear the "already reminded" flag, otherwise the lot would
        # silently never alert anyone.
        product.expiry_alert_value = 90
        self.assertFalse(lot.product_expiry_reminded,
                         "changing the window must clear the reminded flag")
        self.assertIn(lot, self.env['stock.lot']._notify_expiry_alerts())

        # Widening it again after an alert must re-arm too, so a lot that later
        # moves back into range is picked up.
        lot.product_expiry_reminded = True
        product.expiry_alert_value = 30
        self.assertFalse(lot.product_expiry_reminded)

    # ------------------------------------------------------------------
    # 8. Views, report and settings are wired up
    # ------------------------------------------------------------------
    def test_views_and_actions_exist(self):
        for xmlid in [
            'cognify_expiry_manager.action_expiry_alerts',
            'cognify_expiry_manager.stock_lot_view_expiry_tree',
            'cognify_expiry_manager.stock_lot_view_expiry_search',
            'cognify_expiry_manager.action_report_expiry_stock',
            'cognify_expiry_manager.menu_expiry_alerts',
            'cognify_expiry_manager.ir_cron_expiry_alerts',
            'cognify_expiry_manager.mail_template_expiry_alert',
            'cognify_expiry_manager.group_expiry_user',
            'cognify_expiry_manager.group_expiry_manager',
        ]:
            self.assertTrue(self.env.ref(xmlid), f"{xmlid} should exist")

        report = self.env.ref('cognify_expiry_manager.action_report_expiry_stock')
        self.assertEqual(report.model, 'stock.lot')
        self.assertEqual(report.report_type, 'qweb-pdf')

        cron = self.env.ref('cognify_expiry_manager.ir_cron_expiry_alerts')
        self.assertIn('_notify_expiry_alerts', cron.code,
                      "the scheduled action must call the scan")

        # The expiry groups must hang off the stock privilege, and stock
        # administrators must hold the manager group.
        manager_group = self.env.ref('cognify_expiry_manager.group_expiry_manager')
        stock_manager = self.env.ref('stock.group_stock_manager')
        stock_user = self.env.ref('stock.group_stock_user')
        expiry_user = self.env.ref('cognify_expiry_manager.group_expiry_user')
        self.assertIn(manager_group, stock_manager.all_implied_ids,
                      "stock administrators must be able to manage expiry alerts")
        self.assertIn(stock_user, expiry_user.implied_ids,
                      "the expiry group must sit on top of the stock user group")
        self.assertIn(expiry_user, manager_group.implied_ids,
                      "the expiry manager group must sit on top of the expiry user group")

    def test_expiry_stock_report_renders(self):
        """The printable Expiring Stock report must produce real HTML."""
        product = self._make_product('Expiry Test Report', 30, 1, 'days', self.manager)
        self._receive(product, 'REPORT-1', expiry_date=self._days(2), qty=4)
        lots = self.env['stock.lot'].search(
            [('product_id', '=', product.product_variant_ids[0].id)])
        html = self.env['ir.qweb']._render(
            'cognify_expiry_manager.report_expiry_stock',
            {'doc_ids': lots.ids, 'doc_model': 'stock.lot',
             'docs': lots, 'lang': 'en_US'})
        self.assertIn('Expiring Stock', html)
        self.assertIn('REPORT-1', html)
        self.assertIn('Expires On', html)
        self.assertIn('Days Left', html)
        self.assertIn('1 Day', html)

    def test_settings_round_trip(self):
        """Saving the expiry settings and reading them back must agree."""
        self.env['res.config.settings'].create({
            'cognify_enable_email_notifications': False,
            'cognify_enable_bus_notifications': False,
            'cognify_enable_activity_notifications': True,
            'cognify_notify_user_ids': [(6, 0, self.manager.ids)],
        }).set_values()

        values = self.env['res.config.settings'].create({}).get_values()
        self.assertFalse(values['cognify_enable_email_notifications'])
        self.assertFalse(values['cognify_enable_bus_notifications'])
        self.assertTrue(values['cognify_enable_activity_notifications'])
        self.assertEqual(self._configured_users(values), self.manager,
                         "the recipient list must survive the round trip")

    @mute_logger('odoo.addons.base.models.ir_mail_mail')
    def test_settings_recipients_are_used_by_the_scan(self):
        """A recipient listed in the settings must be told, even with no responsible."""
        product = self._make_product('Expiry Test Nobody', 30, 1, 'days')
        self.assertFalse(product.responsible_id)
        self._receive(product, 'NOBODY-1', expiry_date=self._days(1), qty=2)
        lot = self._lot('NOBODY-1')

        self.env['ir.config_parameter'].sudo().set_str(
            'cognify_expiry_manager.notify_user_ids', repr([self.manager.id]))
        self.assertEqual(self.env['stock.lot']._expiry_alert_recipients(lot), self.manager)

        self.env['stock.lot']._notify_expiry_alerts()
        queued = self.env.cr.precommit.data.get('bus.bus.values', [])
        self.assertTrue([m for _c, kind, m in queued
                         if kind == 'cognify_expiry_manager.expiry_alert'],
                        "the settings-listed user must get the popup")

    def _configured_users(self, values):
        """Pull the user ids out of the x2many commands ``get_values`` returns.

        ``get_values`` hands a many2many back as a list of write commands whose
        payload is itself a list of ids, e.g. ``[(6, 0, [7])]``.
        """
        ids = []
        for command in values['cognify_notify_user_ids']:
            for part in command[2:]:
                if isinstance(part, (list, tuple)):
                    ids += [v for v in part if isinstance(v, int)]
                elif isinstance(part, int):
                    ids.append(part)
        return self.env['res.users'].browse(ids)