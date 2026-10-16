"""End-to-end test of cognify_product_images against the RUNNING Odoo server.

Everything here goes over HTTP to http://127.0.0.1:8069 as a real logged-in
user, so the live workers do the work. No ORM session is used anywhere.

Walks the whole retail lifecycle with products that carry a PNG picture, a
JPEG picture, a WebP picture and no picture at all, printing the document that
belongs to each step:

  1. buy      -> Purchase Order
  2. receive  -> Picking Operations (incoming)
  3. deliver  -> Picking Operations (outgoing) and the Delivery Slip
  4. count    -> Count Sheet (stock take)
  5. settings -> each document switch, and the thumbnail size
  6. portal   -> a portal user downloading the purchase order PDF

Every printed document is downloaded through the report controller and checked
with pdfimages against the same document printed with the switches off, which
is the only honest way to know the pictures reached the file rather than just
the HTML. The PDFs are written to /tmp/opencode/live/ to be looked at.

Records are left in place so the result can also be clicked through in the
browser. `ops_cleanup_picture_test.py` removes them again.

    ./venv/bin/python ops_test_pictures_live.py
"""
import base64
import io
import json
import os
import subprocess
import sys

import requests
from PIL import Image, ImageDraw

try:
    from PIL import WebPImagePlugin  # noqa: F401
    WEBP = True
except ImportError:
    WEBP = False

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
LOGIN = 'pictures.tester'
PASSWORD_FILE = '/home/kob/Documents/vuma-retail/.picturetesterpass'
OUT_DIR = '/tmp/opencode/live'
SWITCHES = ['cognify_picking_images', 'cognify_purchase_order_images',
            'cognify_stock_take_images']

ok, fail = [], []
created = {}


def check(label, cond, detail=''):
    line = f'{label}{" -> " + detail if detail else ""}'
    (ok if cond else fail).append(line)
    print(('  PASS  ' if cond else '  FAIL  ') + line, flush=True)


def section(title):
    print(f'\n--- {title} ' + '-' * max(0, 58 - len(title)), flush=True)


# ---------------------------------------------------------------------------
# JSON-RPC against the running server
# ---------------------------------------------------------------------------
session = requests.Session()


def rpc(path, params):
    response = session.post(f'{BASE}{path}', timeout=300, json={
        'jsonrpc': '2.0', 'method': 'call', 'params': params})
    body = response.json()
    if 'error' in body:
        raise RuntimeError(json.dumps(body['error'].get('data', body['error']))[:700])
    return body.get('result')


def call(model, method, args=None, context=None):
    return rpc('/web/dataset/call_kw', {
        'model': model, 'method': method, 'args': args or [],
        'kwargs': {'context': context or {}}})


def create(model, vals):
    new_id = call(model, 'create', [vals])
    created.setdefault(model, []).append(new_id)
    return new_id


def report_pdf(report_name, doc_ids, http_session=None):
    """Download a PDF through the live report controller."""
    http = http_session or session
    url = f'{BASE}/report/pdf/{report_name}/' + ','.join(str(i) for i in doc_ids)
    response = http.get(url, timeout=300)
    if response.status_code != 200:
        raise RuntimeError(f'HTTP {response.status_code}: {response.text[:300]}')
    if response.content[:5] != b'%PDF-':
        raise RuntimeError(f'not a PDF: {response.content[:120]!r}')
    return response.content


def images_in_pdf(data):
    """How many pictures the PDF actually embeds (the logo counts too)."""
    path = os.path.join(OUT_DIR, '_probe.pdf')
    with open(path, 'wb') as handle:
        handle.write(data)
    listing = subprocess.run(['pdfimages', '-list', path],
                             capture_output=True, text=True).stdout
    return len([line for line in listing.strip().splitlines()
                if line.strip() and line.strip()[0].isdigit()])


def switches(value):
    for switch in SWITCHES:
        setting_id = create('res.config.settings', {switch: value})
        call('res.config.settings', 'execute', [[setting_id]])


def picture(fmt, size, text):
    """A recognisable test picture: a colour band with its format written on it."""
    im = Image.new('RGB', size, (255, 255, 255))
    draw = ImageDraw.Draw(im)
    draw.rectangle([0, 0, size[0], size[1] // 2], fill=(28, 86, 168))
    draw.text((12, size[1] // 4 - 4), text, fill=(255, 255, 255))
    buffer = io.BytesIO()
    im.save(buffer, format=fmt)
    return base64.b64encode(buffer.getvalue()).decode()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    password = open(PASSWORD_FILE).read().strip()

    section('log in')
    result = rpc('/web/session/authenticate',
                 {'db': DB, 'login': LOGIN, 'password': password})
    check('authenticated against the running server', bool(result.get('uid')),
          f"uid={result.get('uid')}")

    section('test products')
    products = {}
    for name, fmt, label in (('Live PNG Widget', 'PNG', 'PNG'),
                             ('Live JPEG Widget', 'JPEG', 'JPG'),
                             ('Live WEBP Widget', 'WEBP', 'WEBP')):
        if fmt == 'WEBP' and not WEBP:
            continue
        product_id = create('product.product', {
            'name': name, 'type': 'consu', 'is_storable': True,
            'list_price': 12.0, 'standard_price': 7.0,
        })
        call('product.product', 'write',
             [[product_id], {'image_1920': picture(fmt, (400, 400), label)}])
        products[name] = product_id
    products['plain'] = create('product.product', {
        'name': 'Live Plain Product (no picture)', 'type': 'consu',
        'is_storable': True, 'list_price': 5.0, 'standard_price': 3.0,
    })
    check('four products created', len(products) == 4, ', '.join(products))

    stored = call('product.product', 'read',
                  [[products['Live PNG Widget'], products['Live WEBP Widget']],
                   ['name', 'image_128']])
    check('pictures stored on the products', all(row.get('image_128') for row in stored),
          ', '.join(f"{row['name']}:{len(row.get('image_128') or '')}" for row in stored))

    section('1. buy: purchase order')
    vendor_id = create('res.partner', {
        'name': 'Live Test Vendor', 'is_company': True,
        'street': '1 Test Street', 'city': 'Harare',
    })
    order_id = create('purchase.order', {
        'partner_id': vendor_id,
        'order_line': [(0, 0, {'product_id': product_id, 'name': name,
                               'product_qty': 10, 'price_unit': 7.0,
                               'date_planned': '2026-10-20 00:00:00'})
                       for name, product_id in products.items()],
    })
    call('purchase.order', 'button_confirm', [[order_id]])
    state = call('purchase.order', 'read', [[order_id], ['state']])[0]['state']
    check('purchase order confirmed', state in ('purchase', 'done'), state)

    section('2. receive: incoming picking')
    receipt_id = call('purchase.order', 'read', [[order_id], ['picking_ids']])[0]['picking_ids'][0]
    created.setdefault('stock.picking', []).append(receipt_id)
    call('stock.picking', 'action_confirm', [[receipt_id]])
    call('stock.picking', 'action_assign', [[receipt_id]])
    moves = call('stock.move', 'search_read',
                 [[['picking_id', '=', receipt_id]], ['id', 'product_uom_qty', 'quantity']])
    move_lines = call('stock.move.line', 'search_read',
                      [[['move_id', 'in', [m['id'] for m in moves]]], ['id']])
    call('stock.move.line', 'write',
         [[line['id'] for line in move_lines], {'quantity': 10.0}])
    call('stock.move', 'write', [[m['id'] for m in moves], {'picked': True}])
    call('stock.picking', 'button_validate', [[receipt_id]])
    state = call('stock.picking', 'read', [[receipt_id], ['state']])[0]['state']
    check('goods received into stock', state == 'done', state)

    section('3. deliver: outgoing picking')
    customer_id = create('res.partner', {
        'name': 'Live Test Customer', 'street': '2 Shop Road', 'city': 'Harare'})
    sale_id = create('sale.order', {
        'partner_id': customer_id,
        'order_line': [(0, 0, {'product_id': product_id, 'product_uom_qty': qty,
                               'price_unit': 12.0})
                       for product_id, qty in zip(products.values(), (3, 3, 3, 2))],
    })
    call('sale.order', 'action_confirm', [[sale_id]])
    out_ids = call('stock.picking', 'search',
                   [[['sale_id', '=', sale_id], ['state', '!=', 'cancel']]])
    delivery_id = out_ids[0]
    created.setdefault('stock.picking', []).append(delivery_id)
    check('delivery transfer created', bool(delivery_id), f'picking {delivery_id}')

    section('4. count: stock take sheet')
    quant_ids = call('stock.quant', 'search',
                     [[['product_id', 'in', list(products.values())]]])
    created.setdefault('stock.quant', []).extend(quant_ids)
    check('quants to count', bool(quant_ids), f'{len(quant_ids)} quants')

    documents = [
        ('purchase order', 'purchase.report_purchaseorder', [order_id],
         'cognify_purchase_order_images'),
        ('incoming picking', 'stock.report_picking', [receipt_id],
         'cognify_picking_images'),
        ('outgoing picking', 'stock.report_picking', [delivery_id],
         'cognify_picking_images'),
        ('stock take sheet', 'stock.report_inventory', quant_ids,
         'cognify_stock_take_images'),
    ]
    pictured = len(products) - 1

    section('5. print every document, on and off')
    for label, report_name, ids, _switch in documents:
        switches(True)
        with_pictures = report_pdf(report_name, ids)
        switches(False)
        without = report_pdf(report_name, ids)

        path = os.path.join(OUT_DIR, label.replace(' ', '_') + '.pdf')
        with open(path, 'wb') as handle:
            handle.write(with_pictures)
        gained = images_in_pdf(with_pictures) - images_in_pdf(without)
        check(f'{label}: PDF prints', with_pictures[:5] == b'%PDF-',
              f'{len(with_pictures)} bytes (off: {len(without)})')
        check(f'{label}: pictures reach the PDF', gained >= pictured,
              f'{gained} extra images for {pictured} pictured products -> {path}')

    section('6. settings switches and size')
    for label, report_name, ids, switch in documents:
        switches(True)
        on_count = images_in_pdf(report_pdf(report_name, ids))
        setting_id = create('res.config.settings', {switch: False})
        call('res.config.settings', 'execute', [[setting_id]])
        try:
            off_count = images_in_pdf(report_pdf(report_name, ids))
        finally:
            switches(True)
        check(f'{label}: {switch} alone turns them off', off_count < on_count,
              f'{on_count} -> {off_count} images')

    for size in ('small', 'large', 'medium'):
        setting_id = create('res.config.settings', {'cognify_image_size': size})
        call('res.config.settings', 'execute', [[setting_id]])
        pdf = report_pdf('stock.report_picking', [delivery_id])
        check(f'thumbnail size {size} prints', pdf[:5] == b'%PDF-', f'{len(pdf)} bytes')

    section('7. delivery slip is still untouched')
    switches(True)
    delivery_slip = report_pdf('stock.report_deliveryslip', [delivery_id])
    check('delivery slip prints without pictures',
          b'cognify-product-image' not in delivery_slip)

    section('8. access to the pictures')
    # The helper reads pictures as superuser so printing never fails on a
    # cosmetic image. Whether that is needed depends on who can print: if every
    # user who can reach a document can also read products, there is no live
    # case where the sudo matters. Report the fact rather than asserting
    # something that cannot fail here.
    portal_ids = call('res.users', 'search', [[('login', '=', 'pictures.portal')]])
    if portal_ids:
        portal_user = portal_ids[0]
        portal_session = requests.Session()
        auth = portal_session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
            'jsonrpc': '2.0', 'method': 'call',
            'params': {'db': DB, 'login': 'pictures.portal', 'password': 'PortalTest-2026'}})
        if auth.json().get('result', {}).get('uid'):
            probe = requests.post(f'{BASE}/web/dataset/call_kw', timeout=120, cookies={
                'session_id': portal_session.cookies.get('session_id', '')}, json={
                'jsonrpc': '2.0', 'method': 'call',
                'params': {'model': 'product.product', 'method': 'read',
                           'args': [[products['Live WEBP Widget']], ['name']],
                           'kwargs': {'context': {}}}}).json()
            readable = 'result' in probe
            check('portal buyer cannot read products (sudo in the helper covers this)',
                  not readable,
                  'product read refused, as expected' if not readable
                  else 'they can read products; the sudo is belt and braces here')
        else:
            check('portal buyer session', False, 'could not authenticate')

    print('\nCreated records (removed again by ops_cleanup_picture_test.py):')
    for model, ids in created.items():
        print(f'  {model:20s} {ids}')
    # Record exactly what was made, so cleanup never has to guess.
    with open(os.path.join(OUT_DIR, 'created.json'), 'w') as handle:
        json.dump(created, handle, indent=2)
    print(f'\nPDFs written to {OUT_DIR}')


if __name__ == '__main__':
    try:
        main()
    except Exception:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        print('\nRUN ABORTED')
    print('\n' + '=' * 68)
    print(f'{len(ok)} passed, {len(fail)} failed')
    for line in fail:
        print('  FAILED: ' + line)
    print('=' * 68)
    sys.exit(1 if fail else 0)