"""Prove the three documents really print the product picture.

Builds a picking, a purchase order and a stock take for products carrying a
PNG and a WebP picture, renders all three through the real PDF engine, and
writes the files to /tmp/opencode/pictures/ so they can be looked at. Every
row of work is rolled back at the end.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_product_pictures.py
"""
import base64
import io
import os

from PIL import Image, ImageDraw

env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


OUT_DIR = '/tmp/opencode/pictures'
os.makedirs(OUT_DIR, exist_ok=True)


def picture(fmt, size, label):
    """A recognisable test picture: a colour band with its format written on it."""
    im = Image.new('RGB', size, (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, size[0], size[1] // 2], fill=(28, 86, 168))
    draw.text((10, size[1] // 4), label, fill=(255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format=fmt)
    return buf.getvalue()


try:
    from PIL import WebPImagePlugin  # noqa: F401
    WEBP = True
except ImportError:
    WEBP = False

png_bytes = picture('PNG', (400, 400), 'PNG')
webp_bytes = picture('WEBP', (900, 600), 'WEBP') if WEBP else None

products = {}
for name, payload in (('Blue Widget PNG', png_bytes),
                      ('Orange Widget PNG', png_bytes),
                      ('Green Widget WEBP', webp_bytes)):
    if payload is None:
        continue
    product = env['product.product'].create({
        'name': name, 'type': 'consu', 'is_storable': True,
        'list_price': 10.0, 'standard_price': 6.0,
    })
    product.image_1920 = base64.b64encode(payload).decode()
    products[name] = product

# Also one product with no picture at all: it must print as it always did.
plain = env['product.product'].create({
    'name': 'Plain Product (no picture)', 'type': 'consu', 'is_storable': True,
    'list_price': 4.0, 'standard_price': 2.0,
})
products['plain'] = plain

vendor = env['res.partner'].create({'name': 'Picture Test Vendor', 'is_company': True})

# --------------------------------------------------------------------------
# 1. Picking Operations
# --------------------------------------------------------------------------
stock = env.ref('stock.stock_location_stock')
customers = env.ref('stock.stock_location_customers')
for product in products.values():
    env['stock.quant']._update_available_quantity(product, stock, 25)

picking = env['stock.picking'].create({
    'picking_type_id': env.ref('stock.picking_type_out').id,
    'location_id': stock.id,
    'location_dest_id': customers.id,
    'move_ids': [(0, 0, {
        'product_id': product.id,
        'product_uom_qty': qty,
        'location_id': stock.id,
        'location_dest_id': customers.id,
    }) for product, qty in zip(products.values(), (4, 2, 6, 3))],
})
picking.action_confirm()
picking.action_assign()
check('picking is assigned with move lines', bool(picking.move_line_ids),
      f'state={picking.state} lines={len(picking.move_line_ids)}')

# --------------------------------------------------------------------------
# 2. Purchase Order
# --------------------------------------------------------------------------
order = env['purchase.order'].create({
    'partner_id': vendor.id,
    'order_line': [(0, 0, {
        'product_id': product.id,
        'name': product.name,
        'product_qty': 5,
        'uom_id': product.uom_id.id,
        'price_unit': 6.0,
    }) for product in products.values()],
})
check('purchase order built', order.state == 'draft', order.name)

# --------------------------------------------------------------------------
# 3. Stock take (Count Sheet from Physical Inventory)
# --------------------------------------------------------------------------
quant_ids = env['stock.quant'].search([
    ('product_id', 'in', [p.id for p in products.values()]),
    ('location_id', '=', stock.id),
]).ids
check('quants exist to count', bool(quant_ids), f'{len(quant_ids)} quants')

# --------------------------------------------------------------------------
# Render all three through the real engine
# --------------------------------------------------------------------------
report = env['ir.actions.report'].with_context(force_report_rendering=True)
DOCUMENTS = [
    ('picking operations', 'stock.action_report_picking', picking.ids),
    ('purchase order', 'purchase.action_report_purchase_order', order.ids),
    ('stock take sheet', 'stock.action_report_inventory', quant_ids),
]

paths = {}
with_picture = sum(1 for p in products.values() if p.image_128)
for label, xmlid, ids in DOCUMENTS:
    html, _t = env['ir.actions.report']._render_qweb_html(xmlid, ids)
    markers = html.decode().count('cognify-product-image')
    pdf, _t = report._render_qweb_pdf(xmlid, ids)
    path = os.path.join(OUT_DIR, xmlid.split('.')[-1] + '.pdf')
    with open(path, 'wb') as handle:
        handle.write(bytes(pdf))
    paths[label] = path
    check(f'{label}: pictures in HTML', markers == with_picture,
          f'{markers} of {with_picture} products with a picture')
    check(f'{label}: PDF renders', bytes(pdf)[:5] == b'%PDF-', f'{len(pdf)} bytes')
    check(f'{label}: no webp left in the PDF', b'image/webp' not in bytes(pdf))

# The plain product must not add a picture of its own.
picking_plain = env['stock.picking'].search(
    [('move_ids.product_id', '=', plain.id)], limit=1)
html, _t = env['ir.actions.report']._render_qweb_html(
    'stock.action_report_picking', picking_plain.ids)
check('product without a picture prints no picture',
      html.decode().count('cognify-product-image') == with_picture)

# Switched off, everything must go back to the Odoo default layout.
env['ir.config_parameter'].sudo().set_bool('cognify_product_images.picking', False)
html_off, _t = env['ir.actions.report']._render_qweb_html(
    'stock.action_report_picking', picking.ids)
check('switch off removes every picture',
      'cognify-product-image' not in html_off.decode())
env['ir.config_parameter'].sudo().set_bool('cognify_product_images.picking', True)

# The Delivery Slip was deliberately left out of scope: it must print exactly
# as Odoo ships it.
delivery = env['stock.picking'].create({
    'picking_type_id': env.ref('stock.picking_type_out').id,
    'location_id': stock.id,
    'location_dest_id': customers.id,
    'move_ids': [(0, 0, {
        'product_id': products['Blue Widget PNG'].id,
        'product_uom_qty': 1,
        'location_id': stock.id,
        'location_dest_id': customers.id,
    })],
})
delivery.action_confirm()
delivery.action_assign()
html, _t = env['ir.actions.report']._render_qweb_html(
    'stock.action_report_delivery', delivery.ids)
check('delivery slip is left untouched', 'cognify-product-image' not in html.decode())

# The Settings block has to load in the web client, with every field it uses.
settings_view = env.ref('stock.res_config_settings_view_form')
arch = env['res.config.settings'].get_view(settings_view.id, 'form')['arch']
check('settings block is on the settings page',
      'cognify_product_images_setting_container' in arch)
missing = [name for name in ('cognify_picking_images', 'cognify_purchase_order_images',
                             'cognify_stock_take_images', 'cognify_image_size')
           if name not in env['res.config.settings']._fields]
check('settings fields exist', not missing, 'missing: %s' % missing)

env.cr.rollback()

print('\n' + '=' * 66)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 66)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back)')
print('PDFs written to ' + OUT_DIR)
for label, path in paths.items():
    print(f'  {label}: {path}')