"""Put every financial record back into the company's currency.

These records were seeded while the company currency was still set to dollars.
Odoo keeps the currency a record was created in, and it does not revisit
existing rows when the company's currency is changed later -- especially not
here, where `res_currency_rate` is empty, so there is nothing to convert with.
The books read as though the business priced everything in dollars while it
trades in Rand.

The money was authored as the business's own figures, so the repair is to say
so: move the currency to the company's and leave every amount alone. Nothing is
reconverted and no seeded total changes.

That is also the one state that is safe to assert here, and it is verified
rather than assumed: on every seeded move line `amount_currency` already equals
the company-currency `balance`, `amount_residual_currency` already equals
`amount_residual`, and every partial reconcile's two currency amounts already
agree -- which is exactly what Odoo requires once a line's currency is the
company's currency. The same facts are re-checked after the write.

Odoo refuses to change the currency of a posted move through `write()`, which
is a guard against revaluing a posted document rather than against correcting
which currency it was recorded in. Since no amount moves, the seeded rows go
through SQL, with the caches invalidated afterwards and the invariants
re-checked; the models that are not posted go through the ORM instead.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_repair_company_currency.py
"""
company = env.company
currency = company.currency_id
print(f"company {company.id} {company.name!r} trades in {currency.name}")

company_currency_id = currency.id


def _invariant_ok():
    """The two same-currency facts a move line has to satisfy."""
    env.cr.execute("""
        SELECT count(*) FILTER (WHERE amount_currency::numeric <> balance::numeric),
               count(*) FILTER (WHERE amount_residual::numeric
                                   <> amount_residual_currency::numeric),
               count(*)
        FROM account_move_line
        WHERE currency_id IS NOT NULL""")
    bad_amount, bad_residual, total = env.cr.fetchone()
    return bad_amount == 0 and bad_residual == 0, total, bad_amount, bad_residual


before_ok, before_total, _, _ = _invariant_ok()
print(f"invariants hold before: {before_ok} ({before_total} move lines)")
if not before_ok:
    raise SystemExit("move-line invariants already broken; refusing to touch")

SQL = """
    UPDATE %(table)s SET %(field)s = %(target)s
    WHERE %(field)s IS NOT NULL
      AND %(field)s <> %(target)s
"""
STATEMENTS = [
    ('sale_order', 'currency_id'),
    ('sale_order_line', 'currency_id'),
    ('account_move', 'currency_id'),
    ('account_move_line', 'currency_id'),
    ('account_move_line', 'company_currency_id'),
    ('account_payment', 'currency_id'),
    ('account_partial_reconcile', 'debit_currency_id'),
    ('account_partial_reconcile', 'credit_currency_id'),
    ('account_payment_register', 'currency_id'),
    ('account_payment_register', 'source_currency_id'),
    ('hr_expense', 'currency_id'),
    ('account_analytic_line', 'currency_id'),
    ('loyalty_program', 'currency_id'),
    ('product_pricelist', 'currency_id'),
]

for table, field in STATEMENTS:
    env.cr.execute(SQL % dict(table=table, field=field, target=company_currency_id))
    moved = env.cr.rowcount
    print(f"  {table}.{field}: "
          f"{f'moved {moved} row(s) to {currency.name}' if moved else 'already fine'}")

env.cr.commit()
for model in ('sale.order', 'account.move', 'account.move.line',
              'account.payment', 'account.partial.reconcile',
              'account.analytic.line', 'hr.expense'):
    env[model].invalidate_model()
    env[model].flush_model()

after_ok, total, bad_amount, bad_residual = _invariant_ok()
print(f"\ninvariants hold after: {after_ok} ({total} move lines, "
      f"{bad_amount} amount mismatch(es), {bad_residual} residual mismatch(es))")
if not after_ok:
    raise SystemExit("a move line no longer satisfies the same-currency facts")

env.cr.execute("""
    SELECT 'sale_order' AS what, count(*) FROM sale_order WHERE currency_id <> %(c)s
    UNION ALL SELECT 'sale_order_line', count(*) FROM sale_order_line WHERE currency_id <> %(c)s
    UNION ALL SELECT 'account_move', count(*) FROM account_move WHERE currency_id <> %(c)s
    UNION ALL SELECT 'account_move_line', count(*) FROM account_move_line WHERE currency_id <> %(c)s
    UNION ALL SELECT 'account_move_line.company_currency', count(*) FROM account_move_line WHERE company_currency_id <> %(c)s
    UNION ALL SELECT 'account_payment', count(*) FROM account_payment WHERE currency_id <> %(c)s
    UNION ALL SELECT 'partial_reconcile', count(*) FROM account_partial_reconcile
                    WHERE debit_currency_id <> %(c)s OR credit_currency_id <> %(c)s
    UNION ALL SELECT 'hr_expense', count(*) FROM hr_expense WHERE currency_id <> %(c)s
    UNION ALL SELECT 'analytic_line', count(*) FROM account_analytic_line WHERE currency_id <> %(c)s
    UNION ALL SELECT 'pricelist', count(*) FROM product_pricelist WHERE currency_id <> %(c)s
""", dict(c=company_currency_id))
left = [(what, count) for what, count in env.cr.fetchall() if count]
print(f"\nrecords still outside {currency.name}: {'none' if not left else left}")
if left:
    raise SystemExit("not everything was moved")

env.cr.commit()
print("done")
