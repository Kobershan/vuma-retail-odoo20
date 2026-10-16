"""Finish the tax move: leave each tax holding one set of repartition lines.

`ops_fix_country_taxes.py` moved every reference to the old taxes, including
their repartition lines, which is one step more than it should. Each new tax
therefore ended up with eight lines where it needs four -- the set it created
for itself plus the old tax's set re-parented onto it -- and the archived old
taxes were left with none, which is not a well-formed tax.

Which set survives is not a free choice. ``account_move_line.tax_repartition_
line_id`` is RESTRICT, so every posted VAT row is holding one of those lines
open: the re-parented set is the one the books point at and cannot be deleted.
The fresh set is the one that goes, because nothing has been posted against a
tax that did not exist a moment ago.

So: keep the referenced lines, drop the unreferenced surplus down to one base
and one tax line per document, then give the archived old taxes a fresh set of
the same shape so they are valid records rather than husks.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_fix_tax_repartition_lines.py
"""
new = env['account.tax'].browse([509, 510, 511, 512])
old = env['account.tax'].browse([1, 2, 3, 4])


def _report(taxes, label):
    print(label)
    for tax in taxes:
        invoice = tax.invoice_repartition_line_ids
        refund = tax.refund_repartition_line_ids
        accounts = invoice.mapped('account_id')
        print(f"  tax {tax.id} '{tax.name}' (active={tax.active}): "
              f"{len(invoice)} invoice, {len(refund)} refund -> "
              f"{', '.join(accounts.mapped('name')) or 'no account'}")


_report(new | old, "before:")

# Which of a tax's lines the books point at. Everything else is a surplus.
env.cr.execute("""
    SELECT r.tax_id, r.id
    FROM account_tax_repartition_line r
    WHERE r.id IN (
        SELECT tax_repartition_line_id FROM account_move_line
        WHERE tax_repartition_line_id IS NOT NULL)""")
referenced = set(env.cr.fetchall())

surplus = env['account.tax.repartition.line'].browse()
for tax in new:
    for document in ('invoice', 'refund'):
        for kind in ('base', 'tax'):
            lines = getattr(tax, f'{document}_repartition_line_ids').filtered(
                lambda line, kind=kind: line.repartition_type == kind).sorted('id')
            if len(lines) < 2:
                continue
            # Keep the referenced one if there is one, else the oldest.
            kept = [line for line in lines if (tax.id, line.id) in referenced]
            keeper = kept[0] if kept else lines[0]
            for line in lines:
                if line.id != keeper.id:
                    surplus |= line

if surplus:
    surplus.unlink()
    print(f"\ndropped {len(surplus)} surplus repartition line(s)")

# The archived taxes kept none of their own, so give them a valid shape back.
from odoo import Command  # noqa: E402

for tax in old:
    if tax.invoice_repartition_line_ids and tax.refund_repartition_line_ids:
        continue
    tax.write({
        'invoice_repartition_line_ids': [
            Command.create({'repartition_type': 'base', 'factor_percent': 100}),
            Command.create({'repartition_type': 'tax', 'factor_percent': 100}),
        ],
        'refund_repartition_line_ids': [
            Command.create({'repartition_type': 'base', 'factor_percent': 100}),
            Command.create({'repartition_type': 'tax', 'factor_percent': 100}),
        ],
    })

env.flush_all()
_report(new | old, "\nafter:")

broken = env['account.tax'].with_context(active_test=False).search(
    [('company_id', '=', new.company_id.id)]).filtered(
    lambda tax: len(tax.invoice_repartition_line_ids) != 2
    or len(tax.refund_repartition_line_ids) != 2)
print(f"\ntaxes without exactly one base and one tax line per document: "
      f"{len(broken)}")
if broken:
    raise SystemExit(f"  {broken.mapped('name')}")

env.cr.commit()
print("done")
