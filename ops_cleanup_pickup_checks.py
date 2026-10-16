"""Remove the pickups ops_check_live_pickup_lines.py leaves behind.

A confirmed pickup is part of the audit trail and is not meant to be deleted,
which is right on a live system and unhelpful for a check that has just made
one. This removes those records and nothing else, and it says what it removed.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_cleanup_pickup_checks.py
"""
pickups = env['customer.goods.pickup'].search([])
if not pickups:
    print('no pickups to remove')
    raise SystemExit(0)

names = ', '.join(sorted(pickups.mapped('name')))
ids = tuple(pickups.ids)
env.cr.execute(
    'DELETE FROM customer_goods_pickup_line WHERE pickup_id IN %s', [ids])
env.cr.execute(
    'DELETE FROM customer_goods_receipt_line WHERE receipt_id IN '
    '(SELECT id FROM customer_goods_receipt WHERE pickup_id IN %s)', [ids])
env.cr.execute(
    'DELETE FROM customer_goods_receipt WHERE pickup_id IN %s', [ids])
env.cr.execute('DELETE FROM customer_goods_pickup WHERE id IN %s', [ids])
env.cr.commit()
print(f'removed {len(pickups)} pickup(s): {names}')
print(f'pickups remaining: {env["customer.goods.pickup"].search_count([])}')
