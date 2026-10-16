# Expiry Date Manager — Odoo 20 setup & usage

Module: `custom-addons/cognify_expiry_manager` (depends on Odoo's own
`product_expiry`, which it also installs).

Everything below has already been applied to the `vuma_retail` database.

---

## What it does

| Requirement | How it is met |
|---|---|
| Expiry date for **goods received** | Type the supplier's printed date on the **Purchase Order line**. It is carried onto the lot automatically when the receipt is validated. |
| Expiry date for **goods in stock** | Stored on the lot, shown on the lot form, the lot list and the Expiry Alerts menu. |
| **Closest to expiry is picked first** | Product category set to **First Expiry First Out (FEFO)**. The detailed-operations *Pick From* column tells the picker exactly which lot to take. |
| **Expiry date on the picking slip** | The slip prints an **Expiration Date** and a **Days Left** column, with expired batches flagged in red. |
| **Alert when getting close to expiry** | Four channels, each switchable: chatter activity, in-app popup, email, and the Expiry Alerts menu. |
| **Period configurable per product, e.g. 1 day or 3 months** | Each product carries an **Alert Lead Time** value plus a **unit** (Days / Weeks / Months / Years). Months and years use real calendar arithmetic. |

---

## Commands

```bash
cd /home/kob/Documents/vuma-retail

# Install (installs product_expiry too, as a dependency)
./venv/bin/python odoo/odoo-bin module install -c odoo.conf -d vuma_retail \
    cognify_expiry_manager

# Run the module's tests (20 tests, rolls back, leaves no data)
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail \
    -u cognify_expiry_manager --stop-after-init \
    --test-enable --test-tags=/cognify_expiry_manager --workers=0

# Uninstall / reinstall from scratch
./venv/bin/python odoo/odoo-bin module uninstall -c odoo.conf -d vuma_retail cognify_expiry_manager
./venv/bin/python odoo/odoo-bin module install    -c odoo.conf -d vuma_retail cognify_expiry_manager

# Run the server
./venv/bin/python odoo/odoo-bin -c odoo.conf        # http://127.0.0.1:8069
```

Note: the `odoo-bin module` subcommand takes its own `-c` / `-d`; `--stop-after-init`
only exists on the normal server invocation, not on the subcommand.

---

## Setting a product up

**Products → the product → Inventory tab**

1. **Tracking** = *By Unique Lot Number* (expiry cannot be tracked without lots).
2. **Dates** group → tick **Expiration Date**.
3. **Expiration Date** = shelf life in days after receipt (fallback only — the
   supplier's printed date wins if one is typed on the PO).
4. **Alert Lead Time** = a number, and **Alert Unit** beside it:
   * milk → `1` + `Days`
   * vaccines → `3` + `Months`
   Leave both empty to switch alerts off for that product.
5. Tick **Pick by Closest Expiry**, then press **Apply FEFO to this product's
   category**. That sets the category's removal strategy to FEFO and sets the
   removal lead time to 0, so removal date and expiry date coincide and FEFO
   genuinely means "closest expiry goes out first".

The **Responsible** field on the product decides who gets told about its lots.

### Pick a window that fits the shelf life

The window is how far ahead you want warning, so it has to make sense next to
how long the goods last:

| Product | Shelf life | Sensible window | Why |
|---|---|---|---|
| Whole milk | 14 days | `7` + Days | A week of warning is useful |
| Yoghurt | 10 days | `5` + Days | |
| Vaccine | 2 years | `3` + Months | Genuinely needs lead time |
| Seasonal stock | 1 year | `2` + Months | |

If the window is **wider than the shelf life** — `3 Months` on a 14-day product —
the goods read as *Expiring Soon* the moment they are received, because the
alert date already fell in the past. That is correct arithmetic, just rarely
what you want. Months and years use real calendar arithmetic: 3 months before
24 October is 24 July, not 22 July.

---

## Receiving goods

**Orders → Purchase Orders → the order → Order Lines → Supplier Expiry**

Type the date printed on the supplier's label. A date in the past is rejected —
goods cannot be received already expired.

On the **receipt**, the *Detailed Operations* screen shows:

* **Expiry Date** — read-only, the date the lot will receive
* **Override Expiry** — hidden by default; type here only to correct a mistake

Precedence when the lot is created: **Override Expiry → Supplier Expiry →
shelf life**.

---

## Alerts

**Inventory → Expiry Alerts** lists every lot that is inside its alert window or
past it, colour-coded red/orange, sorted by expiry date. Use the view's
**Download xlsx** for a spreadsheet, or **Print → Expiring Stock** for a PDF.

**Inventory → Settings → Expiry Alerts** controls:

* **Alert Recipients** — told in addition to each product's Responsible user
* **Notify via** — in-app popup / activity on the lot / email, each on its own
* **Expiry on Delivery Slips** — needs *Display Expiration Dates* **and**
  *Display Lots & Serial Numbers* both on

A scheduled action (*Expiry: notify on lots nearing their expiry date*) runs once
a day. Each lot alerts only **once per window**, so running it more often is
safe. **Inventory → Expiry Alerts → Re-scan Expiry Alerts** runs it immediately.

Changing a product's alert window clears the "already alerted" flag on its lots
that still hold stock, so a widened window immediately catches up.

Email needs an outgoing mail server. With none configured the mail is still
created and queued, it just is not transmitted.

---

## Delivery slips

`Settings → Inventory → Expiry on Delivery Slips`, turn on **Display Expiration
Dates** and **Display Lots & Serial Numbers**. Printed picking slips then show:

```
Product           Lot/Serial Number   Expiration Date   Days Left        Delivered
Yoghurt 500g      LOT-A               11/12/2026        30               3 Units
Yoghurt 500g      LOT-B               01/06/2026        -103 (expired)  2 Units
```

A slip carrying no expiry-tracked goods prints unchanged.

---

## Columns are hidden with `optional`, not `column_invisible`

The expiry columns on the purchase order and the receipt used to be gated with
`column_invisible="not use_expiration_date"`. That attribute is resolved **once
for the whole list**, against the list's eval context, which holds the context,
the user and the parent record and none of the row's own fields. An expression
naming a row field therefore compiles, loads, passes every server-side test and
then throws in the browser:

```
EvalError: Can not evaluate python expression: (bool(not use_expiration_date))
Error: Name 'use_expiration_date' is not defined
```

Which lines need an expiry date is a per-row fact, and `column_invisible` cannot
express one. `optional="show"` / `optional="hide"` can, so those columns use it.
`test_view_guards.py` in `vuma_multi_tenant` now evaluates every
`column_invisible` in every one of our views the way the list renderer does, and
fails on any bare name.

The tests here also build ordinary sale orders. A user in the Mixed Basket group
gets `vuma_is_mixed_basket` defaulted on every new quote, and a mixed quote is a
shell that splits into the two trading companies on confirm -- which would
refuse a plain order. The test helper turns that flag off when the module that
defines it is installed.

---

## Layout

```
custom-addons/cognify_expiry_manager/
├── expiry_period.py            calendar arithmetic for "3 months before..."
├── models/
│   ├── product_template.py     the value + unit alert window, FEFO button
│   ├── stock_lot.py            unit-aware alert date, the alert scan, 4 channels
│   ├── purchase_order_line.py  supplier expiry date + its past-date guard
│   ├── stock_move_line.py      carries that date onto the lot, receipt override
│   ├── stock_quant.py          FEFO sorts by removal/expiry date
│   └── res_config_settings.py  recipients + channel switches
├── report/                     picking-slip column, printable expiry report
├── views/                      product, PO, receipt, lot, settings, menus
├── data/                       daily cron, alert email template
├── security/                   Expiry Alerts / Expiry Alerts Manager groups
├── static/src/js/              in-app popup plugin
└── tests/                      21 tests covering the whole flow
```

### Ops scripts

| File | What it does |
|---|---|
| `ops/generate_column_default_repair.py` | Regenerates the column-default repair SQL from the live registry |
| `ops/fix_odoo20_required_column_defaults.sql` | The repair itself — see the Odoo test-suite section |
| `ops/schema_backup_res_partner_product_template.sql` | Schema of the two affected tables, taken before the repair |
| `ops/smoke_new_product_expiry.py` | End-to-end smoke test on the live DB, rolled back afterwards |

```bash
# prove a new perishable product works end to end, on the live database
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --log-level=error < ops/smoke_new_product_expiry.py
```

---

## Known issue: Odoo's own test suite (NOT from this module)

Running Odoo's **own** `stock` / `purchase_stock` test suite with
`--test-enable` died with
`null value in column "group_rfq" ... violates not-null constraint`.

**Diagnosis — upstream Odoo 20.0 defect, not local drift:**

* `res_partner.group_rfq`, `res_partner.group_on` (`purchase_stock`) and
  `product_template.invoice_policy` (`sale`) are live Odoo 20 fields declared
  `required=True`. Odoo makes such columns `NOT NULL` with **no database
  default**. A freshly initialised Odoo 20 database has exactly the same
  schema — verified by creating a throwaway database and comparing.
* Odoo's test helper `new_test_user` creates a user; the ORM builds the
  `res.partner` row through the `_inherits` mechanism and **omits**
  `group_rfq` / `group_on` from the INSERT, so the row is rejected.
* The running application is unaffected: creating products, partners, users and
  purchase orders through the ORM all supply these values correctly.

**Do not drop these columns.** `group_rfq` / `group_on` control RFQ grouping and
`invoice_policy` controls invoicing policy; dropping them breaks those features
and Odoo recreates them on the next module upgrade.

**Fix applied.** `ops/fix_odoo20_required_column_defaults.sql` gives every
NOT NULL column a database default equal to the value that column's own Odoo
field would have written. Safe by construction: Odoo supplies these values
during normal use so the default is never consulted, and the only behaviour that
changes is the case that used to raise.

```bash
psql -h localhost -U odoo -d vuma_retail \
     -f ops/fix_odoo20_required_column_defaults.sql
```

Regenerate it with `ops/generate_column_default_repair.py`. The original
`res_partner` / `product_template` schema is backed up in
`ops/schema_backup_res_partner_product_template.sql`, and every statement is
individually reversible with `ALTER TABLE ... ALTER COLUMN ... DROP DEFAULT`.

**Result: `22 errors of 521 tests` → `15 errors of 618 tests`.** Every NOT NULL
violation is gone. What remains is unrelated and not fixable at database level:

* Odoo `at_install` tests that read `payment.provider.custom_mode` before
  `delivery` has loaded the module adding `cash_on_delivery` to that selection;
* tests that assume reference/demo data this database does not have;
* one upstream Odoo test bug (`can't write on readonly field 'is_storable'`).

None of these affect the running application or this module's own suite.