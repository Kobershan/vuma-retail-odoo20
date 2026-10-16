"""Replay the exact server calls the dashboard's browser makes.

Two things decided earlier from the code, now checked against the running
server rather than assumed:

* a pivot's load path is ``fields_get`` on its model, then
  ``read_grouping_sets``; ``_getMeasureSpecs`` throws whenever a measure's
  field description has no aggregator, and ``_normalize`` throws whenever it
  is asked to group by a field that ``fields_get`` did not return;
* a chart's load path is ``read_group`` through the web GraphModel, which
  also throws without an aggregate function, and drops a groupby whose field
  is not groupable.

So every pivot and every chart in every dashboard file is walked and each of
those calls is made, with the field descriptions the model really returns --
not a guess about what they contain.

      ./venv/bin/python ops_replay_client_calls.py
"""
import json
import re
from pathlib import Path

import requests

BASE = "http://127.0.0.1:8069"
DB = "vuma_retail"
LOGIN = "pictures.tester"
PASSWORD = open("/home/kob/Documents/vuma-retail/.picturetesterpass").read().strip()
FILES = Path(
    "/home/kob/Documents/vuma-retail/custom-addons/cognify_management_reports/data/files")

session = requests.Session()
auth = session.post(f"{BASE}/web/session/authenticate", timeout=120, json={
    "jsonrpc": "2.0", "method": "call",
    "params": {"db": DB, "login": LOGIN, "password": PASSWORD}}).json()
if not auth.get("result", {}).get("uid"):
    raise SystemExit("could not log in")


def rpc(model, method, args, kwargs=None):
    response = session.post(f"{BASE}/web/dataset/call_kw", timeout=300, json={
        "jsonrpc": "2.0", "method": "call",
        "params": {"model": model, "method": method, "args": args,
                   "kwargs": kwargs or {"context": {}}}})
    body = response.json()
    if "error" in body:
        data = body["error"].get("data", {})
        raise RuntimeError(data.get("message") or json.dumps(data)[:300])
    return body["result"]


fields_cache = {}


def fields_of(model):
    """fields_get, as the client's loadFields() issues it."""
    if model not in fields_cache:
        fields_cache[model] = rpc(model, "fields_get", [None, None])
    return fields_cache[model]


GROUPABLE_TYPES = {"date", "datetime", "char", "selection", "many2one", "boolean"}


def domain_literal(domain):
    """Evaluate a stored domain string the way @web/core/py_js does."""
    import datetime

    class Rel:
        def __init__(self, **kw):
            self.kw = kw

        def __radd__(self, other):
            return self._shift(other, self.kw)

        def __add__(self, other):
            return self._shift(other, self.kw)

        def __rsub__(self, other):
            return self._shift(other, {k: -v for k, v in self.kw.items()})

        @staticmethod
        def _shift(base, kw):
            months = kw.get("months", 0) + kw.get("years", 0) * 12
            total = base.year * 12 + base.month - 1 + months
            year, month = divmod(total, 12)
            month += 1
            last = [31, 29 if year % 4 == 0 and (year % 100 or year % 400 == 0) else 28,
                    31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1]
            day = min(base.day, last)
            return datetime.date(year, month, day) + datetime.timedelta(days=kw.get("days", 0))

    scope = {
        "context_today": lambda: datetime.date.today(),
        "relativedelta": Rel,
        "strftime": lambda fmt, v=None: (v or datetime.date.today()).strftime(fmt),
        "today": lambda: datetime.date.today(),
        "now": lambda: datetime.datetime.now(),
        "datetime": datetime, "date": datetime.date, "time": datetime.time,
        "timedelta": datetime.timedelta,
        "True": True, "False": False, "None": None,
        "true": True, "false": False, "null": None,
        "set": set, "min": min, "max": max, "bool": bool,
    }
    return eval(domain, dict(scope), {})  # noqa: S307


aggregator_errors = []
errors = 0

for path in sorted(FILES.glob("*.json")):
    data = json.loads(path.read_text())
    print(f"\n=== {path.name} ===")

    for pivot_id, pivot in sorted((data.get("pivots") or {}).items(), key=lambda x: int(x[0])):
        model = pivot.get("model")
        label = f"  pivot {pivot_id} {model}"
        try:
            fields = fields_of(model)
        except Exception as error:  # noqa: BLE001
            print(f"  FAIL {label} -> fields_get: {str(error)[:150]}")
            errors += 1
            continue

        # activeMeasures is the measure fieldName, exactly as
        # getDefinitionForPivotModel() builds it.
        active = [m.get("fieldName") for m in (pivot.get("measures") or [])]
        missing = [name for name in active if name not in fields]
        if missing:
            print(f"  FAIL {label} -> fields_get omits {missing}")
            errors += 1
            continue
        # _getMeasureSpecs throws here, per field.
        no_agg = []
        for name in active:
            desc = fields[name]
            agg = desc.get("aggregator")
            if agg is None and desc.get("type") != "many2one":
                no_agg.append(name)
        if no_agg:
            detail = {n: {k: fields[n].get(k) for k in ("aggregator", "group_operator")}
                      for n in no_agg}
            print(f"  FAIL {label} -> no aggregate function for {no_agg}: {detail}")
            aggregator_errors.append((path.name, pivot, no_agg))
            errors += 1
            continue

        dims = list(pivot.get("rows") or []) + list(pivot.get("columns") or [])
        groupbys = []
        for d in dims:
            spec = d.get("fieldName") if isinstance(d, dict) else d
            if isinstance(d, dict) and d.get("granularity"):
                spec = f"{spec}:{d['granularity']}"
            base = spec.split(":")[0]
            desc = fields.get(base)
            if desc is None:
                print(f"  FAIL {label} -> _normalize: Field {base} does not exist")
                errors += 1
                break
            if not desc.get("groupable", True) or desc.get("type") not in GROUPABLE_TYPES:
                print(f"  FAIL {label} -> {base} not groupable "
                      f"(type={desc.get('type')}, groupable={desc.get('groupable')})")
                errors += 1
                break
            groupbys.append(spec)
        else:
            specs = [f"{m.get('fieldName')}:{fields[m.get('fieldName')].get('aggregator') or 'sum'}"
                     for m in pivot.get("measures") or []]
            try:
                rows = rpc(model, "read_grouping_sets",
                           [domain_literal(pivot.get("domain") or "[]"),
                            [groupbys] if groupbys else [[]],
                            specs])
            except Exception as first:  # noqa: BLE001
                reason = str(first)
                if "read_grouping_sets" not in reason:
                    print(f"  FAIL {label} -> {reason[:200]}")
                    errors += 1
                    continue
                try:
                    rows = rpc(model, "read_group",
                               [domain_literal(pivot.get("domain") or "[]"),
                                groupbys, specs])
                except Exception as error:  # noqa: BLE001
                    print(f"  FAIL {label} -> {str(error)[:200]}")
                    errors += 1
                    continue
            print(f"  ok   {label} -> {len(rows) if rows else '0'} group(s)")

    for sheet in data.get("sheets") or []:
        for figure in sheet.get("figures") or []:
            source = (figure.get("data") or {}).get("dataSource") or {}
            if source.get("type") != "odoo":
                continue
            meta, params = source.get("metaData", {}), source.get("searchParams", {})
            model = meta.get("resModel")
            measure = meta.get("measure")
            label = (figure.get("data") or {}).get("title", {}).get("text", figure.get("id"))
            label = f"  chart  {label[:28]:<28} {model}"
            try:
                fields = fields_of(model)
            except Exception as error:  # noqa: BLE001
                print(f"  FAIL {label} -> fields_get: {str(error)[:150]}")
                errors += 1
                continue
            if measure and measure != "__count" and measure not in fields:
                print(f"  FAIL {label} -> measure {measure} not in fields_get")
                errors += 1
                continue
            if measure and measure != "__count":
                desc = fields[measure]
                if desc.get("aggregator") is None and desc.get("type") != "many2one":
                    print(f"  FAIL {label} -> no aggregate function for {measure} "
                          f"({{aggregator: {desc.get('aggregator')}, "
                          f"group_operator: {desc.get('group_operator')}}})")
                    errors += 1
                    continue
            gbs, bad = [], None
            for gb in params.get("groupBy") or meta.get("groupBy") or []:
                base, _, interval = str(gb).partition(":")
                desc = fields.get(base)
                if desc is None:
                    bad = f"field {base} does not exist"
                    break
                if (not desc.get("groupable", True)
                        or desc.get("type") not in GROUPABLE_TYPES):
                    bad = (f"{base} not groupable (type={desc.get('type')}, "
                           f"groupable={desc.get('groupable')})")
                    break
                gbs.append(f"{base}:{interval}" if interval else base)
            if bad:
                print(f"  FAIL {label} -> {bad}")
                errors += 1
                continue
            agg_field = [f"{measure}:sum"] if measure and measure != "__count" else ["__count"]
            try:
                rows = rpc(model, "read_group",
                           [domain_literal(params.get("domain") or "[]"), gbs, agg_field])
            except Exception as error:  # noqa: BLE001
                print(f"  FAIL {label} -> {str(error)[:200]}")
                errors += 1
                continue
            print(f"  ok   {label} -> {len(rows) if rows else '0'} group(s)")

print("\n" + "=" * 70)
print(f"{errors} failed call(s)")
if aggregator_errors:
    print("\nFields a pivot measure ran into with no aggregate function:")
    for name, pivot, missing in aggregator_errors:
        print(f"  {name} pivot {pivot.get('id')}: {missing}")
print("=" * 70)
raise SystemExit(1 if errors else 0)
