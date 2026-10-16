"""Run the read_group every dashboard pivot and chart depends on.

The odoo chart data source builds a pivot from the dataSource's own
``metaData``/``searchParams`` -- resModel, measure, groupBy, domain -- and asks
the server for one read_group. The KPI tiles read a pivot of their own. If
either read fails the tile or the chart renders as an error on the page, and no
shape check catches it: the document imports fine, the action exists, the model
has the field. Only the query itself fails.

So this walks every dashboard file and runs exactly those queries as a
logged-in user, the same way the browser would.

    ./venv/bin/python ops_check_chart_data.py
"""
import json
from pathlib import Path

import requests

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
LOGIN = 'pictures.tester'
PASSWORD = open('/home/kob/Documents/vuma-retail/.picturetesterpass').read().strip()
FILES = Path('/home/kob/Documents/vuma-retail/custom-addons/cognify_management_reports/data/files')

session = requests.Session()
auth = session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
    'jsonrpc': '2.0', 'method': 'call',
    'params': {'db': DB, 'login': LOGIN, 'password': PASSWORD}}).json()
if not auth.get('result', {}).get('uid'):
    raise SystemExit('could not log in')


def rpc(params):
    response = session.post(f'{BASE}/web/dataset/call_kw', timeout=300, json={
        'jsonrpc': '2.0', 'method': 'call', 'params': params})
    body = response.json()
    if 'error' in body:
        data = body['error'].get('data', {})
        raise RuntimeError(data.get('message') or json.dumps(data)[:300])
    return body['result']


import datetime  # noqa: E402


class _RelativeDelta:
    """A date shift that supports both `date + delta` and `date - delta`.

    Python's date subtraction yields a timedelta rather than a shifted date,
    which is not how the browser's interpreter treats these expressions: there
    `context_today() - relativedelta(months=1)` is simply the date a month back.
    Returning this type keeps `date + delta` and `date - delta` both producing
    the shifted date, so the domain means the same thing it means on screen.
    """

    def __init__(self, **kwargs):
        self.kwargs = kwargs

    def __radd__(self, other):
        return _shift(other, self.kwargs)

    def __add__(self, other):
        return _shift(other, self.kwargs)

    def __rsub__(self, other):
        return _shift(other, {key: -value for key, value in self.kwargs.items()})

    def __repr__(self):
        return f'relativedelta({self.kwargs})'


def _shift(base, kwargs):
    months = kwargs.get('months', 0) + kwargs.get('years', 0) * 12
    total = base.year * 12 + base.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
    day = min(base.day, last)
    return datetime.date(year, month, day) + datetime.timedelta(
        days=kwargs.get('days', 0))


SCOPE = {
    'context_today': lambda: datetime.date.today(),
    'relativedelta': _RelativeDelta,
    'strftime': lambda fmt, value=None: (value or datetime.date.today()).strftime(fmt),
    'today': lambda: datetime.date.today(),
    'now': lambda: datetime.datetime.now(),
    'datetime': datetime, 'date': datetime.date, 'time': datetime.time,
    'timedelta': datetime.timedelta,
    # The browser's interpreter spells booleans both ways -- py.js resolves
    # lower-case `true` as well as `True` -- so the domain carries whichever the
    # builder wrote and both have to survive being read here.
    'True': True, 'False': False, 'None': None,
    'true': True, 'false': False, 'null': None,
    'set': set, 'min': min, 'max': max, 'bool': bool,
}


def domain_literal(domain):
    # The real builtins are used on purpose. Stripping them is how the name
    # policing is done elsewhere (test_domains_are_evaluable_in_the_browser and
    # _domain_problems in the builder); here the only question is whether the
    # read_group behind the chart succeeds. A stripped dict also breaks CPython's
    # attribute-call path, which the domains rely on for .strftime().
    return eval(domain, dict(SCOPE), {})  # noqa: S307


ok, fail = 0, 0
for path in sorted(FILES.glob('*.json')):
    data = json.loads(path.read_text())

    pivots = data.get('pivots') or {}
    if pivots:
        print(f'\n[{path.name}] {len(pivots)} pivots (KPI tiles)')
    for pivot_id in sorted(pivots, key=int):
        pivot = pivots[pivot_id]
        model = pivot.get('model')
        # A dimension is an object carrying a fieldName (that is the shape
        # o-spreadsheet reads), so it has to be unwrapped before it can go to
        # read_group as a groupby string.
        dimensions = list(pivot.get('rows') or []) + list(pivot.get('columns') or [])
        groupby = [
            (d.get('fieldName', '') + ':' + d['granularity'])
            if isinstance(d, dict) and d.get('granularity')
            else (d.get('fieldName') if isinstance(d, dict) else d)
            for d in dimensions
        ]
        aggregates = [
            f"{measure['fieldName']}:{measure.get('aggregator') or 'sum'}"
            for measure in (pivot.get('measures') or [])
        ]
        label = f'  {path.stem:<20} pivot {pivot_id} {pivot.get("name", "")[:24]:<24} {model}'
        try:
            rows = rpc({'model': model, 'method': 'read_group',
                        'args': [domain_literal(pivot.get('domain') or '[]'),
                                 groupby, aggregates],
                        'kwargs': {'context': pivot.get('context') or {}}})
        except Exception as error:  # noqa: BLE001
            print(f'  FAIL  {label} -> {str(error)[:170]}')
            fail += 1
            continue
        print(f'  PASS  {label} -> '
              f'{len(rows) if rows else "0 groups (no data)"}')
        ok += 1

    charts = [figure for sheet in data['sheets']
              for figure in (sheet.get('figures') or [])
              if figure.get('tag') == 'chart'
              and figure.get('data', {}).get('dataSource', {}).get('type') == 'odoo']
    print(f'\n[{path.name}] {len(charts)} odoo charts')
    for figure in charts:
        definition = figure['data']
        source = definition['dataSource']
        meta = source.get('metaData', {})
        params = source.get('searchParams', {})
        model = meta.get('resModel')
        measure = meta.get('measure')
        groupby = [g for g in (params.get('groupBy') or meta.get('groupBy') or [])]
        title = (definition.get('title') or {}).get('text', figure['id'])
        label = f'  {path.stem:<20} {title[:34]:<34} {model}'

        if not model:
            print(f'  FAIL  {label} -> no resModel')
            fail += 1
            continue
        try:
            aggregates = [f'{measure}:sum'] if measure else ['__count']
            rows = rpc({'model': model, 'method': 'read_group',
                        'args': [domain_literal(params.get('domain') or '[]'),
                                 groupby, aggregates],
                        'kwargs': {'context': params.get('context') or {}}})
        except Exception as error:  # noqa: BLE001
            print(f'  FAIL  {label} -> {str(error)[:170]}')
            fail += 1
            continue
        empty = not rows
        note = '0 groups (no data)' if empty else f'{len(rows)} groups'
        print(f'  PASS  {label} -> {note}')
        ok += 1

print('\n' + '=' * 64)
print(f'{ok} queries loaded their data, {fail} failed')
print('=' * 64)
raise SystemExit(1 if fail else 0)
