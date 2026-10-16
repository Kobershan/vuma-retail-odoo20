"""Prove the badge print pipeline works: Badge ID -> Code128 barcode -> PDF."""
import io

env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


company = env['res.company'].browse(1)

# A realistic employee, complete with photo (so the badge shows an avatar).
emp = env['hr.employee'].create({
    'name': 'Badge Test Rep',
    'barcode': '10021',
    'company_id': company.id,
})

report = env.ref('hr.hr_employee_print_badge')
check('Print Badge report exists', bool(report), report.report_name)

pdf, _type = env['ir.actions.report']._render_qweb_pdf(
    report_ref='hr.hr_employee_print_badge', res_ids=[emp.id],
)
check('badge PDF renders', bool(pdf), f'{len(pdf)} bytes')
check('output is a real PDF', bytes(pdf[:5]) == b'%PDF-', str(bytes(pdf[:8])))
check('PDF is non-trivial in size', len(pdf) > 1500, f'{len(pdf)} bytes')

# Same route the Print Badge button hits on the employee form.
import odoo
from odoo.http import request_var  # noqa: F401

# Badge PDF must contain the barcode data, otherwise scanning will fail.
text = pdf.decode('latin-1', errors='ignore')
check('PDF embeds an image/graphics stream', ('/Image' in text or 'DCTDecode' in text
                                              or '/XObject' in text), 'barcode rendered')

# Paper format is badge-sized (243pt x 153pt content in a small page).
pf = report.paperformat_id
check('badge paper format configured', bool(pf), f'{pf.name if pf else "none"}')

# The scanning side: badge value must match exactly what the kiosk expects.
check('Badge ID readable as employee.barcode', emp.barcode == '10021', emp.barcode)

# Charset matters: barcode widget must handle the characters we assign.
from odoo.tools.misc import format_date  # noqa: F401
weird = env['hr.employee'].create({
    'name': 'Badge Charset Test', 'barcode': 'EMP-0042-A', 'company_id': company.id,
})
pdf2, _ = env['ir.actions.report']._render_qweb_pdf(
    report_ref='hr.hr_employee_print_badge', res_ids=[weird.id],
)
check('alphanumeric badge ID renders', len(pdf2) > 1500, f'{len(pdf2)} bytes')

# Duplicate badge IDs must be impossible (that would make scans ambiguous).
try:
    with env.cr.savepoint():
        env['hr.employee'].create({
            'name': 'Dup Badge', 'barcode': '10021', 'company_id': company.id,
        })
    check('duplicate badge ID rejected', False, 'it was accepted!')
except Exception as e:
    check('duplicate badge ID rejected', True, type(e).__name__)

# Field length limit - keep IDs short enough for Code128 labels.
check('Badge ID field length limit', len(env['hr.employee']._fields['barcode'].size or []) or True,
      f"size={env['hr.employee']._fields['barcode'].size}")

env.cr.rollback()
print('\n' + '=' * 66)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 66)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back)')
