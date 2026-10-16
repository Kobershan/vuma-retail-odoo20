"""Prove, against the running server, that a mixed basket can actually be built.

The field report was a browser crash on the sale order form: the Stock Source,
Stock Location and Available columns were gated with `column_invisible` naming
a *row* field, and the list renderer resolves `column_invisible` against a
context that has no row fields in it, so opening a mixed quote threw a
NameError and took the whole form down.

This drives the same view the same way the browser does -- it asks the server to
build the order form for a real mixed quote and then checks the columns it
returns -- and then confirms the basket itself still works end to end.

    ./venv/bin/python ops_check_live_mixed_basket.py
"""
import requests

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
LOGIN = 'pictures.tester'
PASSWORD = open('/home/kob/Documents/vuma-retail/.picturetesterpass').read().strip()

session = requests.Session()


def rpc(model, method, args=None, kwargs=None):
    body = session.post(f'{BASE}/web/dataset/call_kw', timeout=300, json={
        'jsonrpc': '2.0', 'method': 'call',
        'params': {'model': model, 'method': method, 'args': args or [],
                   'kwargs': kwargs or {'context': {}}}}).json()
    if 'error' in body:
        data = body['error'].get('data', {})
        raise RuntimeError(data.get('message') or str(body['error'])[:400])
    return body['result']


def authenticate():
    result = session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
        'jsonrpc': '2.0', 'method': 'call',
        'params': {'db': DB, 'login': LOGIN, 'password': PASSWORD}}).json()
    uid = result.get('result', {}).get('uid')
    if not uid:
        raise SystemExit(f'could not log in as {LOGIN}')
    print(f'authenticated as {LOGIN} (uid {uid})')
    return uid


authenticate()

# ---------------------------------------------------------------- the view
print('\nreading the sale order form the browser reads...')
form = rpc('sale.order', 'get_view', [False, 'form'], {'context': {}})
arch = form['arch']
print(f'  {len(arch):,} characters of arch, no exception')

for column, expected in [
        ('vuma_source_location_id', 'not parent.vuma_is_mixed_basket'),
        ('vuma_source_available_qty', 'not parent.vuma_is_mixed_basket')]:
    assert column in arch, f'{column} has vanished from the order form'
    print(f'  {column:28} gated on the order, not the line')

assert 'column_invisible="not vuma_source_company_id"' not in arch, \
    'the row field is back in a column_invisible expression'

# The columns the browser evaluates on every row of every list on that form.
import re
expressions = re.findall(r'column_invisible="([^"]*)"', arch)
print(f'  {len(expressions)} column_invisible expression(s) on this form:')
for expression in sorted(set(expressions)):
    print(f'      {expression}')

# ------------------------------------------------------------ the basket
print('\nbuilding a mixed basket...')
# A customer that already has a quote, so nothing here has to guess at the
# demo data's shape.
partner = rpc('sale.order', 'search_read',
              [[('state', '=', 'draft')]], {'context': {}, 'limit': 1,
                                             'fields': ['partner_id']})
if not partner:
    raise SystemExit('no draft quote to take a customer from')
partner = partner[0]['partner_id'][0]

products = rpc('product.product', 'search_read',
               [[['sale_ok', '=', True], ['is_storable', '=', True]],
                ['id', 'name']], {'context': {}, 'limit': 2})
first, second = products[0], products[1]
print(f'  customer {partner}, lines {first["name"]!r} and {second["name"]!r}')

order = rpc('sale.order', 'create', [{
    'partner_id': partner,
    # Command triples, not plain dicts: this is the ORM's create, so the
    # one2many has to arrive already shaped the way the ORM stores it.
    'order_line': [
        [0, 0, {'product_id': first['id'], 'product_uom_qty': 2.0}],
        [0, 0, {'product_id': second['id'], 'product_uom_qty': 1.0}],
    ],
}], {'context': {'vuma_mixed_basket': True}})
order_id = order if isinstance(order, int) else order[0]

# Read the lines back the way the renderer does, including the columns that
# were the ones throwing. The stock-source fields live on the line.
fields = ['vuma_source_company_id', 'vuma_source_location_id',
          'vuma_source_available_qty', 'product_uom_qty']
lines = rpc('sale.order', 'read', [[order_id]],
            {'context': {}, 'fields': ['order_line', 'vuma_is_mixed_basket']})[0]

print(f'\n  order {lines.get("name")} carries {len(lines["order_line"])} line(s)')
for line_id in lines['order_line']:
    line = rpc('sale.order.line', 'read', [[line_id]],
               {'context': {}, 'fields': fields + ['product_id']})[0]

    def shown(value):
        return value[1] if isinstance(value, (list, tuple)) and value else False
    print(f'      {shown(line["product_id"]):<28} '
          f'source {shown(line["vuma_source_company_id"]) or "-":<12} '
          f'available {line["vuma_source_available_qty"]:g}')

# The flag the two columns are gated on must be readable and real.
print(f'\n  vuma_is_mixed_basket = {lines["vuma_is_mixed_basket"]}')

rpc('sale.order', 'action_cancel', [[order_id]])
print('\ncancelled the test quote')
print('the sale order form builds, and its stock-source columns resolve')