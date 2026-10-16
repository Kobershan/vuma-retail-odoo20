"""Re-create the monthly sales targets the demo seed lays down.

ops_check_views.py once dropped the real cognify_sales_target table by
mistake (its stand-in cleanup ran against an installed module), which
took every seeded target with it. The module update that followed
recreated the table empty. This puts the targets back without
touching any of the other demo data.

Safe to run again: a month a rep already has a target for is skipped,
which is also what the table's unique constraint now enforces.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_seed_targets.py
"""
import random
from datetime import date

from dateutil.relativedelta import relativedelta

env = env(user=1)  # noqa: F821
random.seed(20261014)

MONTHS = 6
from odoo import fields  # noqa: E402

TODAY = fields.Date.today()

# The same four people the demo seed creates and puts on the sales teams.
salespeople = env['res.users'].search(
    [('login', 'in', ['demo.sales.moyo', 'demo.sales.ncube',
                      'demo.sales.dube', 'demo.sales.kamau'])])
if not salespeople:
    raise SystemExit('no demo salespeople found: run ops_seed_demo_data.py first')

# A target is only meaningful next to what the person actually sells, so
# the goal is their own run rate: total sales over the seeded period
# divided by the number of months. Averaging over a short lookback would
# be noisier, because a rep only makes a handful of orders a month and
# one quiet month would halve their goal.
month_cursor = date(TODAY.year, TODAY.month, 1)
period_start = month_cursor - relativedelta(months=MONTHS - 1)
run_rate = {}
for rep in salespeople:
    past = env['sale.order'].search([
        ('user_id', '=', rep.id),
        ('state', '=', 'sale'),
        ('date_order', '>=', period_start),
        ('date_order', '<', month_cursor),
    ])
    total = sum(past.mapped('amount_total'))
    run_rate[rep.id] = (total / MONTHS) if total else 8000.0

created = 0
skipped = 0
for _ in range(MONTHS):
    for rep in salespeople:
        if env['cognify.sales.target'].search_count(
                [('user_id', '=', rep.id), ('date', '=', month_cursor)]):
            skipped += 1
            continue
        typical = run_rate[rep.id]
        # Nudged either side so the report shows a spread of people on
        # target and people short of it rather than everyone landing on 100%.
        env['cognify.sales.target'].create({
            'date': month_cursor,
            'user_id': rep.id,
            'target_revenue': round(typical * random.uniform(0.7, 1.1), 2),
            'target_units': round(typical / 12, 1),
            'target_margin': round(typical * random.uniform(0.2, 0.35), 2),
        })
        created += 1
    month_cursor -= relativedelta(months=1)

# The shell rolls the transaction back when the script ends, so the
# work has to be committed here to reach the database at all.
env.cr.commit()

print(f'  {created} sales targets created, {skipped} already set', flush=True)
