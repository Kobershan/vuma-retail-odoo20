"""Confirm both pivots shapes are right in what the server actually serves.

    ./venv/bin/python ops_check_payload_shapes.py [dir]
"""
import glob
import json
import os
import sys

DIRS = sys.argv[1:] or [
    '/home/kob/Documents/vuma-retail'
    '/custom-addons/cognify_management_reports/data/files',
    '/tmp/opencode/served',
]

problems = []
files = 0
for directory in DIRS:
    paths = [p for p in glob.glob(os.path.join(directory, '*.json'))
             if os.path.isfile(p)]
    files += len(paths)
    for path in sorted(paths):
        payload = json.load(open(path))
        document = payload.get('snapshot') or payload
        sheet_ids = [sheet['id'] for sheet in document.get('sheets') or []]
        if 'dashboard_sheet_id' not in sheet_ids:
            problems.append(f'{path}: no dashboard_sheet_id')
        for key in ('pivots', 'lists', 'namedRanges', 'styles', 'formats',
                    'borders', 'customTableStyles'):
            if key in document and not isinstance(document[key], dict):
                problems.append(f'{path}: {key} must be an object')
        for key in ('globalFilters', 'sheets'):
            if key in document and not isinstance(document[key], list):
                problems.append(f'{path}: {key} must be a list')
        for pivot_id, pivot in (document.get('pivots') or {}).items():
            for axis in ('rows', 'columns'):
                value = pivot.get(axis) or []
                if not isinstance(value, list):
                    problems.append(
                        f'{os.path.basename(path)} pivot {pivot_id} '
                        f'{axis} must be a list')
                    continue
                bad = [d for d in value
                       if not isinstance(d, dict) or not d.get('fieldName')]
                if bad:
                    problems.append(
                        f'{os.path.basename(path)} pivot {pivot_id} {axis} '
                        f'has {len(bad)} entry/ies without a fieldName')
            filters = pivot.get('filters')
            # Absent is fine: `(definition.filters ?? []).map(...)` only
            # substitutes the empty list for a genuinely missing key.
            if filters is not None and not isinstance(filters, list):
                problems.append(
                    f'{os.path.basename(path)} pivot {pivot_id} filters is '
                    f'{type(filters).__name__}')
        for sheet in document.get('sheets') or []:
            for figure in sheet.get('figures') or []:
                if figure.get('tag') != 'chart':
                    continue
                data = figure.get('data') or {}
                if data.get('type') == 'scorecard':
                    continue
                source = data.get('dataSource') or {}
                if source.get('type') == 'odoo' and not source.get('actionXmlId'):
                    problems.append(
                        f'{os.path.basename(path)} chart {figure.get("id")} '
                        'has no actionXmlId')

print(f'{files} payload(s) checked across {len(DIRS)} location(s)')
if problems:
    print('  SHAPE PROBLEMS:')
    for item in problems:
        print(f'    {item}')
    raise SystemExit(1)
print('  no shape problems found')
