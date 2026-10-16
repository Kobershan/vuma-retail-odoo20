"""Remove everything ops_seed_demo_data.py created.

Everything the seeder makes is marked with the ``DEMO`` prefix (products,
categories, partners, users, leave types) or is reachable from those, so this
walks outwards from the marked records. It is meant for a demo/dev database.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \\
        --http-port=8199 --log-level=error < ops_cleanup_demo_data.py
"""
env = env(user=1)  # noqa: F821

DEMO = 'DEMO'
removed = {}


def drop(label, records):
    """Delete records, tolerating any that have already gone.

    Records are searched for at the start and deleted at the end, and plenty
    can disappear in between through a cascade or an ondelete rule. Browsing
    the ids again and deleting one at a time means a record that has already
    gone is skipped rather than aborting the whole cleanup.
    """
    ids = records.ids
    if not ids:
        removed[label] = removed.get(label, 0) + 0
        return 0
    model = records._name
    for record_id in ids:
        record = env[model].browse(record_id).exists()
        if not record:
            continue
        try:
            with env.cr.savepoint():
                record.unlink()
        except Exception:  # noqa: BLE001 - demo cleanup, best effort
            continue
    removed[label] = removed.get(label, 0) + len(ids)
    return len(ids)


def like(text):
    return [('name', 'like', f'%{DEMO}%')] if isinstance(text, str) else []


# ---------------------------------------------------------------------------
# Find the demo records
# ---------------------------------------------------------------------------
categories = env['product.category'].search(like(f'{DEMO} '))
products = env['product.product'].search(like(f'{DEMO} '))
partners = env['res.partner'].search(like(f'{DEMO} '))
users = env['res.users'].search([('login', 'like', 'demo.%')])
employees = env['hr.employee'].search([('user_id', 'in', users.ids)])
work_entry_types = env['hr.work.entry.type'].search([('code', 'in', ['LEAVE200', 'LEAVE201'])])

orders = env['purchase.order'].search([('partner_id', 'in', partners.ids)])
sales = env['sale.order'].search([('partner_id', 'in', partners.ids)])
invoices = env['account.move'].search([
    ('move_type', 'in', ['out_invoice', 'out_refund', 'in_invoice', 'in_refund']),
    ('partner_id', 'in', partners.ids),
])
payments = env['account.payment'].search([('partner_id', 'in', partners.ids)])
pickings = env['stock.picking'].search([
    '|', ('partner_id', 'in', partners.ids),
    ('origin', 'in', sales.mapped('name') + orders.mapped('name')),
])
leaves = env['hr.leave'].search([
    '|', ('employee_id', 'in', employees.ids),
    ('work_entry_type_id', 'in', work_entry_types.ids),
])
attendances = env['hr.attendance'].search([('employee_id', 'in', employees.ids)])
expenses = env['hr.expense'].search([('employee_id', 'in', employees.ids)])
timesheets = env['account.analytic.line'].search([
    ('employee_id', 'in', employees.ids)])
targets = env['cognify.sales.target'].search([('user_id', 'in', users.ids)])
analytic_accounts = env['account.analytic.account'].search([
    ('name', '=', 'DEMO Rollout Project')])
print(f'found: {len(products)} products, {len(partners)} partners, {len(sales)} sales, '
      f'{len(invoices)} invoices, {len(attendances)} attendances, {len(leaves)} leaves')
print(f'        {len(expenses)} expenses, {len(timesheets)} timesheet entries, '
      f'{len(targets)} sales targets')

# ---------------------------------------------------------------------------
# Cancel before deleting: nothing confirmed can go
# ---------------------------------------------------------------------------
for invoice in invoices:
    if invoice.state == 'posted':
        invoice.button_draft()
    if invoice.state == 'cancel':
        continue
for invoice in invoices:
    if invoice.state not in ('cancel',):
        try:
            invoice.button_cancel()
        except Exception:  # noqa: BLE001
            pass
# A posted payment must be brought back to draft before it can be removed.
for payment in payments:
    if payment.state == 'posted':
        payment.action_draft()
for payment in payments:
    if payment.state not in ('draft', 'cancel'):
        try:
            payment.action_cancel()
        except Exception:  # noqa: BLE001
            pass
# Completed transfers and their moves: Odoo refuses to delete them through the
# ORM on purpose, so they come out by id. There is no bill for them (the
# payments were already gone), so nothing is left pointing at them.
for picking in env['stock.picking'].search([
        ('id', 'in', env['stock.move'].search(
            [('product_id', 'in', products.ids)]).picking_id.ids)]):
    if picking.state == 'done':
        moves = picking.move_ids
        env.cr.execute('DELETE FROM stock_move_line WHERE move_id IN %s', (tuple(moves.ids),))
        env.cr.execute('DELETE FROM stock_move WHERE id IN %s', (tuple(moves.ids),))
        env.cr.execute('DELETE FROM stock_picking WHERE id = %s', (picking.id,))
        removed['transfers (completed)'] = removed.get('transfers (completed)', 0) + 1

for order in sales:
    if order.state not in ('cancel', 'done'):
        try:
            order.action_cancel()
        except Exception:  # noqa: BLE001
            pass
for order in orders:
    if order.state not in ('cancel', 'done'):
        try:
            order.button_cancel()
        except Exception:  # noqa: BLE001
            pass
for picking in pickings:
    # A completed transfer cannot be cancelled; it is dealt with by id below.
    if picking.state in ('cancel', 'done'):
        continue
    try:
        picking.action_cancel()
    except Exception:  # noqa: BLE001
        pass

# ---------------------------------------------------------------------------
# Delete, children first
# ---------------------------------------------------------------------------
drop('payment wizards', env['account.payment.register'].search([
    ('partner_id', 'in', partners.ids)]))
drop('payments', payments)
drop('invoices', invoices)
drop('sale orders', sales)
drop('purchase orders', orders)
drop('transfers', pickings)

drop('attendances', attendances)
drop('time off', leaves)
# An approved allocation cannot simply be deleted, so they are refused first,
# which is the reversal Odoo allows.
allocations = env['hr.leave.allocation'].search([
    ('employee_id', 'in', employees.ids)])
for allocation in allocations:
    if allocation.state != 'refuse':
        try:
            allocation.action_refuse()
        except Exception:  # noqa: BLE001
            pass
drop('leave allocations', allocations)
# The plan name is translated and stored as jsonb, which the ORM cannot put in
# a domain, so the ids are filtered here and browsed back into a recordset.
plan_ids = [
    plan.id for plan in env['hr.leave.accrual.plan'].search([])
    if (plan.name or '').startswith(f'{DEMO} ')]
drop('accrual plans', env['hr.leave.accrual.plan'].browse(plan_ids))

# Timesheets hang off the project, so they go before it.
drop('timesheet entries', timesheets)
drop('expenses', expenses)
drop('sales targets', targets)
drop('quants', env['stock.quant'].search([('product_id', 'in', products.ids)]))
drop('employees', employees)
drop('users', users)
drop('partners', partners)
drop('products', products)
drop('categories', categories)
drop('work entry types', work_entry_types)

env['ir.config_parameter'].sudo().set_str('cognify_demo_data.seeded_at', '')
env.cr.commit()

print('\n' + '=' * 52)
for label, count in sorted(removed.items()):
    print(f'  removed {count:6d}  {label}')
print('=' * 52)
print('committed')