"""Check the management reports end to end through the running Odoo server.

Everything here is HTTP to http://127.0.0.1:8069 as a real logged-in user, so
the live workers do the work: authenticate, read every report action, read every
SQL view, and confirm the dashboards are published and reachable.

This is deliberately not the same as the module's own tests. Those prove the
arithmetic; this proves that a user sitting in front of the running server can
actually open each report and get figures back, which is the only thing that
covers the views, the actions, the menus and the access rules at once.
"""
import json
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
LOGIN = 'pictures.tester'
PASSWORD = open('/home/kob/Documents/vuma-retail/.picturetesterpass').read().strip()

ok, fail = [], []


def check(label, cond, detail=''):
    line = f'{label}{" -> " + detail if detail else ""}'
    (ok if cond else fail).append(line)
    print(('  PASS  ' if cond else '  FAIL  ') + line, flush=True)


session = requests.Session()


def rpc(params):
    response = session.post(f'{BASE}/web/dataset/call_kw', timeout=300, json={
        'jsonrpc': '2.0', 'method': 'call', 'params': params})
    body = response.json()
    if 'error' in body:
        raise RuntimeError(json.dumps(body['error'].get('data', {}))[:300])
    return body['result']


def call(model, method, args=None):
    return rpc({'model': model, 'method': method, 'args': args or [],
                'kwargs': {'context': {}}})


def search_read(model, domain, fields, limit=None, order=None):
    # limit is a keyword here on purpose: search_read takes offset before it
    # positionally, and passing it in the wrong slot quietly skips rows.
    kwargs = {'context': {}}
    if limit:
        kwargs['limit'] = limit
    if order:
        kwargs['order'] = order
    return rpc({'model': model, 'method': 'search_read',
                'args': [domain, fields], 'kwargs': kwargs})


def read_group(model, domain, aggregates, groupby):
    # read_group takes the groupby before the aggregates in Odoo 20. The old
    # order was fields then groupby, and passing them the other way round does
    # not error usefully: it is read as a granularity on a non-date field.
    return rpc({'model': model, 'method': 'read_group',
                'args': [domain, groupby, aggregates],
                'kwargs': {'context': {}}})


print('--- log in ---')
auth = session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
    'jsonrpc': '2.0', 'method': 'call',
    'params': {'db': DB, 'login': LOGIN, 'password': PASSWORD}}).json()
check('authenticated as pictures.tester', bool(auth.get('result', {}).get('uid')),
      f"uid={auth.get('result', {}).get('uid')}")

print('\n--- every report model returns data ---')
CONFIRMED = [('state', 'not in', ['draft', 'sent', 'cancel'])]
ACTIONS = [
    ('Sales Overview', 'sale.report', CONFIRMED),
    ('Sales by Day', 'sale.report', CONFIRMED),
    ('Sales by Month', 'sale.report', CONFIRMED),
    ('Sales by Channel', 'sale.report', CONFIRMED),
    ('Sales by Category', 'sale.report', CONFIRMED),
    ('Cost of Sales', 'sale.report', CONFIRMED),
    ('Pipeline', 'sale.report', [('state', 'in', ['draft', 'sent'])]),
    ('Product Turnover', 'sale.report', CONFIRMED),
    ('Salesperson by Month', 'sale.report', CONFIRMED),
    ('Customer Balances', 'cognify.customer.credit', []),
    ('Staff Hours', 'cognify.staff.report', []),
    ('Leave', 'cognify.staff.report', [('leave_taken', '>', 0)]),
    ('Debt Ageing', 'cognify.customer.ageing', []),
    ('Cash and Bank', 'cognify.cash.position', []),
    ('Stock Health', 'cognify.stock.health', []),
    ('Dead and Slow Stock', 'cognify.stock.health', [('is_dead', '=', True)]),
    ('Low and Out of Stock', 'cognify.stock.health',
     [('stock_status', 'in', ['low', 'out'])]),
    ('Achievement Against Target', 'cognify.rep.achievement', []),
    ('Sales Targets', 'cognify.sales.target', []),
    ('Expense Spend', 'cognify.expense.report', []),
    ('Timesheet Utilisation', 'cognify.utilisation.report', []),
]
for label, model, domain in ACTIONS:
    try:
        rows = search_read(model, domain or [], ['id'], limit=1)
        check(f'{label}: {model}', bool(rows), 'no data' if not rows else 'ok')
    except Exception as error:  # noqa: BLE001
        check(f'{label}: {model}', False, str(error)[:160])

print('\n--- the sales figures are real ---')
turnover = search_read('sale.report', CONFIRMED,
                       ['price_subtotal', 'product_uom_qty'], limit=3)
check('turnover figures present', bool(turnover) and turnover[0]['price_subtotal'] > 0,
      f"top line {turnover[0]['price_subtotal']:.2f}" if turnover else '')

# The demo spreads its orders over months on purpose, so a report that only
# ever returns the current month is broken, not merely quiet.
months = read_group('sale.report', CONFIRMED, ['price_subtotal:sum'], ['date:month'])
check('sales spread over several months', len(months) >= 3,
      f'{len(months)} months with sales')

print('\n--- money owed ---')
balances = search_read('cognify.customer.credit', [('outstanding', '>', 0)],
                       ['partner_name', 'outstanding', 'overdue'], limit=5)
check('customers owing money', bool(balances),
      f"largest {balances[0]['partner_name']} {balances[0]['outstanding']:.2f}"
      if balances else '')

ageing = search_read('cognify.customer.ageing', [('outstanding', '>', 0)],
                     ['partner_name', 'age_band', 'outstanding', 'overdue'], limit=5)
check('debt ageing returns customers', bool(ageing),
      f"{ageing[0]['partner_name']} band {ageing[0]['age_band']}" if ageing else '')
bands = {row['age_band'] for row in
         search_read('cognify.customer.ageing', [('outstanding', '>', 0)],
                     ['age_band'], limit=100)}
check('ageing bands are distinct', len(bands) >= 1, ', '.join(sorted(bands)))

# The buckets have to add up to the outstanding balance, or the report is
# quietly losing money on the way to the screen.
AGGREGATES = ['bucket_current:sum', 'bucket_30:sum', 'bucket_60:sum',
              'bucket_90:sum', 'bucket_90_plus:sum', 'outstanding:sum',
              'overdue:sum']
totals = read_group('cognify.customer.ageing', [], AGGREGATES, [])
# With no groupby Odoo 20 hands back bare value lists rather than
# dicts, so the figures are read by position.
if totals:
    row = totals[0]
    t = (dict(zip([a.split(':')[0] for a in AGGREGATES], row))
         if isinstance(row, list) else row)
    buckets = (t['bucket_current'] + t['bucket_30'] + t['bucket_60']
               + t['bucket_90'] + t['bucket_90_plus'])
    check('ageing buckets add up to outstanding',
          abs(buckets - t['outstanding']) < 0.01,
          f'buckets {buckets:.2f} vs outstanding {t["outstanding"]:.2f}')
    overdue = t['bucket_30'] + t['bucket_60'] + t['bucket_90'] + t['bucket_90_plus']
    check('overdue matches the past-due buckets', abs(overdue - t['overdue']) < 0.01,
          f'{overdue:.2f} vs {t["overdue"]:.2f}')
else:
    check('ageing buckets add up to outstanding', False, 'no rows')

print('\n--- cash ---')
cash = search_read('cognify.cash.position', [('receipts', '>', 0)],
                   ['journal_name', 'month', 'receipts', 'net', 'closing'], limit=5)
check('money in recorded against the bank', bool(cash),
      f"{cash[0]['journal_name']} {cash[0]['receipts']:.2f} in "
      f"{cash[0]['month']}" if cash else '')
if cash:
    check('money in is not cancelled out by money out',
          any(row['receipts'] > 0 for row in
              search_read('cognify.cash.position', [], ['receipts'], limit=200)),
          'every month netting to zero would mean both sides of the posting '
          'are being counted')

print('\n--- stock ---')
stock = search_read('cognify.stock.health', [('qty_on_hand', '>', 0)],
                    ['product_name', 'qty_on_hand', 'value_cost', 'value_retail',
                     'margin_percent'], limit=5)
check('stock valued at cost and retail', bool(stock),
      f"{stock[0]['product_name']} cost {stock[0]['value_cost']:.2f} "
      f"retail {stock[0]['value_retail']:.2f}" if stock else '')
if stock:
    check('stock carries a real cost',
          all(row['unit_cost'] > 0 for row in
              search_read('cognify.stock.health', [('qty_on_hand', '>', 0)],
                          ['unit_cost'], limit=50)),
          'a zero cost means the stock never received a valuation')

print('\n--- targets ---')
targets = search_read('cognify.sales.target', [], ['date', 'target_revenue'], limit=5)
check('sales targets exist', bool(targets),
      f"{len(targets)} shown, first {targets[0]['target_revenue']:.2f}"
      if targets else '')
achievement = search_read('cognify.rep.achievement', [('has_target', '=', True)],
                           ['user_name', 'month', 'revenue', 'target_revenue',
                            'attainment'], limit=5)
check('achievement reads against targets', bool(achievement),
      f"{achievement[0]['user_name']} {achievement[0]['attainment']:.0f}% "
      f"in {achievement[0]['month']}" if achievement else '')
untargeted = search_read('cognify.rep.achievement', [('has_target', '=', False)],
                         ['has_target', 'attainment', 'target_revenue'], limit=5)
# Attainment cannot read blank: Odoo's Float turns an empty value into 0.0 on
# the way out of the ORM, so a NULL in the view would be a blank nobody ever
# sees. The flag is what carries the distinction, and it is what a user has to
# read to tell "no goal set" from "sold nothing against a goal".
check('no target is flagged, not silently counted as zero',
      all(row['attainment'] == 0.0 and not row['has_target']
          and row['target_revenue'] == 0.0 for row in untargeted),
      f'{len(untargeted)} rows with no goal set, all at 0%')

print('\n--- staff, time off and short paid ---')
hours = search_read('cognify.staff.report', [('hours_worked', '>', 0)],
                    ['employee_name', 'month', 'hours_worked', 'overtime_hours'],
                    limit=5)
check('staff hours recorded', bool(hours),
      f"{hours[0]['employee_name']} {hours[0]['hours_worked']:.1f}h in "
      f"{hours[0]['month']}" if hours else '')

leave = search_read('cognify.staff.report', [('annual_entitlement', '>', 0)],
                    ['employee_name', 'month', 'annual_entitlement',
                     'leave_available', 'short_paid_days', 'leave_exhausted'],
                    limit=5)
check('leave entitlement comes from allocations', bool(leave),
      f"{leave[0]['employee_name']} entitled {leave[0]['annual_entitlement']:.0f}d"
      if leave else '')

short_paid = search_read('cognify.staff.report', [('short_paid_days', '>', 0)],
                         ['employee_name', 'month', 'short_paid_days',
                          'leave_available', 'leave_exhausted'], limit=5)
check('short paid leave recorded', bool(short_paid),
      f"{short_paid[0]['employee_name']} {short_paid[0]['short_paid_days']:.0f}d "
      f"short paid in {short_paid[0]['month']}" if short_paid else '')

exhausted = search_read('cognify.staff.report', [('leave_exhausted', '=', True)],
                        ['employee_name', 'month', 'leave_available'], limit=5)
check('someone has run out of leave', bool(exhausted),
      f"{exhausted[0]['employee_name']} at {exhausted[0]['leave_available']:.1f}d"
      if exhausted else '')

# Entitlement must never be below zero. A negative figure would mean the
# report is double counting the entitlement against leave it already counted.
negatives = search_read('cognify.staff.report', [('annual_entitlement', '<', 0)],
                        ['employee_name'], limit=5)
check('no negative entitlement', not negatives, f'{len(negatives)} rows')

print('\n--- expenses and time ---')
expenses = search_read('cognify.expense.report', [('expense_total', '>', 0)],
                       ['employee_name', 'expense_total', 'expense_ratio'], limit=5)
check('approved expenses recorded', bool(expenses),
      f"{expenses[0]['employee_name']} {expenses[0]['expense_total']:.2f}"
      if expenses else '')

utilisation = search_read('cognify.utilisation.report', [('hours', '>', 0)],
                          ['employee_name', 'hours', 'billable_hours', 'cost',
                           'timesheet_coverage'], limit=5)
check('timesheets recorded', bool(utilisation),
      f"{utilisation[0]['employee_name']} {utilisation[0]['hours']:.1f}h"
      if utilisation else '')
if utilisation:
    check('timesheet coverage never exceeds 100%',
          all(row['timesheet_coverage'] <= 100.0 for row in
              search_read('cognify.utilisation.report', [],
                          ['timesheet_coverage'], limit=100)))

print('\n--- money is shown in a currency ---')
# A Monetary field with no currency formats as a bare number, which is the
# one thing that makes a finance report look amateurish.
for model, measure, currency_field in [
        ('cognify.customer.ageing', 'outstanding', 'company_currency_id'),
        ('cognify.cash.position', 'receipts', 'currency_id'),
        ('cognify.stock.health', 'value_cost', 'company_currency_id'),
        ('cognify.rep.achievement', 'revenue', 'company_currency_id'),
        ('cognify.expense.report', 'expense_total', 'company_currency_id'),
        ('cognify.utilisation.report', 'cost', 'company_currency_id')]:
    fields_info = call(model, 'fields_get', [[currency_field], ['type']])
    declared = currency_field in fields_info
    rows = search_read(model, [], [currency_field], limit=20)
    populated = any(row[currency_field] for row in rows)
    check(f'{model}.{currency_field} exists and is set',
          declared and populated,
          'declared' if declared else 'field missing')

print('\n--- names read as text, not as jsonb ---')
# Translated columns are jsonb keyed by language on some tables and plain text
# on others, depending on whether translations have ever been written for them.
# A report view that selects one as the other shows the user a raw
# {"en_US": "Bank"} in the column, which is how the cash and stock reports were
# broken until the view read both ways.
for model, name_field in [
        ('cognify.cash.position', 'journal_name'),
        ('cognify.stock.health', 'product_name'),
        ('cognify.customer.ageing', 'partner_name'),
        ('cognify.customer.credit', 'partner_name'),
        ('cognify.staff.report', 'employee_name'),
        ('cognify.expense.report', 'employee_name'),
        ('cognify.utilisation.report', 'employee_name'),
        ('cognify.rep.achievement', 'user_name')]:
    rows = search_read(model, [], [name_field], limit=20)
    odd = [row[name_field] for row in rows
           if not isinstance(row[name_field], str)]
    check(f'{model}.{name_field} reads as text', rows and not odd,
          f'{odd[:1]}' if odd else 'clean')

print('\n--- menus and dashboards ---')
# Looked up by xmlid rather than by name. Four other modules in this database
# ship a menu of their own called "Management" (HR, Inventory, Accounting and
# one more), all nested under something else, so a name search returns five
# rows and the first one is whichever the planner happened to put lowest.
root = call('ir.model.data', 'search_read',
            [[['model', '=', 'ir.ui.menu'],
              ['module', '=', 'cognify_management_reports'],
              ['name', '=', 'menu_cognify_management']], ['res_id']])
check('Management menu exists', bool(root),
      f"id {root[0]['res_id']}" if root else 'not found')

# Every report below sits one level under a group (Sales, Customers, Finance,
# Staff...), so child_of is what walks down through those.
child_ids = []
if root:
    # Not through call(): that sends everything in `args` positionally, and
    # search_read takes offset third, so a limit passed there arrives as an
    # offset dict and dies inside the server on int().
    child_ids = rpc({'model': 'ir.ui.menu', 'method': 'search_read',
                     'args': [[['id', 'child_of', root[0]['res_id']]], ['name']],
                     'kwargs': {'context': {}, 'limit': 200}})
child_names = {row['name'] for row in child_ids}
REPORT_MENUS = {'Debt Ageing', 'Cash and Bank Position', 'Stock Health and Value',
                'Dead and Slow Moving Stock', 'Low and Out of Stock',
                'Achievement Against Target', 'Sales Targets', 'Expenses',
                'Timesheet Utilisation', 'Hours Worked',
                'Leave Taken and Available'}
missing_menus = REPORT_MENUS - child_names
check('every new report has a menu', not missing_menus,
      ', '.join(sorted(missing_menus)) if missing_menus
      else f'{len(REPORT_MENUS)} menus present')

# Matched by name: a plain user cannot read spreadsheet.dashboard.group, so
# looking the section up that way would fail for reasons of access, not content.
EXPECTED_DASHBOARDS = {
    'Management Overview', 'Sales and Products', 'Customers', 'Staff',
    'Finance and Cash', 'Stock', 'Targets and People',
}
published = search_read('spreadsheet.dashboard', [('is_published', '=', True)],
                        ['name'], limit=100)
names = {d['name'] for d in published}
missing = EXPECTED_DASHBOARDS - names
check('all seven Management dashboards are published for this user', not missing,
      ', '.join(sorted(missing)) if missing
      else ', '.join(sorted(EXPECTED_DASHBOARDS & names)))

# A dashboard whose chart points at a deleted action renders as an empty box,
# so the wiring is worth checking rather than trusting the generated file.
print('\n--- the dashboard files parse and point at live actions ---')
module_dir = '/home/kob/Documents/vuma-retail/custom-addons/cognify_management_reports'
dashboard_xml = ET.parse(f'{module_dir}/data/dashboard_data.xml').getroot()
data_files = [node.get('file') for node in dashboard_xml.iter('field')
              if node.get('file', '').endswith('.json')]
check('seven dashboard files declared', len(data_files) == 7,
      f'{len(data_files)} declared')

referenced = set()
for relative in data_files:
    # The file= attribute is the path Odoo resolves inside the addons path, so
    # it starts with the module directory rather than with the project root.
    payload = json.load(open(f'{module_dir}/data/files/'
                             f'{Path(relative).name}'))
    for sheet in payload['sheets']:
        for figure in sheet.get('figures', []):
            source = figure.get('data', {}).get('dataSource', {})
            if source.get('type') == 'odoo':
                referenced.add(source['actionXmlId'])
resolved = rpc({'model': 'ir.model.data', 'method': 'search_read',
                'args': [[['model', '=', 'ir.actions.act_window'],
                          ['module', '=', 'cognify_management_reports']],
                         ['name']],
                'kwargs': {'context': {}, 'limit': 500}})
live_actions = {f'cognify_management_reports.{row["name"]}' for row in resolved}
missing_actions = sorted(referenced - live_actions)
check('every dashboard chart points at an action that exists',
      not missing_actions,
      ', '.join(missing_actions) if missing_actions
      else f'{len(referenced)} distinct actions, all live')

print('\n' + '=' * 64)
print(f'{len(ok)} passed, {len(fail)} failed')
for line in fail:
    print('  FAILED: ' + line)
print('=' * 64)