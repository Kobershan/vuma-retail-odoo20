"""Verify PDF generation works across the system, not just for badges.

This covers the whole report stack that wkhtmltopdf + renderPM unblock.
"""
env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


# 1. wkhtmltopdf is discoverable and runnable
from odoo.tools import config, find_in_path  # noqa: E402
config.parse_config(['-c', '/home/kob/Documents/vuma-retail/odoo.conf'])
wkh = find_in_path('wkhtmltopdf')
check('wkhtmltopdf found on bin_path', bool(wkh), wkh or 'NOT FOUND')

import subprocess  # noqa: E402
if wkh:
    ver = subprocess.run([wkh, '--version'], capture_output=True, text=True)
    check('wkhtmltopdf is patched-qt (needed for headers/footers)',
          'patched qt' in ver.stdout, ver.stdout.strip())

state = env['ir.actions.report'].get_pdf_engine_state('wkhtmltopdf')
check('Odoo reports pdf engine state = ok', state == 'ok', state)

# 2. Barcode image generation (reportlab/renderPM backend)
from odoo.tools.barcode import createBarcodeDrawing  # noqa: E402
png = createBarcodeDrawing('Code128', value='10021', format='png', width=600,
                           height=120, humanReadable=1, fontName='Courier').asString('png')
check('barcode PNG renders', bytes(png[:8]) == b'\x89PNG\r\n\x1a\n', f'{len(png)} bytes')

# 3. A real business document end-to-end: post an invoice, render its PDF.
company = env['res.company'].browse(1)
partner = env['res.partner'].create({'name': 'PDF Probe Customer', 'is_company': True})
product = env['product.product'].create({'name': 'PDF Probe Widget', 'type': 'service',
                                         'invoice_policy': 'order'})
tax = env['account.tax'].search([('company_id', '=', company.id)], limit=1)
inv = env['account.move'].create({
    'move_type': 'out_invoice', 'partner_id': partner.id,
    'invoice_line_ids': [(0, 0, {'product_id': product.id, 'quantity': 2,
                                 'price_unit': 150.0,
                                 'tax_ids': [(6, 0, tax.ids)] if tax else []})],
})
inv.action_post()
check('test invoice posted', inv.state == 'posted', f'{inv.name} {inv.state}')

pdf, _t = env['ir.actions.report']._render_qweb_pdf('account.account_invoices', inv.ids)
check('INVOICE PDF renders', bytes(pdf[:5]) == b'%PDF-', f'{len(pdf)} bytes')
check('invoice PDF is substantial', len(pdf) > 8000, f'{len(pdf)} bytes')

# 4. A sale order PDF too (different report template/layout)
so = env['sale.order'].create({
    'partner_id': partner.id,
    # Odoo 20 names this product_uom_qty, not quantity
    'order_line': [(0, 0, {'product_id': product.id, 'product_uom_qty': 1,
                           'price_unit': 99.0})],
})
so_pdf, _t = env['ir.actions.report']._render_qweb_pdf('sale.action_report_saleorder', so.ids)
check('SALE ORDER PDF renders', bytes(so_pdf[:5]) == b'%PDF-', f'{len(so_pdf)} bytes')

# 5. Badge PDF (the one this whole exercise is for)
emp = env['hr.employee'].create({'name': 'PDF Probe Rep', 'barcode': '90001',
                                 'company_id': company.id})
bpdf, _t = env['ir.actions.report']._render_qweb_pdf('hr.hr_employee_print_badge', [emp.id])
check('BADGE PDF renders', bytes(bpdf[:5]) == b'%PDF-', f'{len(bpdf)} bytes')

# 6. Sanity: whichever engine Odoo picks by default must report a working state
default_engine = env['ir.actions.report']._get_pdf_engine()
default_state = env['ir.actions.report'].get_pdf_engine_state(default_engine)
check('default pdf engine reports ok', default_state == 'ok',
      f'{default_engine} -> {default_state}')

env.cr.rollback()
print('\n' + '=' * 68)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 68)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back)')
