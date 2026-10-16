"""Fetch what the server actually serves for each dashboard and compare it to
the document stored in the filestore.

The stored files import cleanly, but they are not what the browser loads.
``/spreadsheet/dashboard/data/<id>`` post-processes the document -- filters,
revision id, the model's fields -- so it is the served payload that has to be
valid, and it is the only one of the two that nothing here has ever checked.

    ./venv/bin/python ops_fetch_served_dashboards.py [outdir]
"""
import json
import sys
from pathlib import Path

import requests

BASE = 'http://127.0.0.1:8069'
DB = 'vuma_retail'
LOGIN = 'pictures.tester'
PASSWORD = open('/home/kob/Documents/vuma-retail/.picturetesterpass').read().strip()
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else '/tmp/opencode/served')

session = requests.Session()
auth = session.post(f'{BASE}/web/session/authenticate', timeout=120, json={
    'jsonrpc': '2.0', 'method': 'call',
    'params': {'db': DB, 'login': LOGIN, 'password': PASSWORD}}).json()
uid = auth.get('result', {}).get('uid')
if not uid:
    raise SystemExit(f'could not log in: {json.dumps(auth)[:400]}')


def rpc(params):
    response = session.post(f'{BASE}/web/dataset/call_kw', timeout=300, json={
        'jsonrpc': '2.0', 'method': 'call', 'params': params})
    body = response.json()
    if 'error' in body:
        raise RuntimeError(json.dumps(body['error'].get('data', {}))[:400])
    return body['result']


dashboards = rpc({'model': 'spreadsheet.dashboard', 'method': 'search_read',
                  'args': [[['dashboard_group_id.name', '=', 'Management']],
                           ['name', 'dashboard_group_id']],
                  'kwargs': {'context': {}, 'limit': 100}})
if not dashboards:
    # group name is a translated jsonb column; fall back to the group id.
    group = rpc({'model': 'spreadsheet.dashboard.group', 'method': 'search_read',
                 'args': [[], ['name']], 'kwargs': {'context': {}, 'limit': 100}})
    gid = next(g['id'] for g in group if g['name'] == 'Management')
    dashboards = rpc({'model': 'spreadsheet.dashboard', 'method': 'search_read',
                      'args': [[['dashboard_group_id', '=', gid]], ['name']],
                      'kwargs': {'context': {}, 'limit': 100}})

OUT.mkdir(parents=True, exist_ok=True)
print(f'{"id":>4}  {"name":<26} {"stored":>8} {"served":>8}  verdict')
for dashboard in sorted(dashboards, key=lambda d: d['id']):
    name = dashboard['name']
    slug = name.lower().replace(' ', '_').replace('&', 'and')
    response = session.get(f'{BASE}/spreadsheet/dashboard/data/{dashboard["id"]}',
                           timeout=300)
    if response.status_code != 200:
        print(f'{dashboard["id"]:>4}  {name:<26} {"-":>8} {"-":>8}  '
              f'HTTP {response.status_code}')
        continue
    body = response.json()
    # The endpoint wraps the document: {"snapshot": {...}, "revisions": [],
    # "default_currency": {...}, "translation_namespace": "..."}. Only the
    # snapshot is the o-spreadsheet document the browser builds its model from.
    payload = body.get('snapshot') or body.get('data') or body
    stored = session.post(
        f'{BASE}/web/dataset/call_kw', timeout=300, json={
            'jsonrpc': '2.0', 'method': 'call', 'params': {
                'model': 'spreadsheet.dashboard', 'method': 'read',
                'args': [[dashboard['id']], ['spreadsheet_binary_data']],
                'kwargs': {'context': {}}}}).json()['result'][0][
                    'spreadsheet_binary_data']
    served_path = OUT / f'{slug}.json'
    served_path.write_text(json.dumps(payload))

    # The served document is what the browser parses, so its structural
    # invariants are what matter, not the stored file's.
    problems = []
    if not isinstance(payload, dict):
        problems.append('served payload is not an object')
    else:
        if not isinstance(payload.get('globalFilters'), list):
            problems.append(
                f'globalFilters is {type(payload.get("globalFilters")).__name__}, '
                'not a list')
        sheets = payload.get('sheets')
        if not isinstance(sheets, list):
            problems.append('sheets is not a list')
        else:
            ids = [sheet.get('id') for sheet in sheets]
            if 'dashboard_sheet_id' not in ids:
                problems.append('no dashboard_sheet_id')
            for sheet in sheets:
                for figure in sheet.get('figures') or []:
                    if figure.get('tag') == 'chart':
                        source = figure.get('data', {}).get('dataSource', {})
                        if source.get('type') == 'odoo' and not source.get(
                                'actionXmlId'):
                            problems.append(
                                f'chart {figure.get("id")} has no actionXmlId')
        pivots = payload.get('pivots')
        if pivots is not None and not isinstance(pivots, dict):
            problems.append('pivots is not an object')
    verdict = '; '.join(problems) if problems else 'ok'
    print(f'{dashboard["id"]:>4}  {name:<26} {len(json.dumps(stored)):>8} '
          f'{len(response.text):>8}  {verdict}')

print(f'\nserved payloads written to {OUT}')
