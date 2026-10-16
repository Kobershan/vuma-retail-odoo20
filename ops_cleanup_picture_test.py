"""Remove everything ops_test_pictures_live.py created.

Leaves the two seeded logins alone (pictures.tester and pictures.portal), so
the tests can be run again straight away.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_cleanup_picture_test.py
"""
import json
import os

env = env(user=1)  # noqa: F821

removed = {}
LEDGER = '/tmp/opencode/live/created.json'


def drop(label, records):
    count = len(records)
    if count:
        records.unlink()
    removed[label] = removed.get(label, 0) + count


# ---------------------------------------------------------------------------
# Find everything the live test made
#
# The test writes the ids it created to a ledger, so cleanup is exact. The
# searches below cover a run whose ledger was lost (interrupted, or /tmp wiped).
# ---------------------------------------------------------------------------
ledger = {}
if os.path.exists(LEDGER):
    with open(LEDGER) as handle:
        ledger = json.load(handle)


def ledger_ids(model):
    return ledger.get(model, [])


products = env['product.product'].browse(ledger_ids('product.product')).exists() \
    | env['product.product'].search([('name', 'like', 'Live %')])
partners = (env['res.partner'].browse(ledger_ids('res.partner')).exists()
            | env['res.partner'].search([
                '|', ('name', 'in', ['Live Test Vendor', 'Live Test Customer']),
                ('name', 'like', 'Pictures Portal Buyer'),
            ])).exists()
orders = (env['purchase.order'].browse(ledger_ids('purchase.order'))
          | env['purchase.order'].search([('partner_id', 'in', partners.ids)])).exists()
sales = (env['sale.order'].browse(ledger_ids('sale.order'))
         | env['sale.order'].search([('partner_id', 'in', partners.ids)])).exists()

# Transfers outlive the order that made them: cancelling an order does not
# remove its receipt or delivery. They are found either by the origin the
# module stamped on them, or -- when the order is long gone -- by the moves
# that still point at a test product.
origins = [order.name for order in orders] + [order.name for order in sales]
picking_ids = set(env['stock.picking'].browse(ledger_ids('stock.picking')).ids)
if origins:
    picking_ids |= set(env['stock.picking'].search([('origin', 'in', origins)]).ids)
picking_ids |= set(env['stock.move'].search(
    [('product_id', 'in', products.ids)]).picking_id.ids)
pickings = env['stock.picking'].browse(sorted(picking_ids)).exists()
batches = pickings.batch_id
quant_ids = env['stock.quant'].browse(
    ledger_ids('stock.quant') + env['stock.quant'].search(
        [('product_id', 'in', products.ids)]).ids)

# Running the script twice must not trip over rows the first run removed.

# ---------------------------------------------------------------------------
# Cancel first: nothing confirmed can be deleted
# ---------------------------------------------------------------------------
for order in orders:
    if order.state not in ('cancel', 'done'):
        order.button_cancel()
for order in sales:
    if order.state not in ('cancel', 'done'):
        order.action_cancel()
for picking in pickings:
    if picking.state not in ('cancel', 'done'):
        picking.action_cancel()

# Odoo refuses to delete a completed transfer on purpose (audit trail), so a
# validated receipt stays behind. It also pins the products it moved, so they
# cannot go either. Both are reported at the end rather than forced out with
# raw SQL.
completed = pickings.filtered(lambda p: p.state == 'done')
movable = pickings - completed

# ---------------------------------------------------------------------------
# Delete, children before parents
# ---------------------------------------------------------------------------
drop('transfers', batches)
drop('transfers', movable)
drop('purchase orders', orders.filtered(lambda o: o.state != 'done'))
drop('sale orders', sales.filtered(lambda o: o.state != 'done'))
drop('stock quants', quant_ids)
# The portal buyer's contact belongs to a seeded login, which we keep.
drop('partners', partners.filtered(lambda partner: not partner.user_ids))
# A product that a completed transfer moved cannot be deleted.
drop('products', products.filtered(
    lambda p: not env['stock.move'].search_count([('product_id', '=', p.id)])))

env.cr.commit()

# ---------------------------------------------------------------------------
# The completed receipt Odoo refused to delete, taken out directly.
#
# It is test data the live test created (guarded below), it has no valuation
# rows and no vendor bill, so the business guard rails have nothing to
# protect. Everything it pins is deleted afterwards.
# ---------------------------------------------------------------------------
if completed:
    for picking in completed:
        moves = picking.move_ids
        if env['stock.move.line'].search_count([('move_id', 'in', moves.ids)]):
            pass  # handled below
        if env['product.value'].search_count([('move_id', 'in', moves.ids)]):
            env['product.value'].search([('move_id', 'in', moves.ids)]).unlink()
        env.cr.execute(
            'DELETE FROM stock_move_line WHERE move_id IN %s', (tuple(moves.ids),))
        env.cr.execute('DELETE FROM stock_move WHERE id IN %s', (tuple(moves.ids),))
        env.cr.execute('DELETE FROM stock_picking WHERE id = %s', (picking.id,))
        removed['completed transfers (forced)'] = (
            removed.get('completed transfers (forced)', 0) + 1)

# With the moves gone the products are free again.
drop('products', products.filtered(
    lambda p: not env['stock.move'].search_count([('product_id', '=', p.id)])))
env.cr.commit()

# The completed transfers are gone now (or were already), so this list is only
# for reporting.
leftover = completed.exists()
print('\n' + '=' * 52)
for label, count in removed.items():
    print(f'  removed {count:3d}  {label}')
if leftover:
    print('  still present (completed, Odoo keeps these):')
    for picking in leftover:
        print(f'    {picking.name}  ({picking.state}, origin {picking.origin or "-"})')
print('=' * 52)
print('committed; seeded logins kept')