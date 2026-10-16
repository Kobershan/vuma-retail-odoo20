"""Live smoke test on the real database, through the same methods the UI uses.

Everything is rolled back at the end, so nothing is left behind.
"""
import datetime

from odoo import fields

# Any leftover from an interrupted previous run.
for _t in env['product.template'].search([('name', '=', 'SMOKE Whole Milk 1L')]):
    _t.product_variant_ids[:]._unlink_direct() if False else None

print('\n================ 1. NEW PRODUCT ================')
product = env['product.template'].create({
    'name': 'SMOKE Whole Milk 1L',
    'type': 'consu',
    'is_storable': True,
    'tracking': 'lot',
    'categ_id': env.ref('product.product_category_goods').id,
    'list_price': 3.50,
    'standard_price': 2.80,
})
variant = product.product_variant_ids[0]
print('  created       :', product.display_name)
print('  storable      :', product.is_storable)
print('  tracking      :', product.tracking)

print('\n================ 2. TURN ON EXPIRY ================')
product.write({
    'use_expiration_date': True,
    'expiration_time': 14,
    'use_time': 3,
    'removal_time': 1,
    'fefo_expiration_date': True,
    'expiry_alert_value': 3,
    'expiry_alert_unit': 'months',
})
print('  use_expiration_date :', product.use_expiration_date)
print('  shelf life          :', product.expiration_time, 'days')
print('  alert window        :', product.expiry_alert_window)

company = env.ref('base.main_company')
warehouse = env['stock.warehouse'].search([('company_id', '=', company.id)], limit=1)
manager = env['res.users'].search(
    [('all_group_ids', 'in', [env.ref('stock.group_stock_manager').id])], limit=1)
product.responsible_id = manager
supplier = env['res.partner'].create({
    'name': 'SMOKE Fresh Dairy Co', 'email': 'smoke@example.com'})

print('\n================ 3. SUPPLIER EXPIRY ON PO ================')
printed = fields.Date.today() + datetime.timedelta(days=12)
po = env['purchase.order'].create({
    'partner_id': supplier.id,
    'company_id': company.id,
    'picking_type_id': warehouse.in_type_id.id,
    'order_line': [(0, 0, {
        'product_id': variant.id,
        'product_qty': 24.0,
        'uom_id': variant.uom_id.id,
        'price_unit': 2.80,
        'date_planned': fields.Datetime.now(),
        'supplier_expiration_date': printed,
    })],
})
print('  PO             :', po.name)
print('  supplier expiry:', po.order_line.supplier_expiration_date)
po.button_confirm()

print('\n================ 4. RECEIVE ================')
receipt = po.picking_ids
receipt.action_assign()
lot_name = 'SMOKE-%s' % printed.strftime('%Y%m%d')
for line in receipt.move_line_ids:
    line.lot_name = lot_name
    line.quantity = 24.0
    line.picked = True
receipt.with_context(skip_expired=True).button_validate()
lot = env['stock.lot'].search([('name', '=', lot_name)])
print('  lot            :', lot.name)
print('  expiration     :', lot.expiration_date)
print('  matches label  :', fields.Date.to_date(lot.expiration_date) == printed)
print('  on hand        :', lot.product_qty)
print('  alert date     :', lot.alert_date)
print('  days left      :', lot.expiry_days_left)
print('  status         :', lot.expiry_status)

print('\n================ 5. FEFO PICKS CLOSEST ================')
later = fields.Date.today() + datetime.timedelta(days=40)
po2 = env['purchase.order'].create({
    'partner_id': supplier.id,
    'company_id': company.id,
    'picking_type_id': warehouse.in_type_id.id,
    'order_line': [(0, 0, {
        'product_id': variant.id, 'product_qty': 24.0,
        'uom_id': variant.uom_id.id, 'price_unit': 2.80,
        'date_planned': fields.Datetime.now(),
        'supplier_expiration_date': later,
    })],
})
po2.button_confirm()
r2 = po2.picking_ids
r2.action_assign()
for line in r2.move_line_ids:
    line.lot_name = 'SMOKE-%s' % later.strftime('%Y%m%d')
    line.quantity = 24.0
    line.picked = True
r2.with_context(skip_expired=True).button_validate()

product.action_apply_fefo()
print('  removal strategy:', product.categ_id.removal_strategy_id.name)

customer = env['res.partner'].create({'name': 'SMOKE Corner Shop'})
order = env['sale.order'].create({'partner_id': customer.id})
env['sale.order.line'].create({
    'order_id': order.id, 'product_id': variant.id,
    'product_uom_qty': 6.0, 'price_unit': 3.50})
order.action_confirm()
delivery = order.picking_ids
delivery.action_assign()
picked = delivery.move_line_ids.mapped('lot_id')
print('  delivery picked:', picked[:1].name)
print('  picked expiry  :', picked[:1].expiration_date)
print('  is closest     :', fields.Date.to_date(picked[:1].expiration_date) == printed)

print('\n================ 6. DELIVERY SLIP ================')
env.user.group_ids += env.ref('stock.group_lot_on_delivery_slip')
env.user.group_ids += env.ref('product_expiry.group_expiry_date_on_delivery_slip')
delivery.with_context(skip_expired=True).button_validate()
html = str(env['ir.qweb']._render(
    'stock.report_delivery_document',
    {'docs': delivery, 'doc_ids': delivery.ids, 'doc_model': 'stock.picking',
     'object': delivery, 'o': delivery, 'lang': 'en_US'}))
for marker in ('Expiration Date', 'Days Left', lot_name):
    print('  slip contains %-18s: %s' % ("'%s'" % marker, marker in html))

print('\n================ 7. ALERT SCAN ================')
env['ir.config_parameter'].sudo().set_bool(
    'cognify_expiry_manager.enable_email_notifications', False)
alerted = env['stock.lot']._notify_expiry_alerts()
print('  alerted lots   :', alerted.mapped('name'))
print('  this lot in it :', lot in alerted)
print('  second scan    :', env['stock.lot']._notify_expiry_alerts(),
      '(empty = no repeat)')
print('  activity       :', lot.activity_ids.mapped('summary'))

print('\n================ 8. EXPIRY ALERTS MENU ================')
action = env.ref('cognify_expiry_manager.action_expiry_alerts')
from odoo.tools.safe_eval import safe_eval
ctx = env['ir.actions.actions']._get_eval_context()
on_menu = env['stock.lot'].search(safe_eval(action.domain, ctx))
print('  rows on menu   :', len(on_menu), on_menu.mapped('name'))

env.cr.rollback()
print('\n[rolled back - nothing committed]')