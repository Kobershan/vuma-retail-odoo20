# Management Reports & Dashboards — Odoo 20 setup & usage

Module: `custom-addons/cognify_management_reports`

Everything below has already been applied to the `vuma_retail` database.

---

## Where the numbers come from

| Kind of figure | Read from | Why |
|---|---|---|
| Revenue, cost, margin, turnover | **posted invoices / confirmed orders** | What has actually been billed and what it cost |
| Customer balances, credit | **posted invoices** | What is owed, what is late |
| Order counts, quotations, pipeline | **orders** | A quotation is not revenue but it is work in hand |
| Money in and out, balances | **general ledger, bank and cash journals** | What actually moved, not what was invoiced |
| Stock on hand, value, dead lines | **stock quants and the general ledger** | Money tied up in what is on the shelf |
| Hours, overtime, late arrivals | **attendance** | Clock-in and clock-out pairs |
| Time off taken and left | **approved requests** | Booked and approved time only |
| Expense claims | **approved expenses** | A claim still waiting on a manager is not yet a cost |
| Billable hours, project cost | **analytic lines** | Time recorded against what it was charged at |
| Revenue against target | **confirmed order lines** | What the rep sold, against the goal agreed |

Every report is an ordinary Odoo view, so it drills down: click a month, get
the orders; click a customer, get their invoices.

---

## What is there

### Dashboards — the *Dashboards* menu, **Management** group

Seven dashboards, each built from pivots on the report actions below. They read
Odoo **live** — there is no snapshot, so a dashboard cannot drift from the books.

| Dashboard | Shows |
|---|---|
| **Management Overview** | Sales this month vs last, cost of sales, gross profit, sales today, open quotations; sales and margin by month; sales by category and by channel |
| **Sales and Products** | Revenue, units, cost, gross profit; best sellers and slowest movers by revenue; revenue by category; salesperson by month |
| **Customers** | Invoiced, outstanding, overdue, credit available; largest balances; overdue by customer; sales by customer; balances by sales team |
| **Staff** | Hours worked, overtime, late arrivals, time off taken, leave available; hours and overtime by employee; time off by month; leave still available |
| **Finance and Cash** | Money in, money out, net movement, unreconciled; the five ageing bands; money in/out and closing balance by month; largest balances by customer |
| **Stock** | Value at cost, at retail, margin in stock, units on hand, value in problem lines; value by category; status split; dead and slow movers; low and out of stock |
| **Targets and People** | Revenue, target, still to go, attainment %; expenses claimed, claims, hours, billable hours, timesheet coverage; revenue against target, attainment by person, expenses by person, billable share by person |

### Reports — the *Management* menu

Grouped under seven submenus:

| Menu | Report | Backed by |
|---|---|---|
| Sales → Sales Overview | Month by month: revenue, cost, margin | `sale.report` |
| Sales → Sales by Day / by Month | Daily and monthly trend | `sale.report` |
| Sales → Sales by Channel | By sales team, over months | `sale.report` |
| Sales → Sales by Category | By product category, over months | `sale.report` |
| Sales → Cost of Sales | Revenue, purchase cost, margin and margin % by category | `sale.report` |
| Sales → Pipeline (Quotations) | Open quotations by salesperson | `sale.report` |
| Products → Product Turnover | Ranked by revenue | `sale.report` |
| Products → Slowest Moving | The same, ranked the other way | `sale.report` |
| Customers → Outstanding Balances | Owed, overdue, days late, credit limit, credit available | `cognify.customer.credit` |
| Customers → Credit Available | Headroom per customer | `cognify.customer.credit` |
| Customers → Sales by Customer | Revenue per customer over months | `sale.report` |
| Customers → Debt Ageing | Outstanding split into five bands, overdue and overdue % | `cognify.customer.ageing` |
| Finance → Cash and Bank Position | Money in, money out, net, opening and closing balance, unreconciled, per account per month | `cognify.cash.position` |
| Stock → Stock Health and Value | Value at cost and retail, margin, movement, cover, status | `cognify.stock.health` |
| Stock → Dead and Slow Moving Stock | The same, filtered to lines that are not moving | `cognify.stock.health` |
| Stock → Low and Out of Stock | The same, filtered to lines about to run out | `cognify.stock.health` |
| Salespeople → Salesperson by Month | Revenue and margin per person per month | `sale.report` |
| Salespeople → Sales Targets | Targets per rep per month — the one editable report | `cognify.sales.target` |
| Salespeople → Achievement Against Target | Revenue, target, attainment %, shortfall, on target | `cognify.rep.achievement` |
| Staff → Hours Worked | Shifts, hours, overtime, late arrivals by month | `cognify.staff.report` |
| Staff → Leave Taken and Available | Entitlement, taken, available, short paid, exhausted | `cognify.staff.report` |
| Staff → Expenses | Approved claims per person per month, and against revenue | `cognify.expense.report` |
| Staff → Timesheet Utilisation | Hours, billable hours, cost, revenue, timesheet coverage | `cognify.utilisation.report` |

`cognify.sales.target` is the only report you can add to. Everything else is
read-only, because it is a SQL view rather than a table.

### Settings

*Settings → Employees → Management Reports:*

* **Dead Stock After** (default 90 days). No sale in this long, while stock is
  still held, and the line is dead.
* **Slow Mover Threshold** (default 5 units). Fewer units sold over that period
  makes a line that is selling a slow one.

There is deliberately **no annual leave entitlement setting**. Entitlement is
read from the time off allocations HR actually granted, so there is no second
figure to keep in step with reality.

---

## Commands

```bash
cd /home/kob/Documents/vuma-retail

# Install
./venv/bin/python odoo/odoo-bin module install -c odoo.conf -d vuma_retail \
    cognify_management_reports

# Check every SQL view builds, without touching the module
./venv/bin/python ops_check_views.py

# Run the module's tests (rolls back, leaves no data)
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail \
    -u cognify_management_reports --stop-after-init \
    --test-enable --test-tags=/cognify_management_reports \
    --workers=0 --http-port=8199

# End to end against the running server (needs it up)
./venv/bin/python ops_test_management_reports_live.py

# Rebuild the dashboard files (see "How the dashboards work")
./venv/bin/python ops_build_dashboards.py

# Dashboard checks. Run these after every rebuild -- each covers something the
# others cannot, and the #ERROR class of bug slips past all of them individually.

# Import every dashboard through the real o-spreadsheet engine
node ops_check_dashboards.mjs

# Walk the render path (figures and charts), not just the import path
node ops_check_dashboard_render.mjs

# Same, against what the server actually serves rather than the files on disk
./venv/bin/python ops_fetch_served_dashboards.py   # writes /tmp/opencode/served
node ops_check_live_render.mjs /tmp/opencode/served

# Every domain through Odoo's own py interpreter, the way the browser runs it
node ops_check_domains.mjs

# Every pivot and chart read_group, against the running server
./venv/bin/python ops_check_chart_data.py

# Re-seed the sales targets
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_seed_targets.py

# Demo data: six months of trading, staff, attendance and leave
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_seed_demo_data.py

# Put every financial record back into the company's currency, after the demo
# data went in while the company's was set to something else
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_repair_company_currency.py

# What is in a currency other than the company's, across the registry
./venv/bin/python ops_audit_currencies.py

# Remove it again
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_cleanup_demo_data.py
```

### Changing the Python needs a restart

A module upgrade reloads Odoo's registry but **not** the addon's Python: the
workers cache the imported module, so `init()` keeps building the old view.
Anything that changes a `models/*.py` file — a new column, a changed `COALESCE`,
a new help string — only takes effect after the server restarts:

```bash
sudo systemctl restart odoo.service
```

Views, actions, menus and settings XML are picked up by the upgrade alone.

A running server can upgrade itself over RPC, which is what the *Apps* list's
Upgrade button does, and needs no restart:

```python
env['ir.module.module'].search([('name', '=', 'cognify_management_reports')]) \
    .button_immediate_upgrade()
```

---

## Demo data

The database had no trading history at all, so every report would have been
empty. `ops_seed_demo_data.py` builds six months of realistic activity:
27 products in 5 categories, 14 customers (several with a credit limit), 4
salespeople in 2 teams, ~57 sale orders with real deliveries, 33 invoices
(19 settled by real payments, 9 open, 5 overdue), 6 employees with 745
attendance records and 32 time-off requests.

`ops_seed_targets.py` then sets 24 monthly targets across the four
salespeople, which is what makes *Achievement Against Target* and the
*Targets and People* dashboard say anything.

Everything it creates is prefixed `DEMO`, so `ops_cleanup_demo_data.py` can
remove all of it. **It is for a demo database, not a live one.**

It also switches on credit limits for the company, because the customer credit
report is blank without them.

---

## How this is built, and why

**Most reports are pivots on `sale.report`.** Odoo already maintains that view
of every sale order line with revenue, cost and margin on it, and it has every
dimension needed — date, product, category, partner, team, salesperson. The
module adds saved actions arranged the way a manager asks the question, and
nothing is recomputed. That keeps the reports fast, consistent with the Sales
list, and impossible to drift from the core figures.

Note that in Odoo 20 `sale.report` is **no longer a database view**: it is built
on demand from a query Odoo assembles at runtime. A SQL view that wants to read
it has to go back to `sale_order_line` instead, which is what the achievement
view does.

**The rest are SQL views**, because a pivot cannot do what they need:

| View | Why it cannot be a pivot |
|---|---|
| `cognify.customer.credit` | `res.partner.credit` is computed and not stored, so it can be neither grouped nor summed |
| `cognify.customer.ageing` | The ageing split has to be worked out per invoice, which no grouped field on a partner gives |
| `cognify.cash.position` | Reads by journal and month, not by partner, and the counterparties have to be excluded |
| `cognify.stock.health` | Joins quants, locations, sales and product cost into one row per variant |
| `cognify.staff.report` | Attendance and leave live in two unrelated tables |
| `cognify.expense.report` | Only approved claims count, which is a filter on state per line |
| `cognify.utilisation.report` | Hours, cost and billed revenue come from three different places |
| `cognify.rep.achievement` | Left join of sales against a target table that may have no row at all |

**Money fields on the view models declare a currency field.** A model built on a
SQL view has no currency relationship for Odoo to infer, and a Monetary field
with no currency formats as a bare number. Most carry `company_currency_id`;
`cognify.cash.position` carries `currency_id`, which falls back to the
company's currency for a journal that has none of its own.

**A view's `id` must be the thing the row is about.** The ORM addresses a report
row by its `id`, so a view that emits two rows carrying one id loses one of them
silently. Three views here did exactly that, and the symptom was a number that
was quietly half the truth: `cognify.expense.report` joined its per-company
revenue onto a per-employee row on the month alone, so a month in which a second
company invoiced tripled the rows and the ORM kept one. `cognify.customer.ageing`
and `cognify.customer.credit` grouped their CTEs by `(partner, company)` and then
keyed the row on the partner, so a customer trading in two companies appeared
once with only one company's debt. All three now group by the entity the row is
about. `test_every_view_model_has_unique_ids` guards this and collects every
offender at once, because one broken view otherwise hides the next behind it.
`ops_find_duplicate_view_ids.py` checks the same thing against whatever data is
actually in the database.

---

## Things worth knowing

**A pivot's `rows` and `columns` are objects, not field names.** Each entry is
`{"fieldName": "..."}`. o-spreadsheet reads a dimension as
`fields[dimension.fieldName]`, so a bare string gives `fields[undefined]` and the
dimension comes back with no field, no display name and nothing to group by.
Nothing throws at import: the document loads, the figures appear, and every cell
reading that pivot renders as **#ERROR**. This is what made all seven dashboards
fail at once while every other check passed — the report models were fine, every
action resolved, every document imported cleanly, and all 45 domains evaluated.
`_dimension()` in `ops_build_dashboards.py` builds them, `validate()` rejects the
string form at build time, and
`tests/test_dashboards.py::test_pivot_dimensions_are_objects_with_a_field_name`
covers it in the module's own suite.

**A pivot's `filters` is a list, not a mapping.** Same failure, second place.
The runtime definition is built with

    this.filters = (definition.filters ?? []).map((filter) => ...)

and `??` only substitutes the empty list when the key is **absent**. A mapping
is kept as a mapping, `.map` is not a function on it, and building the runtime
definition throws — which the %ERROR catch turns into a load failure, so again
the tile reads #ERROR. `None` (absent) is fine, which is what 92 of the 112
pivots Odoo ships itself do, and the other 20 carry `[]`. The builder now emits
`[]`, `validate()` rejects the mapping and the module keeps
`test_pivot_filters_is_an_array`.

Both of these survived every check that looks at a model, an action, a domain or
the document shape, because both only break when o-spreadsheet *builds the
pivot's runtime definition* — which nothing but a browser does.

**Odoo 20 moved several fields.** These bit during the build and will bite
anyone extending these reports:

* `res_partner.credit_limit` is **company-dependent jsonb**, not a number.
  Read it as `(credit_limit ->> (company_id)::text)::float`.
* `res_company.account_use_credit_limit` is a plain boolean, but
  `res_partner.use_partner_credit_limit` no longer exists.
* `hr.leave.date_from` / `date_to` are **computed** from `request_date_from` /
  `request_date_to` ("should therefore never be set directly"). Writing them is
  silently ignored and every request collapses onto today.
* `hr.leave.type` is gone; time off is booked against `hr.work.entry.type`.
* `hr_employee.department_id` and `job_id` are related to the current
  `hr_version` row, not stored on the employee.
* `hr.leave.action_validate()` is private; the public one is `action_approve()`.
* A view's `id` column **must be unique per row**. Keying the staff report on
  the employee id repeats it once per month, and the ORM then reads one month's
  figures for all of them.
* `_sql_constraints` is **not evaluated** on a model in Odoo 20. Use
  `models.Constraint(...)`, or a `@api.constrains` method, or the rule is a
  silent no-op.

**Translated columns are jsonb, but only sometimes.** Odoo stores a translated
field as plain text until a translation has been written for it, and as jsonb
keyed by language afterwards. `account_journal.name` is jsonb here;
`res_partner.name` and `hr_employee.name` are not. Selecting one as the other
puts a raw `{"en_US": "Bank"}` in front of the user, so the views that select a
name read it either way:

```sql
COALESCE(to_jsonb(j.name) ->> 'en_US', j.name::text) AS journal_name
```

`to_jsonb()` is a no-op on a jsonb column and wraps a text one in quotes, so the
`->>` returns NULL and the fallback picks the text up.

**A Float field cannot be blank.** Odoo reads an empty number as `0.0` on the
way out of the ORM (`float(value or 0.0)`), so a NULL in a view is a blank no
user ever sees. A sentinel such as `-1` survives the read but is then summed
into every pivot and every `read_group` on the model, quietly dragging the
averages down. Wherever a number means "not applicable", the report carries a
Boolean beside it instead:

| Number | Flag | Means |
|---|---|---|
| `attainment` = 0 | `has_target` | No goal was set, as against falling short of one |
| `days_since_sale` = 0 | `never_sold` | Never sold at all, as against sold long ago |
| `weeks_cover` = 0 | — | Nothing is selling, so cover is infinite |

**A SQL view reads the database directly.** Anything the ORM has buffered but
not yet written is invisible to it, so flush before reading a report inside the
same transaction (`self.env.flush_all()`). Committed data — everything in
normal use — is unaffected.

**Leave available is read from the allocations, not from a setting.** Entitlement
is whatever HR granted that year in `hr.leave.allocation`, summed over the year
the month falls in. A leave request is "annual" when its time off type is one
that holds an allocation for that person, which is a structural test rather
than a guess from the type's name. **Leave Available** is entitlement less
annual leave taken from the start of the year to the end of that month, so it is
a running balance, and it does not restart in months nobody books leave. An
allocation on an accrual plan hands out days through its milestones, so the
entitlement here is the floor rather than the whole story.

**Overtime is per shift.** Anything past eight hours in one shift; a month's
hours minus eight would call a normal week overtime.

**Short paid days are days already booked.** Leave taken beyond the entitlement
is counted as short paid, because that is the decision a manager has already
made. *Leave Shortfall* is the other side of the same coin: leave approved for
later in the year that this year's entitlement cannot cover, which still has to
be paid or refused.

---

## Troubleshooting

**Everything is priced in dollars**

That is leftover seed data, not a configuration choice. The demo had gone in
while the company currency was still set to something else, and Odoo keeps the
currency a record was created in — it does not revisit existing rows when the
company's is changed later, and here `res_currency_rate` is empty, so it has
nothing to convert with either. `res.company.currency_id` is Rand and
`default_currency` on the dashboards already reports it correctly, so the orders
and invoices simply read as though the business had priced everything in
dollars.

`ops_repair_company_currency.py` moves every financial record back into the
company's currency and leaves the amounts alone. It will not touch a figure,
because on every seeded move line `amount_currency` already equals the
company-currency `balance` and `amount_residual_currency` already equals
`amount_residual` — which is what Odoo requires once a line's currency is the
company's. Odoo blocks changing the currency of a posted move through
`write()`, so the seeded rows go through SQL with the caches invalidated and
those invariants re-checked on both sides. It stops rather than writes anything
if they did not already hold.

`ops_audit_currencies.py` lists whatever may still be sitting in another
currency, model by model, so the same thing can be caught in future.

**Money shows as a bare number**

The currency field on that view is NULL. Check the row's `currency_id` (or
`company_currency_id`) — for `cognify.cash.position` that means the journal has
no currency *and* its company has none, which only happens on an unconfigured
company.

**A name column shows `{"en_US": "..."}`**

A view is selecting a translated column that is stored as jsonb without pulling
the language out. See the `to_jsonb()` note above.

**Attainment reads 0% for a rep**

Read *Target Set*. Zero with the flag off is "no goal was set", which is not the
same as having missed one. Set a target on *Sales Targets* and the percentage
appears.

**The staff report is empty**

It covers three years back and a year ahead. Check *Employees* exist and that
`hr.attendance` has check-in **and** check-out: hours are the difference
between the two, so an open shift counts as zero hours.

**Leave Available is zero for everyone**

There is no validated `hr.leave.allocation` for those employees this year. The
report reads entitlement from allocations rather than from a setting, so with
no allocation granted the entitlement is zero and so is the balance. Grant the
allocations under *Time Off → Allocations*.

**Annual leave is being counted as "other"**

A request is annual leave when its time off type holds an allocation for that
person. Book the request against the allocated type, or allocate that type.

**Credit available is blank for everyone**

The customer has no credit limit set. Credit limits live on
*Contacts → the customer → Credit Limit* and must be switched on for the
company (*Settings → Accounting*). Headroom is shown whenever a limit exists;
the *Credit Managed* column says whether Odoo enforces it.

**A dashboard is blank**

Check the filters at the top of the dashboard, and that the person opening it
can see the underlying records — customer balances need Sales access, the staff
dashboard needs HR access. A dashboard reads what its user can read. If one tile
in particular is empty, its chart points at an action that no longer exists;
`ops_test_management_reports_live.py` checks every one of them.

**The margin is zero**

Cost of sales comes from the product's cost price as it was when the order was
confirmed. Set a cost on the product, or use landed costs.

---

## Files

```
custom-addons/cognify_management_reports/
├── models/
│   ├── customer_credit.py     # cognify.customer.credit     (SQL view)
│   ├── customer_ageing.py     # cognify.customer.ageing     (SQL view)
│   ├── cash_position.py       # cognify.cash.position       (SQL view)
│   ├── stock_health.py        # cognify.stock.health        (SQL view)
│   ├── staff_report.py        # cognify.staff.report        (SQL view)
│   ├── expense_report.py      # cognify.expense.report      (SQL view)
│   ├── utilisation_report.py  # cognify.utilisation.report  (SQL view)
│   ├── sales_target.py        # cognify.sales.target (a table) and
│   │                          #   cognify.rep.achievement   (SQL view)
│   └── res_config_settings.py # the three settings
├── views/                     # pivot/list views, actions, menus
├── data/
│   ├── dashboard_data.xml     # the seven dashboard records
│   └── files/*.json           # generated dashboard documents
├── security/ir.access.csv
└── tests/
    ├── test_management_reports.py       # sales, customers, credit
    └── test_finance_stock_reports.py    # cash, stock, targets, staff, time

ops_build_dashboards.py               # regenerates data/files/*.json
ops_check_views.py                    # builds every view against the database
ops_check_dashboards.mjs              # imports every dashboard, headless
ops_check_dashboard_render.mjs        # walks the render path, headless
ops_fetch_served_dashboards.py        # what the server serves, for the check above
ops_check_live_render.mjs             # render path against the served payloads
ops_check_domains.mjs                 # domains via Odoo's py interpreter
ops_check_chart_data.py               # every pivot/chart read_group, live
ops_seed_targets.py                   # the monthly sales targets
ops_seed_demo_data.py                 # six months of demo trading
ops_cleanup_demo_data.py              # removes it again
ops_repair_company_currency.py        # records left in a foreign currency
ops_audit_currencies.py               # currency fields vs the company's
ops_test_management_reports_live.py   # every report, over HTTP, end to end
ops_find_duplicate_view_ids.py        # report views reusing an id across rows
```

**Do not hand-edit `data/files/*.json`.** A dashboard is an o-spreadsheet
document, not a view. Change `ops_build_dashboards.py`, re-run it, and upgrade
the module.
