"""Create a real invoice for live HTTP testing of the signing page.

COMMIT is intentional - the point is to have a URL a running server can serve.
Pair with ops_test_invoice_signing_http.py, which cleans up afterwards.
"""
from odoo import Command

env = env(user=1)  # noqa: F821

partner = env['res.partner'].search([('name', '=', 'HTTP Sign Customer')], limit=1)
if not partner:
    partner = env['res.partner'].create({'name': 'HTTP Sign Customer', 'is_company': True})
product = env['product.product'].search([('name', '=', 'HTTP Sign Widget')], limit=1)
if not product:
    product = env['product.product'].create({
        'name': 'HTTP Sign Widget', 'type': 'consu', 'invoice_policy': 'order'})

inv = env['account.move'].search([('name', '=', '/TEST/HTTP/SIGN/0001')], limit=1)
if not inv:
    inv = env['account.move'].create({
        'move_type': 'out_invoice',
        'partner_id': partner.id,
        'name': '/TEST/HTTP/SIGN/0001',
        'invoice_line_ids': [Command.create({
            'product_id': product.id, 'quantity': 3, 'price_unit': 120.0})],
    })
    inv.action_post()

inv.action_request_signature()
token = inv.sudo()._portal_ensure_token()
env.cr.commit()

print(f'INVOICE_ID={inv.id}')
print(f'ACCESS_TOKEN={token}')
print(f'SIGN_URL=http://127.0.0.1:8069/my/invoices/{inv.id}/sign?access_token={token}')
print(f'PORTAL_URL=http://127.0.0.1:8069/my/invoices/{inv.id}')
