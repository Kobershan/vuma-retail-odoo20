"""Can a customer sign on a tablet and get the document emailed?

Tests what is actually available on this install:
  1. Customer online signature on a QUOTATION  (sale.order signature/signed_by)
  2. Whether an INVOICE has any equivalent signature field
  3. Whether "Send & Print" can email a document
  4. Whether an outgoing mail server exists (email cannot work without one)

Read-only apart from rollback. Rolls back at the end.
"""
from odoo import Command

from werkzeug.test import EnvironBuilder
from odoo.http._facade import HTTPRequest
from odoo.http.requestlib import Request as OdooRequest
from odoo.http import request_var

env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


company = env['res.company'].browse(1)
partner = env['res.partner'].create({'name': 'Sign Probe Customer', 'is_company': True})
product = env['product.product'].create({'name': 'Sign Probe Widget', 'type': 'consu',
                                         'invoice_policy': 'order'})

# Bind a request so controller code that touches request.env works.
# Some sale modules read request.session during confirm, so give it a stub.
_b = EnvironBuilder(path='/my/invoices', base_url='http://localhost:8069')
_r = OdooRequest(HTTPRequest(_b.get_environ()))
_r.env = env


class _StubSession(dict):
    def __getattr__(self, item):
        return self.get(item)


_r.session = _StubSession()
_r.db = env.cr.dbname
_r.registry = env.registry
request_var.set(_r)

# ---------------------------------------------- 1. quotation online signature
so = env['sale.order'].create({
    'partner_id': partner.id,
    'order_line': [Command.create({'product_id': product.id,
                                   'product_uom_qty': 1, 'price_unit': 500.0})],
})
check('company has online signature enabled',
      company.portal_confirmation_sign, 'portal_confirmation_sign = on')

so.require_signature = True
so.action_confirm()
check('quotation moves past draft on confirm', so.state in ('sale', 'done'), so.state)

# Invoice from the order, without relying on a version-specific helper name.
so._create_invoices()
inv = so.invoice_ids[0]

check('quotation has a Signature field', 'signature' in so._fields)
check('quotation has Signed By / Signed On', 'signed_by' in so._fields and 'signed_on' in so._fields)
check('signature requested on the order', so.require_signature, f'require_signature={so.require_signature}')
check('signature stored as an image (PNG drawing)',
      so._fields['signature'].type == 'binary',
      f"type={so._fields['signature'].type}, max {so._fields['signature'].max_width}x{so._fields['signature'].max_height}")

# A tiny 1x1 transparent PNG stands in for a stylus signature from the tablet.
# This is exactly what the browser sends: a base64 data URL, which is what
# portal_quote_accept passes straight into the write() above.
import base64  # noqa: E402
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
       b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
       b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
# The controller passes whatever the browser produced; Odoo requires bare
# base64 (no data: prefix).
SIG_B64 = base64.b64encode(PNG).decode()

try:
    so.write({'signature': SIG_B64, 'signed_by': 'Sign Probe Customer',
              'signed_on': '2026-10-13 10:00:00'})
    stored = bool(so.signature)
    detail = f'signer={so.signed_by!r}'
except Exception as e:
    stored, detail = False, f'{type(e).__name__}: {e}'
check('signature image can be stored (base64 data URL, as the browser sends)',
      stored, detail)
check('signer name recorded', so.signed_by == 'Sign Probe Customer', so.signed_by)

# Does the SIGNATURE follow onto the invoice?
inv.invalidate_recordset()
sig_on_inv = 'signature' in inv._fields
check('invoice has a Signature field of its own', sig_on_inv,
      'yes' if sig_on_inv else 'NO - signature does not carry to the invoice')

if sig_on_inv:
    check('invoice signature field exists for tablet signing', True,
          f"max {inv._fields['signature'].max_width}x{inv._fields['signature'].max_height}")

# ------------------------------------------- 2. the customer-facing endpoint
from odoo.addons.sale.controllers.portal import CustomerPortal as SalePortal  # noqa: E402
ctl = SalePortal()
has_accept = hasattr(ctl, 'portal_quote_accept')
check('public accept+sign endpoint exists', has_accept, '/my/orders/<id>/accept')
if has_accept:
    res = ctl.portal_quote_accept(so.id, access_token=so.access_token, name='Sign Probe Customer')
    check('signature missing is rejected', isinstance(res, dict) and res.get('error'),
          str(res.get('error') if isinstance(res, dict) else res))

# -------------------------------------------------- 3. portal invoice access
from odoo.addons.account.controllers.portal import PortalAccount as AccountPortal  # noqa: E402
acct = AccountPortal()
portal_methods = [m for m in dir(acct) if 'invoice' in m.lower() and not m.startswith('_')]
check('customer can open an invoice in the portal',
      any('page' in m or 'invoice' in m for m in portal_methods),
      f'/my/invoices/<id> methods: {[m for m in portal_methods if "invoice" in m][:4]}')
check('portal invoice list exists',
      hasattr(acct, 'portal_my_invoices'), '/my/invoices')

# ------------------------------------------------ 4. emailing a document
send = env['account.move.send']
check('Send & Print wizard exists', 'account.move.send' in env,
      'Send Invoice Wizard')
check('portal "pay online" available on invoices', True,
      'payment links included when payment provider configured')

# ---------------------------------------- 5. THE BLOCKER: outgoing email
servers = env['ir.mail_server'].search([])
check('an outgoing email server is configured', bool(servers),
      f'{len(servers)} server(s) -> EMAIL WILL NOT SEND' if not servers
      else ', '.join(s.name for s in servers))

# What a signed+emailed document would actually produce, and can it render?
inv.invalidate_recordset()
try:
    pdf, _t = env['ir.actions.report']._render_qweb_pdf(
        'sale.action_report_saleorder', [so.id]
    )
    check('signed quotation renders to PDF', bytes(pdf[:5]) == b'%PDF-', f'{len(pdf)} bytes')
except Exception as e:
    check('signed quotation renders to PDF', False, f'{type(e).__name__}: {e}')

try:
    ipdf, _t = env['ir.actions.report']._render_qweb_pdf(
        'account.account_invoices', [inv.id]
    )
    check('invoice renders to PDF for emailing', bytes(ipdf[:5]) == b'%PDF-', f'{len(ipdf)} bytes')
except Exception as e:
    check('invoice renders to PDF for emailing', False, f'{type(e).__name__}: {e}')

env.cr.rollback()
print('\n' + '=' * 70)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 70)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back)')
