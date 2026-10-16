"""Walk the whole thing as a real user would, on a brand new product.

This is the acceptance check for the requirement: *any new product that expires
must be able to carry an expiry date.* Nothing here is created by the test suite
- it drives the same public methods the UI calls:

    1. create a storable product the way the Products form does
    2. switch on expiry tracking and set a 3-month alert window
    3. buy it, typing the supplier's printed expiry date on the PO line
    4. receive it and confirm the date lands on the lot
    5. sell it and confirm FEFO ships the batch closest to expiry
    6. print the slip and confirm the expiry date is on it
    7. run the alert scan and confirm it notifies exactly once

Everything runs in a savepoint that is rolled back.
"""
import datetime

from odoo import fields
from odoo.tests import TransactionCase, tagged

from ..expiry_period import shift_months


@tagged('post_install', '-at_install')
class TestNewProductExpiry(TransactionCase):
    """Acceptance test: a newly created perishable product, end to end."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.supplier = cls.env['res.partner'].create({
            'name': 'Fresh Dairy Co', 'email': 'orders@freshdairy.example'})
        cls.customer = cls.env['res.partner'].create({'name': 'Corner Shop'})
        cls.manager = cls.env['res.users'].search(
            [('all_group_ids', 'in', [cls.env.ref('stock.group_stock_manager').id])], limit=1)
        if not cls.manager.partner_id.email:
            cls.manager.partner_id.email = 'buyer@vuma.example'

    def test_new_perishable_product_gets_expiry_end_to_end(self):
        today = fields.Date.today()

        # 1. A new product, created the way the Products form creates it.
        product = self.env['product.template'].create({
            'name': 'Whole Milk 1L',
            'type': 'consu',
            'is_storable': True,
            'tracking': 'lot',
            'categ_id': self.env.ref('product.product_category_goods').id,
            'list_price': 3.50,
            'standard_price': 2.80,
        })
        variant = product.product_variant_ids[0]

        # 2. Switch on expiry tracking and give it a 3-month alert window.
        product.write({
            'use_expiration_date': True,
            'expiration_time': 14,        # 14-day shelf life
            'use_time': 3,
            'removal_time': 1,
            'alert_time': 0,
            'fefo_expiration_date': True,
            'expiry_alert_value': 3,
            'expiry_alert_unit': 'months',
            'responsible_id': self.manager.id,
        })
        self.assertTrue(product.use_expiration_date)
        self.assertEqual(product.expiry_alert_window, '3 Months')

        # 3. Buy it, typing the date printed on the supplier's label.
        printed = today + datetime.timedelta(days=12)
        po = self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'company_id': self.company.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [(0, 0, {
                'product_id': variant.id,
                'product_qty': 24.0,
                'uom_id': variant.uom_id.id,
                'price_unit': 2.80,
                'date_planned': fields.Datetime.now(),
                'supplier_expiration_date': printed,
            })],
        })
        self.assertEqual(po.order_line.supplier_expiration_date, printed)
        po.button_confirm()

        # 4. Receive it. The clerk types the lot number and validates.
        receipt = po.picking_ids
        receipt.action_assign()
        for line in receipt.move_line_ids:
            line.lot_name = 'MILK-%s' % printed.strftime('%Y%m%d')
            line.quantity = 24.0
            line.picked = True
        receipt.with_context(skip_expired=True).button_validate()

        lot = self.env['stock.lot'].search([('name', '=', 'MILK-%s' % printed.strftime('%Y%m%d'))])
        self.assertTrue(lot, "receiving must create the lot")
        self.assertEqual(fields.Date.to_date(lot.expiration_date), printed,
                         "the lot must carry the date from the supplier's label")
        self.assertEqual(lot.product_qty, 24.0, "stock must be on hand")
        self.assertEqual(lot.expiry_days_left, 12)

        # The 3-month window puts the alert date 3 calendar months BEFORE the
        # 12-day expiry, i.e. already in the past. So the batch reads as
        # "Expiring Soon" the moment it lands. That is correct rather than a
        # bug: if you want warning 3 months ahead, you want product with a
        # shelf life longer than 3 months. A product that only lasts two weeks
        # should be given a window of days, not months.
        self.assertEqual(fields.Date.to_date(lot.alert_date),
                         shift_months(fields.Datetime.to_datetime(printed), -3).date(),
                         "the alert date must be exactly 3 calendar months before expiry")
        self.assertNotEqual(fields.Date.to_date(lot.alert_date),
                            printed - datetime.timedelta(days=90),
                            "a 3-month window is not 90 days")
        self.assertEqual(lot.expiry_status, 'expiring')

        # 5. Pick by closest expiry. Receive a second, fresher batch.
        second_printed = today + datetime.timedelta(days=40)
        po2 = self.env['purchase.order'].create({
            'partner_id': self.supplier.id,
            'company_id': self.company.id,
            'picking_type_id': self.warehouse.in_type_id.id,
            'order_line': [(0, 0, {
                'product_id': variant.id,
                'product_qty': 24.0,
                'uom_id': variant.uom_id.id,
                'price_unit': 2.80,
                'date_planned': fields.Datetime.now(),
                'supplier_expiration_date': second_printed,
            })],
        })
        po2.button_confirm()
        receipt2 = po2.picking_ids
        receipt2.action_assign()
        for line in receipt2.move_line_ids:
            line.lot_name = 'MILK-%s' % second_printed.strftime('%Y%m%d')
            line.quantity = 24.0
            line.picked = True
        receipt2.with_context(skip_expired=True).button_validate()

        # Turn the category to FEFO via the product's own button.
        product.action_apply_fefo()
        self.assertEqual(product.categ_id.removal_strategy_id.method, 'fefo')

        # Sell six. FEFO must take the batch that expires soonest (day 12).
        # An ordinary order, not a mixed-basket shell: see the note in
        # test_expiry_manager._sell.
        order_vals = {'partner_id': self.customer.id}
        if 'vuma_is_mixed_basket' in self.env['sale.order']._fields:
            order_vals['vuma_is_mixed_basket'] = False
        order = self.env['sale.order'].create(order_vals)
        self.env['sale.order.line'].create({
            'order_id': order.id,
            'product_id': variant.id,
            'product_uom_qty': 6.0,
            'price_unit': 3.50,
        })
        order.action_confirm()
        delivery = order.picking_ids
        delivery.action_assign()
        shipped = delivery.move_line_ids.mapped('lot_id')
        self.assertTrue(shipped, "FEFO must reserve a lot")
        self.assertEqual(shipped[:1].expiration_date.date(), printed,
                         "the batch closest to expiry must be picked first")

        # 6. The slip carries the expiry date.
        self.env.user.group_ids += self.env.ref('stock.group_lot_on_delivery_slip')
        self.env.user.group_ids += self.env.ref('product_expiry.group_expiry_date_on_delivery_slip')
        delivery.with_context(skip_expired=True).button_validate()
        html = str(self.env['ir.qweb']._render(
            'stock.report_delivery_document',
            {'docs': delivery, 'doc_ids': delivery.ids, 'doc_model': 'stock.picking',
             'object': delivery, 'o': delivery, 'lang': 'en_US'}))
        self.assertIn('Expiration Date', html)
        self.assertIn('Days Left', html)
        self.assertIn('MILK-%s' % printed.strftime('%Y%m%d'), html)

        # 7. Alerts fire once for the batch already inside its window.
        alerted = self.env['stock.lot']._notify_expiry_alerts()
        self.assertIn(lot, alerted, "a batch inside its 3-month window must alert")
        self.assertTrue(lot.product_expiry_reminded)
        self.assertNotIn(lot, self.env['stock.lot']._notify_expiry_alerts(),
                         "and must not alert twice")