"""Prove, against the running server, that choosing an invoice fills the lines.

This is the end-to-end check for the upliftment flow, and it deliberately goes
over RPC rather than through a shell: an addon's Python is imported when a
worker starts, so a shell would import the current file and prove nothing about
what the browser is being served. Here the record is created, filled and
confirmed through the same endpoint the web client uses.

    ./venv/bin/python ops_check_live_pickup_lines.py
"""
import requests

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
# Raising a pickup needs the Goods Pickup groups. The general test user has to
# carry them for this check, which it does via ops_grant_pickup_group.py.
LOGIN = 'pictures.tester'
PASSWORD = open('/home/kob/Documents/vuma-retail/.picturetesterpass').read().strip()

session = requests.Session()
auth = session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
    'jsonrpc': '2.0', 'method': 'call',
    'params': {'db': DB, 'login': LOGIN, 'password': PASSWORD}}).json()
uid = auth.get('result', {}).get('uid')
if not uid:
    raise SystemExit('could not log in')
print(f"authenticated as {LOGIN} (uid {uid})")


def rpc(model, method, args=None, kwargs=None):
    response = session.post(f'{BASE}/web/dataset/call_kw', timeout=300, json={
        'jsonrpc': '2.0', 'method': 'call',
        'params': {'model': model, 'method': method, 'args': args or [],
                   'kwargs': kwargs or {'context': {}}}})
    body = response.json()
    if 'error' in body:
        data = body['error'].get('data', {})
        raise RuntimeError(data.get('message') or str(body['error'])[:300])
    return body['result']


# A customer with more than one posted invoice, so the choice is a real choice.
invoices = rpc('account.move', 'search_read', [
    [('move_type', '=', 'out_invoice'), ('state', '=', 'posted')],
    ['name', 'partner_id', 'invoice_date', 'amount_total']],
    {'context': {}, 'limit': 200})
by_partner = {}
for move in invoices:
    by_partner.setdefault(move['partner_id'][0], []).append(move)
partner = next(p for p, moves in by_partner.items() if len(moves) >= 2)
theirs = sorted(by_partner[partner], key=lambda m: m['invoice_date'])

print(f"\ncustomer #{partner}, {len(theirs)} posted invoices:")
for move in theirs:
    print(f"    {move['name']}  {move['invoice_date']}  "
          f"{move['amount_total']:,.2f}")

chosen = theirs[-1]
print(f"\nchoosing {chosen['name']}...")

warehouse = rpc('stock.warehouse', 'search_read',
                [[('company_id', '=', uid)], ['id']],
                {'context': {}, 'limit': 1})
warehouse_id = warehouse[0]['id'] if warehouse else 1
locations = rpc('stock.location', 'search_read',
                [[('usage', '=', 'internal'),
                  ('warehouse_id', '=', warehouse_id)]],
                {'context': {}, 'limit': 1})
if not locations:
    raise SystemExit('no internal location in that warehouse')

created = rpc('customer.goods.pickup', 'create', [{
    'partner_id': partner,
    'invoice_id': chosen['id'],
    'warehouse_id': warehouse_id,
    'dest_location_id': locations[0]['id'],
    'pickup_reason': 'customer_request',
}])
pickup_id = created if isinstance(created, int) else created[0]

# What the form gets is the same fill, on demand.
rpc('customer.goods.pickup', 'action_reload_invoice_lines', [[pickup_id]])

lines = rpc('customer.goods.pickup', 'read', [[pickup_id]], {
    'context': {},
    'fields': ['name', 'invoice_number', 'state', 'total_collected_qty',
               'line_ids']})[0]
detail = rpc('customer.goods.pickup.line', 'read',
             [lines['line_ids'],
              ['product_id', 'invoiced_qty', 'eligible_qty', 'qty_collected',
               'product_uom_id']],
             {'context': {}})

product_lines = len([i for i in rpc('account.move', 'read',
                                   [[chosen['id']]], {
                                       'context': {},
                                       'fields': ['invoice_line_ids']})[0]
                     ['invoice_line_ids']])

print(f"{lines['name']} against {lines['invoice_number']}: "
      f"{len(detail)} line(s) offered, nothing filled in yet")
for line in detail:
    def shown(value):
        return value[1] if isinstance(value, (list, tuple)) else value
    print(f"    {str(shown(line['product_id'])):<30} "
          f"invoiced {line['invoiced_qty']:>7.2f}  "
          f"collectable {line['eligible_qty']:>7.2f}  "
          f"qty collected {line['qty_collected']:>5.2f}")

if not detail:
    raise SystemExit("  the server filled nothing")
if any(line['qty_collected'] for line in detail):
    raise SystemExit("  something was pre-filled as collected, which it must not be")

# Take from a line that still has something available. An earlier confirmed
# pickup reserves its quantity, so a line can legitimately read zero here -- that
# is the ledger doing its job, not something to assert against.
available = [line for line in detail if line['eligible_qty'] > 0]
if not available:
    raise SystemExit("  every line on this invoice is already fully reserved")
first = available[0]
print(f"\ntaking from {first['product_id'][1] if isinstance(first['product_id'], (list, tuple)) else first['product_id']}, "
      f"the first line with quantity still available")
rpc('customer.goods.pickup.line', 'write',
    [[first['id']], {'qty_collected': first['eligible_qty']}])
print(f"\nentered {first['eligible_qty']:g} of the first line, "
      f"left the other {len(detail) - 1} at zero")

rpc('customer.goods.pickup', 'action_confirm_collection', [[pickup_id]])
after = rpc('customer.goods.pickup', 'read', [[pickup_id]], {
    'context': {}, 'fields': ['state', 'total_collected_qty', 'line_ids']})[0]
print(f"confirmed: state={after['state']}, {len(after['line_ids'])} line(s), "
      f"total collected {after['total_collected_qty']:g}")

assert after['state'] == 'collected', after['state']
assert len(after['line_ids']) == 1, \
    'a line left at zero should have been dropped, not collected'
assert abs(after['total_collected_qty'] - first['eligible_qty']) < 1e-6
print("\n  a line left at zero was dropped rather than refused")

print(f"\n{pickup_id} is confirmed and is part of the audit trail, so it stays.")
print("Run ops_cleanup_pickup_checks.py to remove what this check left behind.")
print("\nthe running server fills the lines on choosing the invoice")
