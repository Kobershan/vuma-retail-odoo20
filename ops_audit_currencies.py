"""Audit which records carry a currency that is not the company's.

The company trades in Rand, but records created while the company currency was
still set to something else keep the currency they were created in, and Odoo
will show them as-is afterwards rather than converting them. With no
``res_currency_rate`` rows at all there is nothing to convert with, so the
records simply read as though the business priced everything in the other
currency.

This lists every table that holds a currency and what each one is, so the
damage is visible before it is repaired.

    ./venv/bin/python ops_audit_currencies.py

The database password is read from `.dbpass` next to this file, or from
DB_PASSWORD in the environment. It is deliberately not written here: this file
is tracked, and a password in a tracked file travels to every clone.
"""
import os

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))


def _db_password():
    password = os.environ.get('DB_PASSWORD')
    if password:
        return password
    with open(os.path.join(HERE, '.dbpass')) as handle:
        return handle.read().strip()


conn = psycopg2.connect(
    host="localhost", port=5432, dbname="vuma_retail",
    user="odoo", password=_db_password())
cur = conn.cursor()

cur.execute(
    "SELECT currency_id FROM res_company ORDER BY id")
companies = cur.fetchall()
company_currency = {row[0] for row in companies}
print(f"company currency id(s): {sorted(company_currency)}")

cur.execute("""
    SELECT f.model, f.name
    FROM ir_model_fields f
    JOIN ir_model m ON m.id = f.model_id
    WHERE f.ttype = 'many2one' AND f.relation = 'res.currency'
      AND m.state = 'base'
    ORDER BY f.model, f.name""")
targets = [(m, n) for m, n in cur.fetchall() if not m.startswith("_")]
print(f"{len(targets)} currency field(s) across the registry\n")

skipped = []
problems = []
for model, field in targets:
    table = model.replace(".", "_")
    try:
        cur.execute(
            f"SELECT {field}, count(*) FROM {table} "
            f"WHERE {field} IS NOT NULL GROUP BY {field}")
        rows = cur.fetchall()
    except Exception:  # noqa: BLE001
        conn.rollback()
        skipped.append(f"{model}.{field}")
        continue
    if not rows:
        continue
    ids = ", ".join(str(currency) for currency, _ in rows)
    total = sum(count for _, count in rows)
    outside = {
        currency for currency, _ in rows if currency not in company_currency
    }
    if outside:
        names = []
        for currency in sorted(outside):
            cur.execute("SELECT name FROM res_currency WHERE id = %s",
                        (currency,))
            fetched = cur.fetchone()
            names.append(fetched[0] if fetched else str(currency))
        problems.append((model, field, names, total))
    else:
        print(f"  ok   {model}.{field}  {total} rows, company currency")

print(f"\n{len(skipped)} field(s) skipped (table/state): {skipped[:6]}{'...' if len(skipped) > 6 else ''}")
print(f"\n{len(problems)} field(s) reading a currency other than the company's:")
for model, field, names, total in problems:
    print(f"  {model}.{field}  {total} row(s)")
    print(f"    currencies: {sorted(set(names))}")

conn.close()
