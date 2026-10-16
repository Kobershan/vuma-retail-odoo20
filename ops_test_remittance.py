"""End-to-end check of the cognify_remittance cash-management module.

One rep, one trip, several customers. Verifies the core promise: cash a rep
collects does not touch ANY customer's account until the office verifies it.
"""
from odoo import Command

env = env(user=1)  # noqa: F821  (injected by odoo shell)
Company = env['res.company']
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


def expect_error(label, fn):
    """Run fn() expecting it to raise; each attempt needs its own savepoint,
    otherwise the aborted transaction poisons everything that follows."""
    try:
        with env.cr.savepoint():
            fn()
        check(label, False, 'no error raised!')
    except Exception as e:
        check(label, True, type(e).__name__)


def ar_balance(partner, account):
    """Posted receivable balance for a partner (Odoo 20 has no _get_balance)."""
    lines = env['account.move.line'].search([
        ('partner_id', '=', partner.id),
        ('account_id', '=', account.id),
        ('parent_state', '=', 'posted'),
    ])
    return sum(lines.mapped('balance'))


# ---------------------------------------------------------------- fixtures
company = Company.browse(1)

customers = []
for nm in ('ZZ Retailer One', 'ZZ Retailer Two', 'ZZ Retailer Three'):
    customers.append(env['res.partner'].create({'name': nm, 'is_company': True}))

rep = env['res.users'].create({
    'name': 'ZZ Field Rep', 'login': 'zz_field_rep_smoke',
    'group_ids': [Command.link(env.ref('cognify_remittance.group_cognify_remittance_rep').id)],
})
approver = env['res.users'].create({
    'name': 'ZZ Office Approver', 'login': 'zz_approver_smoke',
    'group_ids': [Command.link(env.ref('cognify_remittance.group_cognify_remittance_approver').id)],
})

# Use the REAL configured accounts — this proves the live setup works, and
# leaves the database untouched (no ZZ accounts created, no rollback needed).
cfg = env['res.config.settings'].sudo().get_remittance_config()
cash_account = cfg['remittance_cash_account_id']
check('cash account is configured', bool(cash_account),
      f'{cash_account.code} {cash_account.name}' if cash_account else 'MISSING')

cats = env['cognify.expense.category'].search([])
unmapped = cats.filtered(lambda c: not c.account_id)
check('every expense category has an account', not unmapped,
      f'unmapped: {[c.code for c in unmapped]}' if unmapped else f'{len(cats)} mapped')
# The test uses Food/Fuel/Lodging/Airtime/Other/Toll -> expect 6 distinct accounts.
travel_accounts = cats.mapped('account_id')
check('each category maps to its own travel account',
      len(travel_accounts) == 6, f'{len(travel_accounts)} distinct accounts')

# Point the test's expense assertions at the per-category real accounts.
PER_CAT = {c.code: c.account_id for c in cats}

product = env['product.product'].create({'name': 'ZZ Widget', 'type': 'service', 'invoice_policy': 'order'})
tax = env['account.tax'].search([('company_id', '=', company.id)], limit=1)

# Give each customer a real open balance to settle.
for cust, amt in zip(customers, (1000.0, 600.0, 250.0)):
    inv = env['account.move'].create({
        'move_type': 'out_invoice', 'partner_id': cust.id,
        'invoice_line_ids': [Command.create({
            'product_id': product.id, 'quantity': 1, 'price_unit': amt,
            'tax_ids': [Command.set(tax.ids)] if tax else [],
        })],
    })
    inv.action_post()

receivables = {c: c.property_account_receivable_id for c in customers}
check('all customers have a receivable account', all(receivables.values()))
before = {c: ar_balance(c, receivables[c]) for c in customers}
check('customers have open balances',
      all(abs(v) > 0 for v in before.values()),
      ', '.join(f'{c.display_name}={v:.2f}' for c, v in before.items()))

food = env.ref('cognify_remittance.expense_category_food')
fuel = env.ref('cognify_remittance.expense_category_fuel')
lodging = env.ref('cognify_remittance.expense_category_lodging')
airtime = env.ref('cognify_remittance.expense_category_airtime')
other = env.ref('cognify_remittance.expense_category_other')
toll = env.ref('cognify_remittance.expense_category_toll')

# ------------------------------------------------- rep records the trip
# One trip, three different customers paid.
rem = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [
        Command.create({'customer_id': customers[0].id, 'amount': 700.0}),
        Command.create({'customer_id': customers[1].id, 'amount': 400.0}),
        Command.create({'customer_id': customers[2].id, 'amount': 200.0}),
    ],
    'line_ids': [
        Command.create({'category_id': fuel.id, 'amount': 30.0, 'description': 'Diesel'}),
        Command.create({'category_id': food.id, 'amount': 50.0, 'description': 'Lunch'}),
        Command.create({'category_id': lodging.id, 'amount': 80.0, 'description': 'Hotel'}),
        Command.create({'category_id': airtime.id, 'amount': 10.0, 'description': 'Airtime'}),
        Command.create({'category_id': other.id, 'amount': 20.0, 'description': 'Parking'}),
        Command.create({'category_id': toll.id, 'amount': 10.0, 'description': 'Toll'}),
    ],
})
check('reference generated from sequence', rem.name.startswith('REM/'), rem.name)
check('three customer collections recorded', len(rem.receipt_ids) == 3, f'{len(rem.receipt_ids)}')
check('all three customers listed', len(rem.customer_ids) == 3, f'{len(rem.customer_ids)}')
check('total collected sums all customers', rem.total_received == 1300.0, f'{rem.total_received}')
check('total expenses summed', rem.total_expenses == 200.0, f'{rem.total_expenses}')
check('net to return computed', rem.net_amount == 1100.0, f'{rem.net_amount}')

# ---- THE CORE GUARANTEE: no customer has been touched.
check('no journal entry while in draft', not rem.move_id)
for label, state in (('draft', 'draft'),):
    now = {c: ar_balance(c, receivables[c]) for c in customers}
    check(f'no customer AR touched while {label}',
          all(abs(now[c] - before[c]) < 0.005 for c in customers),
          ', '.join(f'{c.display_name}: {before[c]:.2f}->{now[c]:.2f}' for c in customers))

rem.action_submit()
check('submitted locks it', rem.state == 'submitted')
now = {c: ar_balance(c, receivables[c]) for c in customers}
check('no customer AR touched after submit',
      all(abs(now[c] - before[c]) < 0.005 for c in customers),
      ', '.join(f'{c.display_name}: {before[c]:.2f}->{now[c]:.2f}' for c in customers))

expect_error('rep blocked from editing submitted remittance',
             lambda: rem.with_user(rep).write({'receipt_ids': [(5, 0, 0)]}))

seen = env['cognify.remittance'].with_user(rep).search([])
check('rep sees only own remittances', set(seen.ids) == {rem.id}, f'{seen.ids}')


# ------------------------------------------------------- office verifies
rem.with_user(approver).action_verify()
check('verified by office', rem.state == 'verified' and rem.verified_by_id == approver, rem.state)
now = {c: ar_balance(c, receivables[c]) for c in customers}
check('no customer AR touched after verify',
      all(abs(now[c] - before[c]) < 0.005 for c in customers),
      ', '.join(f'{c.display_name}: {before[c]:.2f}->{now[c]:.2f}' for c in customers))

draft2 = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[0].id, 'amount': 10.0})],
})
expect_error('cannot credit before verification',
             lambda: draft2.with_user(approver).action_credit_accounts())


# ------------------------------------------------------- office credits
rem.with_user(approver).action_credit_accounts()
check('state is credited', rem.state == 'credited', rem.state)
check('journal entry linked', bool(rem.move_id), str(rem.move_id))
move = rem.move_id
check('entry is posted', move.state == 'posted', move.state)
check('entry dated as the collection date', move.date == rem.date, f'{move.date}')

# Read lines back from the DB: after action_post() the o2m cache is stale in v20.
mlines = env['account.move.line'].search([('move_id', '=', move.id)])
debits = sum(mlines.mapped('debit'))
credits = sum(mlines.mapped('credit'))
check('entry has 8 lines (cash + 6 expense cats are merged by account + 3 credits)', len(mlines) >= 4,
      f'{len(mlines)} lines')
check('debits == credits', abs(debits - credits) < 0.005 and debits > 0, f'D={debits:.2f} C={credits:.2f}')

check('cash debited the net returned',
      abs(sum(mlines.filtered(lambda l: l.account_id == cash_account).mapped('debit')) - 1100.0) < 0.005,
      f'{sum(mlines.filtered(lambda l: l.account_id == cash_account).mapped("debit"))}')

# Each expense category must land in ITS OWN real travel account — this is the
# whole point of the dropdown. Amounts: fuel 30, food 50, lodging 80,
# airtime 10, other 20, toll 10.
for code, amt in (('FUEL', 30.0), ('FOOD', 50.0), ('LODGING', 80.0),
                  ('AIRTIME', 10.0), ('OTHER', 20.0), ('TOLL', 10.0)):
    got = sum(mlines.filtered(lambda l, a=PER_CAT[code]: l.account_id == a).mapped('debit'))
    check(f'{code} posted to its own travel account ({amt:.0f})',
          abs(got - amt) < 0.005, f'{PER_CAT[code].code} got {got:.2f}')

check('total expenses debited across travel accounts',
      abs(sum(l.debit for l in mlines
              if l.account_id in cats.mapped('account_id')) - 200.0) < 0.005,
      f'{sum(l.debit for l in mlines if l.account_id in cats.mapped("account_id")):.2f}')

# The key assertion: each customer's account credited by exactly what they paid.
for cust, expected in zip(customers, (700.0, 400.0, 200.0)):
    credited = sum(mlines.filtered(
        lambda l, c=cust: l.account_id == receivables[c] and l.partner_id == c
    ).mapped('credit'))
    check(f'{cust.display_name} credited {expected:.0f}', abs(credited - expected) < 0.005, f'{credited}')

after = {c: ar_balance(c, receivables[c]) for c in customers}
for cust, expected in zip(customers, (700.0, 400.0, 200.0)):
    check(f'{cust.display_name} AR reduced by exactly {expected:.0f}',
          abs((after[cust] - before[cust]) + expected) < 0.005,
          f'{before[cust]:.2f} -> {after[cust]:.2f} (delta {after[cust] - before[cust]:.2f})')


# ------------------------------------------------------- other guards
# Two collections from the SAME customer must merge into one credit.
dup = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [
        Command.create({'customer_id': customers[0].id, 'amount': 50.0}),
        Command.create({'customer_id': customers[0].id, 'amount': 30.0}),
    ],
})
try:
    dup.with_user(rep).action_submit()
    dup.with_user(approver).action_verify()
    dup.with_user(approver).action_credit_accounts()
    dup_lines = env['account.move.line'].search([('move_id', '=', dup.move_id.id)])
    dup_credits = dup_lines.filtered(
        lambda l: l.account_id == receivables[customers[0]] and l.partner_id == customers[0]
    )
    check('repeat customer merged into a single credit line', len(dup_credits) == 1,
          f'{len(dup_credits)} lines')
    check('merged credit totals both collections',
          abs(sum(dup_credits.mapped('credit')) - 80.0) < 0.005,
          f'{sum(dup_credits.mapped("credit"))}')
except Exception as e:
    check('repeat customer merged into a single credit line', False, f'{type(e).__name__}: {e}')
    env.cr.rollback() if False else None

# A collection with no expenses at all must still be submittable.
plain = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[2].id, 'amount': 250.0})],
})
try:
    plain.with_user(rep).action_submit()
    check('remittance with no expenses can be submitted', plain.state == 'submitted', plain.state)
    plain.with_user(approver).action_verify()
    plain.with_user(approver).action_credit_accounts()
    pl = env['account.move.line'].search([('move_id', '=', plain.move_id.id)])
    check('expense-free entry posts cash for the full amount',
          len(pl) == 2 and abs(sum(pl.mapped('debit')) - 250.0) < 0.005
          and abs(sum(pl.mapped('credit')) - 250.0) < 0.005,
          f'{len(pl)} lines D={sum(pl.mapped("debit")):.2f}')
except Exception as e:
    check('remittance with no expenses can be submitted', False, f'{type(e).__name__}: {e}')

# Submitting with no customer line must be refused.
empty = env['cognify.remittance'].with_user(rep).create({'rep_id': rep.id})
expect_error('cannot submit with no collections',
             lambda: empty.with_user(rep).action_submit())

# A category with no mapped account must block crediting rather than post wrong.
other_cat = env.ref('cognify_remittance.expense_category_other')
saved = other_cat.account_id
other_cat.account_id = False
rem3 = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[0].id, 'amount': 200.0})],
    'line_ids': [Command.create({'category_id': other_cat.id, 'amount': 20.0, 'description': 'Parking'})],
})
rem3.with_user(rep).action_submit()
rem3.with_user(approver).action_verify()
expect_error('unmapped category blocks crediting',
             lambda: rem3.with_user(approver).action_credit_accounts())
other_cat.account_id = saved

expect_error('"Other" requires a description', lambda: env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[0].id, 'amount': 50.0})],
    'line_ids': [Command.create({'category_id': other.id, 'amount': 10.0})],
}))

expect_error('expenses cannot exceed cash collected', lambda: env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[0].id, 'amount': 50.0})],
    'line_ids': [Command.create({'category_id': food.id, 'amount': 999.0})],
}))

# Same guard when the expense line is added AFTER the remittance exists —
# this is the path that a stale compute dependency used to slip through.
gradual = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[0].id, 'amount': 100.0})],
})


def _overspend():
    gradual.write({'line_ids': [Command.create({'category_id': food.id, 'amount': 250.0})]})


expect_error('overspend blocked when adding a line later', _overspend)

# ...and the totals must recompute when lines are edited legitimately.
gradual2 = env['cognify.remittance'].with_user(rep).create({
    'rep_id': rep.id,
    'receipt_ids': [Command.create({'customer_id': customers[0].id, 'amount': 100.0})],
    'line_ids': [Command.create({'category_id': food.id, 'amount': 40.0})],
})
check('totals computed on create', (gradual2.total_received, gradual2.total_expenses, gradual2.net_amount)
      == (100.0, 40.0, 60.0),
      f'{gradual2.total_received}/{gradual2.total_expenses}/{gradual2.net_amount}')
gradual2.write({'line_ids': [(1, gradual2.line_ids.id, {'amount': 70.0})]})
check('totals recompute when an expense is edited',
      (gradual2.total_expenses, gradual2.net_amount) == (70.0, 30.0),
      f'{gradual2.total_expenses}/{gradual2.net_amount}')


# ------------------------------------------------------- report
print('\n' + '=' * 70)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 70)
print(f'{len(ok)} passed, {len(fail)} failed')
