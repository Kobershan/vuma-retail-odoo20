"""Move the company's taxes onto the country it actually invoices in.

Odoo refuses an invoice whose taxes do not belong to the company's fiscal
country, so this had to be put right before anything in this database could be
touched. The four taxes here were defined for the United States while
`res.company.account_fiscal_country_id` is South Africa -- which is both the
validation failure and the reason a quotation read as though its 15% was an
American one.

What the taxes *are* stays exactly as it is: the amount, the direction (sale or
purchase) and the posting account are carried over. What changes is the country
they belong to, and with that the name they are shown under. A 15% sale tax
becomes "VAT 15%", a 15% purchase tax becomes "VAT 15%" as the recoverable half
of the same rate on a purchase, and the two zero-rated ones keep their direction
in their name.

Every use of the old taxes is repointed to the replacement by *role*, not by id,
so the same product rows, sale and purchase lines, Expenses, company defaults
and posted move lines all finish on the tax that does the same job. The old ones
are then archived rather than deleted, because posted move lines still reference
them.

No duplicate can appear from the remap: the replacements did not exist before,
so no row can already be carrying both the tax it had and the one it is moved
to.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_fix_country_taxes.py
"""
company = env.company
country = company.account_fiscal_country_id
currency = company.currency_id
print(f"company {company.name!r} fiscal country "
      f"{country.with_context(lang='en_US').name} ({currency.name})")

foreign = env['account.tax'].with_context(active_test=False).search([
    ('company_id', '=', company.id),
    ('country_id', '!=', country.id),
])
if not foreign:
    print("every tax already belongs to the fiscal country")
    raise SystemExit(0)
print(f"{len(foreign)} tax(es) on another country:")


def _relabel(tax):
    """The name the tax is shown under in the country it now belongs to."""
    if tax.amount:
        return f"VAT {tax.amount:.0f}%"
    return ('Zero-rated export' if tax.type_tax_use == 'sale'
            else 'Zero-rated import')


def _tax_account(tax, document_type):
    """The account the tax's own taxable line of one document posts to."""
    return getattr(tax, f'{document_type}_repartition_line_ids').filtered(
        lambda line: line.repartition_type == 'tax').mapped('account_id')


def _tax_columns():
    """Every (table, column) in the schema that holds a tax.

    Move lines also point at the tax row itself through ``tax_line_id`` and at
    the group through ``group_tax_id``; both are left, because they belong to
    the repartition line and the group the posting was made against, not to the
    rate a document charges.
    """
    env.cr.execute("""
        SELECT c.conrelid::regclass::text, a.attname
        FROM pg_constraint c
        CROSS JOIN LATERAL unnest(c.conkey) AS k(attnum)
        JOIN pg_attribute a
             ON a.attrelid = c.conrelid AND a.attnum = k.attnum
        WHERE c.confrelid = 'account_tax'::regclass
          AND c.contype = 'f'
        ORDER BY 1, 2""")
    columns = {}
    for table_name, column in env.cr.fetchall():
        if column in ('tax_line_id', 'group_tax_id'):
            continue
        columns.setdefault(table_name, []).append(column)
    return columns


# ---- build the replacements ------------------------------------------------
vat_group = env['account.tax.group'].search([
    ('name', '=', f"VAT {currency.name}"),
    ('country_id', '=', country.id)], limit=1)
if not vat_group:
    vat_group = env['account.tax.group'].create({
        'name': f"VAT {currency.name}", 'country_id': country.id})

zero_group = env['account.tax.group'].search([
    ('name', 'like', 'Zero'), ('country_id', '=', country.id)], limit=1)
if not zero_group:
    zero_group = env['account.tax.group'].create({
        'name': 'Zero-rated', 'country_id': country.id})

replacement = {}
for tax in sorted(foreign, key=lambda t: (t.type_tax_use, t.amount)):
    new = env['account.tax'].create({
        'name': _relabel(tax),
        'country_id': country.id,
        'company_id': company.id,
        'type_tax_use': tax.type_tax_use,
        # The amount and how it is applied are the business's own figures.
        'amount': tax.amount,
        'amount_type': tax.amount_type,
        'tax_scope': tax.tax_scope,
        'tax_group_id': zero_group.id if not tax.amount else vat_group.id,
        'include_base_amount': tax.include_base_amount,
        'sequence': tax.sequence,
        'description': tax.description,
    })
    if tax.amount:
        new.invoice_repartition_line_ids.filtered(
            lambda line: line.repartition_type == 'tax'
        ).account_id = _tax_account(tax, 'invoice')
        new.refund_repartition_line_ids.filtered(
            lambda line: line.repartition_type == 'tax'
        ).account_id = _tax_account(tax, 'refund')
    replacement[tax.id] = new
    print(f"  tax {tax.id} '{tax.name}' -> tax {new.id} '{new.name}' "
          f"({tax.type_tax_use}, {new.amount}%)")

id_map = {old: new.id for old, new in replacement.items()}
replacement_ids = list(id_map.values())

# ---- repoint every use -----------------------------------------------------
# One CASE per tax column, so a row keeps the other taxes it had rather than
# getting them silently substituted.
moved = []
for table_name, columns in sorted(_tax_columns().items()):
    table = table_name.replace('.', '_')
    cases = " ".join(f"WHEN {old} THEN {new} " for old, new in id_map.items())
    for column in columns:
        env.cr.execute(
            f"UPDATE {table} SET {column} = CASE {column} {cases} END "
            f"WHERE {column} IN %s",
            [tuple(id_map.keys())])
        if env.cr.rowcount:
            moved.append(f"  {table}.{column}: {env.cr.rowcount} row(s)")
print("\nrepointed:")
for item in sorted(set(moved)) or ['  nothing to change']:
    print(item)
env.flush_all()
env.cr.commit()

# ---- retire the old taxes --------------------------------------------------
foreign.write({'active': False})
print(f"\narchived {len(foreign)} foreign tax(es)")

# ---- the invariants the rest of the transaction relied on ------------------
env.flush_all()
env.cr.execute("""
    SELECT COUNT(*) FROM account_tax t
    WHERE t.company_id = %(company)s AND t.active
      AND t.country_id <> %(country)s
""", {'company': company.id, 'country': country.id})
still_foreign = env.cr.fetchone()[0]
env.cr.execute("""
    SELECT COUNT(*) AS used
    FROM account_move_line_account_tax_rel r
    JOIN account_tax t ON t.id = r.account_tax_id
    WHERE t.country_id <> %(country)s
""", {'country': country.id})
foreign_on_moves = env.cr.fetchone()[0]
env.cr.execute("""
    SELECT COUNT(*) AS used
    FROM account_tax_sale_order_line_rel r
    JOIN account_tax t ON t.id = r.account_tax_id
    WHERE t.country_id <> %(country)s
""", {'country': country.id})
foreign_on_orders = env.cr.fetchone()[0]
env.cr.execute("""
    SELECT COUNT(*) AS used
    FROM product_taxes_rel r
    WHERE r.tax_id NOT IN %(replacement)s
""", {'replacement': tuple(replacement_ids)})
foreign_on_products = env.cr.fetchone()[0]

print(f"  active taxes on another country: {still_foreign}")
print(f"  posted move lines still on one:  {foreign_on_moves}")
print(f"  sale order lines still on one:   {foreign_on_orders}")
print(f"  products still on one:           {foreign_on_products}")

problems = (still_foreign or foreign_on_moves or foreign_on_orders
            or foreign_on_products)
if problems:
    raise SystemExit("something was still left on a foreign tax")

print("\nthe new taxes post to:")
for tax_id in replacement_ids:
    tax = env['account.tax'].browse(tax_id)
    accounts = _tax_account(tax, 'invoice')
    print(f"  {tax.id} '{tax.name}' ({tax.type_tax_use}, {tax.amount}%) -> "
          f"{', '.join(accounts.mapped('name')) or 'no account'}")

env.cr.commit()
print("\ndone")
