"""FULL FLOW TEST, no external SMTP.

1. Create an invoice for a real customer address
2. Office requests a signature
3. Customer signs it through the portal endpoint (as a logged-out public user,
   via the real access token)
4. Office hits "Send & Print" to email the invoice
5. Odoo talks real SMTP to the local sink; we inspect what arrived

COMMITS so the running server can serve the portal page, and so the mail is
really queued.
"""
import base64
from odoo import Command

env = env(user=1)  # noqa: F821

CUSTOMER_EMAIL = 'customer@vuma-retail.test'

partner = env['res.partner'].search([('email', '=', CUSTOMER_EMAIL)], limit=1)
if not partner:
    partner = env['res.partner'].create({
        'name': 'Sink Test Customer', 'email': CUSTOMER_EMAIL, 'is_company': True})
product = env['product.product'].search([('name', '=', 'Sink Test Widget')], limit=1)
if not product:
    product = env['product.product'].create({
        'name': 'Sink Test Widget', 'type': 'consu', 'invoice_policy': 'order'})

inv = env['account.move'].search([('name', '=', '/TEST/MAIL/SIGN/0001')], limit=1)
if not inv:
    inv = env['account.move'].create({
        'move_type': 'out_invoice',
        'partner_id': partner.id,
        'name': '/TEST/MAIL/SIGN/0001',
        'invoice_line_ids': [Command.create({
            'product_id': product.id, 'quantity': 4, 'price_unit': 175.0})],
    })
    inv.action_post()

# 1. office asks for a signature
inv.action_request_signature()
token = inv.sudo()._portal_ensure_token()

# 2. customer signs via the portal, as a logged-out public visitor
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
       b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
       b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
SIG = base64.b64encode(PNG).decode()

from werkzeug.test import EnvironBuilder  # noqa: E402
from odoo.http._facade import HTTPRequest  # noqa: E402
from odoo.http.requestlib import Request as OdooRequest  # noqa: E402
from odoo.http import request_var  # noqa: E402
from odoo.addons.cognify_invoice_signature.controllers.portal import (  # noqa: E402
    InvoiceSignPortal,
)

b = EnvironBuilder(path='/my/invoices', base_url='http://127.0.0.1:8069')
r = OdooRequest(HTTPRequest(b.get_environ()))
public = env.ref('base.public_user')
r.env = env(user=public)
r.db = r.env.cr.dbname
r.registry = r.env.registry


class _S(dict):
    def __getattr__(self, k):
        return self.get(k)


r.session = _S()
request_var.set(r)

sign_res = InvoiceSignPortal().portal_invoice_sign_submit(
    inv.id, access_token=token, name='Sibusiso Ndlovu', signature=SIG)
env.invalidate_all()
inv.invalidate_recordset()
print('  signed by portal :', sign_res.get('success'), '|', inv.signed_by)

# 3. office emails the invoice (Send & Print)
send_wizard = env['account.move.send.wizard'].with_context(
    active_model='account.move', active_ids=inv.ids
).create({
    'move_id': inv.id,
    'sending_methods': ['email'],
    'sending_method_checkboxes': {'email': True},
    'mail_partner_ids': [(4, partner.id)],
    'template_id': env.ref('account.email_template_edi_invoice').id,
})
wizard_action = send_wizard.action_send_and_print()
print('  send wizard      :', type(wizard_action).__name__)

env.cr.commit()

print()
print('  INVOICE_ID  =', inv.id)
print('  SIGN_URL    = http://127.0.0.1:8069/my/invoices/%s/sign?access_token=%s'
      % (inv.id, token))
print('  queued mail =', env['mail.mail'].search_count([
    ('model', '=', 'account.move'), ('res_id', '=', inv.id)]))
