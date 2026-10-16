"""Seed a web login for end-to-end testing against the running server.

Creates (or re-arms) a dedicated test user with the rights the documents need:
purchasing, inventory and Settings. The password is written to
`.picturetesterpass` (mode 600), matching how this repo already stores
`.adminpass` / `.dbpass`.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_seed_picture_tester.py
"""
import os
import secrets

env = env(user=1)  # noqa: F821

LOGIN = 'pictures.tester'
NAME = 'Pictures Tester'
PASSWORD_FILE = '/home/kob/Documents/vuma-retail/.picturetesterpass'

# Groups the tester needs: buy, receive, pick, count, sell a delivery and
# change Settings. purchase_stock adds no group of its own in Odoo 20, so the
# picking side of purchasing comes from stock.group_stock_manager.
GROUPS = [
    'purchase.group_purchase_manager',
    'stock.group_stock_manager',
    'sales_team.group_sale_salesman_all_leads',
    'hr.group_hr_user',
    # The management reports put finance in front of the same people as stock
    # and sales, so the test login needs the accounting side too. Without it
    # the cash and debt reports are the only thing it could not open.
    'account.group_account_invoice',
    'base.group_system',
]
group_ids = [(6, 0, [env.ref(g).id for g in GROUPS])]

existing = env['res.users'].with_context(active_test=False).search([('login', '=', LOGIN)])
if existing:
    user = existing
    user.write({'name': NAME, 'group_ids': group_ids})
    print('re-armed existing login:', LOGIN)
else:
    user = env['res.users'].create({
        'name': NAME,
        'login': LOGIN,
        'group_ids': group_ids,
    })
    print('created login:', LOGIN)

password = secrets.token_urlsafe(12)
user.write({'password': password})

with open(PASSWORD_FILE, 'w') as handle:
    handle.write(password + '\n')
os.chmod(PASSWORD_FILE, 0o600)

env.cr.commit()

print('user id      :', user.id)
print('groups       :', ', '.join(sorted(user.all_group_ids.mapped('full_name'))))
print('password file:', PASSWORD_FILE)
print('committed')