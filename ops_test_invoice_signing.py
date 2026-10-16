"""End-to-end test of cognify_invoice_signature.

Proves a customer can sign an invoice from the portal the way a tablet would,
and that the signature lands on the PDF and is properly guarded.
"""
import base64
from lxml import etree
from odoo import Command, fields
from werkzeug.test import EnvironBuilder
from odoo.http._facade import HTTPRequest
from odoo.http.requestlib import Request as OdooRequest
from odoo.http import request_var

env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


def savepoint(fn):
    try:
        with env.cr.savepoint():
            fn()
        return True
    except Exception:
        return False


company = env['res.company'].browse(1)
partner = env['res.partner'].create({'name': 'Sign Tablet Customer', 'is_company': True})
product = env['product.product'].create({'name': 'Signature Widget', 'type': 'consu',
                                         'invoice_policy': 'order'})
tax = env['account.tax'].search([('company_id', '=', company.id)], limit=1)

def bind(env_, with_website=False):
    b = EnvironBuilder(path='/my/invoices', base_url='http://localhost:8069')
    if with_website:
        # In a real request, website routing puts website_id in the context and
        # env.website resolves from there. portal.frontend_layout needs it.
        site = env_['website'].sudo().search([], limit=1)
        env_ = env_(context=dict(env_.context, website_id=site.id))
    r = OdooRequest(HTTPRequest(b.get_environ()))
    r.env = env_
    r.db = env_.cr.dbname
    r.registry = env_.registry

    class _S(dict):
        def __getattr__(self, k):
            return self.get(k)

    r.session = _S()
    request_var.set(r)
    return r


bind(env)

from odoo.addons.cognify_invoice_signature.controllers.portal import (  # noqa: E402
    InvoiceSignPortal,
)

ctl = InvoiceSignPortal()

# A 1x1 transparent PNG, base64 - exactly what the browser signature pad posts.
PNG = (b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06'
       b'\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05'
       b'\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82')
SIG = base64.b64encode(PNG).decode()


def new_invoice(name, post=True):
    inv = env['account.move'].create({
        'move_type': 'out_invoice',
        'partner_id': partner.id,
        'invoice_line_ids': [Command.create({
            'product_id': product.id, 'quantity': 1, 'price_unit': 250.0,
            'tax_ids': [Command.set(tax.ids)] if tax else [],
        })],
        'name': name,
    })
    if post:
        inv.action_post()
    return inv


# ------------------------------------------------------------- fields exist
inv = new_invoice('/TEST/SIGN/0001')
check('signature field on account.move', 'signature' in inv._fields)
check('signed_by field on account.move', 'signed_by' in inv._fields)
check('signed_on field on account.move', 'signed_on' in inv._fields)
check('signature_requested field exists', 'signature_requested' in inv._fields)

# ------------------------------------------------------------- the rules
check('posted customer invoice is signable', inv._can_be_signed())

draft = new_invoice('/TEST/SIGN/0002', post=False)
check('DRAFT invoice cannot be signed', not draft._can_be_signed(),
      'avoids signing something that may still change')

bill = env['account.move'].create({
    'move_type': 'in_invoice', 'partner_id': partner.id,
    'invoice_date': fields.Date.context_today(env['res.users']),
    'invoice_date_due': fields.Date.context_today(env['res.users']),
    'invoice_line_ids': [Command.create({
        'product_id': product.id, 'quantity': 1, 'price_unit': 50.0})],
})
bill.action_post()
check('VENDOR BILL cannot be signed', not bill._can_be_signed(),
      f'state={bill.state}, a supplier does not sign your bill')

# office must not request a signature on a draft
check('office blocked from requesting signature on a draft',
      not savepoint(lambda: draft.action_request_signature()))

check('office blocked from requesting signature on a vendor bill',
      not savepoint(lambda: bill.action_request_signature()))

# -------------------------------------------------- office requests signature
inv.action_request_signature()
check('signature_requested flag set', inv.signature_requested)

# ------------------------------------------------------ customer signs it
res = ctl.portal_invoice_sign_submit(
    inv.id, access_token=inv.access_token, name='Thandiwe Mokoena', signature=SIG,
)
check('portal submit accepted', bool(res.get('success')), str(res.get('error') or res.get('message')))
inv.invalidate_recordset()
check('signature image stored', bool(inv.signature))
check('signer name stored', inv.signed_by == 'Thandiwe Mokoena', inv.signed_by)
check('timestamp stored', bool(inv.signed_on), str(inv.signed_on))
check('request flag cleared after signing', not inv.signature_requested,
      'nobody can silently re-request')
check('invoice is no longer signable', not inv._can_be_signed(),
      'the first signature stands')
bodies = ' '.join(str(b) for b in inv.message_ids.mapped('body'))
check('chatter records the signing', 'Signed online by' in bodies,
      'note posted to the invoice thread')

# ------------------------------------------------- guards on bad submissions
inv2 = new_invoice('/TEST/SIGN/0003')
inv2.action_request_signature()

r = ctl.portal_invoice_sign_submit(inv2.id, access_token=inv2.access_token,
                                   name='', signature=SIG)
check('empty name rejected with a friendly error', bool(r.get('error')), str(r.get('error')))

r = ctl.portal_invoice_sign_submit(inv2.id, access_token=inv2.access_token,
                                   name='Someone', signature=None)
check('missing signature rejected', bool(r.get('error')), str(r.get('error')))

r = ctl.portal_invoice_sign_submit(999999, access_token='whatever',
                                   name='Ghost', signature=SIG)
check('unknown invoice rejected', bool(r.get('error')), str(r.get('error')))

# --- SECURITY: these must run as a LOGGED-OUT public user. As admin the access
# check is bypassed entirely, which would make a token test meaningless.
inv2 = new_invoice('/TEST/SIGN/0031')
inv2.action_request_signature()
good_token = inv2.sudo()._portal_ensure_token()
check('portal token generated on demand', bool(good_token),
      f'token exists ({len(good_token or "")} chars)')

public = env.ref('base.public_user')
bind(env(user=public))

r = ctl.portal_invoice_sign_submit(inv2.id, access_token='wrong-token',
                                   name='Mallory', signature=SIG)
check('SECURITY: wrong token rejected (logged-out user)', bool(r.get('error')),
      str(r.get('error')))
env.invalidate_all()
inv2.invalidate_recordset()
check('SECURITY: wrong token did NOT sign the invoice',
      not inv2.signature and not inv2.signed_by, 'signature untouched')

r = ctl.portal_invoice_sign_submit(inv2.id, access_token=good_token,
                                   name='Real Customer', signature=SIG)
check('SECURITY: correct token works for the real customer', bool(r.get('success')),
      str(r.get('error') or r.get('message')))
env.invalidate_all()
inv2.invalidate_recordset()
check('SECURITY: correct token recorded the signature',
      inv2.signed_by == 'Real Customer', inv2.signed_by)

# back to admin for the remaining checks
bind(env)

# a second attempt on an already-signed invoice
r = ctl.portal_invoice_sign_submit(inv.id, access_token=inv.access_token,
                                   name='Impostor', signature=SIG)
inv.invalidate_recordset()
check('already-signed invoice cannot be re-signed', bool(r.get('error')), str(r.get('error')))
check('original signer preserved', inv.signed_by == 'Thandiwe Mokoena', inv.signed_by)

# --------------------------------------------------------- signature on PDF
pdf, _t = env['ir.actions.report']._render_qweb_pdf('account.account_invoices', inv.ids)
check('signed invoice renders to PDF', bytes(pdf[:5]) == b'%PDF-', f'{len(pdf)} bytes')

# revoke
inv.action_revoke_signature()
inv.invalidate_recordset()
check('revoke clears the signature', not inv.signature and not inv.signed_by)
check('invoice signable again after revoke', inv._can_be_signed())

# --------------------------------------------- request guard for payment state
inv3 = new_invoice('/TEST/SIGN/0004')
inv3.action_request_signature()
env['account.account'].create({
    'name': 'Sign Test Bank', 'code': 'SIGNBNK', 'account_type': 'asset_cash',
    'company_ids': [Command.link(company.id)],
})
jc = env['account.journal'].search([('type', '=', 'bank'), ('company_id', '=', company.id)],
                                   limit=1)
if jc:
    inv3.payment_reference = 'TESTREF'
    wizard = env['account.payment.register'].with_context(
        active_model='account.move', active_ids=inv3.ids).create({})
    wizard.action_create_payments()
    inv3.invalidate_recordset()
    check('PAID invoice cannot be signed', not inv3._can_be_signed(),
          f'payment_state={inv3.payment_state}')

# --------------------------------------------------- template sanity
# NOTE: rendering portal.frontend_layout from the shell is not possible - it
# needs values only the real HTTP render pipeline supplies (website,
# main_object). That path is covered by the live HTTP test instead.
try:
    tpl = env.ref('cognify_invoice_signature.portal_invoice_sign_page')
    check('signing page template exists', bool(tpl and tpl.name), tpl.name)
    arch = tpl._get_combined_arch()
    xml = etree.tostring(arch, encoding='unicode') if hasattr(arch, 'tag') else str(arch)
    check('signing page uses the stock signature widget',
          'portal.signature_form' in xml, 'no custom JS needed')
    check('signing page points at the submit endpoint', '/sign/submit' in xml, 'call_url wired')
    check('signing page passes the token through', 'access_token' in xml)
except Exception as e:
    check('signing page template exists', False, f'{type(e).__name__}: {str(e)[:120]}')

try:
    rep = env.ref('cognify_invoice_signature.report_invoice_document_signature')
    rarch = rep._get_combined_arch()
    rxml = etree.tostring(rarch, encoding='unicode') if hasattr(rarch, 'tag') else str(rarch)
    check('PDF report carries the signature block', 'customer_signature_block' in rxml,
          'inherits account.report_invoice_document')
except Exception as e:
    check('PDF report carries the signature block', False, f'{type(e).__name__}: {str(e)[:120]}')

env.cr.rollback()
print('\n' + '=' * 70)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 70)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back - no data left)')
