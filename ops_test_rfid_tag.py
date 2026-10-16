"""Does an RFID-in-badge actually work with the Odoo 20 kiosk?

RFID readers used as clock-in devices operate in HID keyboard-wedge mode: they
"type" the tag UID as keystrokes plus Enter. Odoo matches that string against
hr.employee.barcode with an exact '=' comparison, so this test pins down the
real-world constraints: UID formats, hex case, leading zeros, and HID suffixes.
"""
from werkzeug.test import EnvironBuilder
from odoo.http._facade import HTTPRequest
from odoo.http.requestlib import Request as OdooRequest
from odoo.http import request_var
from odoo.addons.hr_attendance.controllers.main import HrAttendance

env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


company = env['res.company'].browse(1)
company.sudo().write({
    'attendance_kiosk_mode': 'barcode',
    'attendance_barcode_source': 'scanner',
    'attendance_kiosk_use_pin': False,
})
company.invalidate_recordset()
token = company.attendance_kiosk_key


def bind():
    b = EnvironBuilder(path='/hr_attendance/attendance_barcode_scanned',
                       base_url='http://localhost:8069')
    r = OdooRequest(HTTPRequest(b.get_environ()))
    r.env = env
    request_var.set(r)


bind()
ctl = HrAttendance()


def scan(value):
    """One tag presented, exactly as an HID reader would deliver it."""
    return ctl.scan_barcode(token, value, 0.0, 0.0, None)


# ---- Realistic RFID tag UIDs ------------------------------------------------
# EM4100 125kHz tags: 8 hex chars. Mifare Ultralight: 7-8 hex. Classic 1K: 10 hex.
CASES = [
    ('0001A2B3', 'EM4100 8-hex UID, leading zero'),
    ('1A2B3C4D', '8-hex UID, no leading zero'),
    ('04A1B2C3D4', '10-hex UID (Mifare 1K style)'),
    ('00FFFFFFFF', 'all-F UID, a real factory default'),
    ('12345678', '8 digits'),
]

for uid, label in CASES:
    emp = env['hr.employee'].create({
        'name': f'RFID {uid}', 'barcode': uid, 'company_id': company.id,
    })
    res = scan(uid)
    att = env['hr.attendance'].search([('employee_id', '=', emp.id)])
    check(f'{label}: "{uid}" clocks in',
          bool(res) and len(att) == 1 and bool(att.check_in),
          f'resp={bool(res)} att={len(att)} in={bool(att and att.check_in)}')
    scan(uid)  # clock back out so the next case starts clean
    env['hr.attendance'].search([('employee_id', '=', emp.id)]).unlink()

# ---- Case sensitivity: the matching is '=' so it IS case sensitive ----------
emp = env['hr.employee'].create({
    'name': 'Case Test', 'barcode': 'ABCD1234', 'company_id': company.id,
})
r_lower = scan('abcd1234')
check('lowercase UID does NOT match uppercase-stored Badge ID',
      not r_lower, f'response={r_lower!r} (exact match is case sensitive)')
r_upper = scan('ABCD1234')
check('matching-case UID works', bool(r_upper), f'response={bool(r_upper)}')
scan('ABCD1234')
env['hr.attendance'].search([('employee_id', '=', emp.id)]).unlink()

# ---- HID suffix/prefix handling ---------------------------------------------
# Cheap readers may append CR/LF, a tab, or emit a prefix. Real HID wedge
# readers normally emit <uid><CR>; Odoo's JS trims. Test the server behaviour
# for a stray character, because that silently breaks a scan.
for suffix, desc in [('\r', 'CR suffix'), ('\r\n', 'CRLF suffix'), ('\t', 'tab suffix')]:
    bad = scan(f'ABCD1234{suffix}')
    check(f'{desc} is rejected (must be trimmed by the reader/JS)',
          not bad, f'response={bad!r}')

# ---- Leading zeros are preserved -------------------------------------------
zero_emp = env['hr.employee'].create({
    'name': 'Zero Test', 'barcode': '00000001', 'company_id': company.id,
})
check('Badge ID keeps leading zeros as text',
      zero_emp.barcode == '00000001', f'stored={zero_emp.barcode!r}')
r = scan('00000001')
check('leading-zero UID clocks in', bool(r), f'response={bool(r)}')
scan('00000001')
env['hr.attendance'].search([('employee_id', '=', zero_emp.id)]).unlink()

# ---- One tag, two employees, must not be ambiguous -------------------------
a = env['hr.employee'].create({'name': 'Dup A', 'barcode': 'DUP0001', 'company_id': company.id})
dup_blocked = False
try:
    with env.cr.savepoint():
        env['hr.employee'].create({'name': 'Dup B', 'barcode': 'DUP0001',
                                   'company_id': company.id})
except Exception:
    dup_blocked = True
check('duplicate RFID UID across employees is blocked', dup_blocked,
      'UniqueViolation on hr_employee_barcode_uniq')
check('original employee unaffected', a.exists() and a.barcode == 'DUP0001',
      f'{a.name} still holds DUP0001')

# ---- Same card tapped twice in quick succession ----------------------------
# With the 10s kiosk delay the UI suppresses a repeat, but confirm the server
# toggles rather than creating a second open record.
t1 = env['hr.employee'].create({'name': 'Toggle', 'barcode': 'TOG0001', 'company_id': company.id})
scan('TOG0001')
first = env['hr.attendance'].search([('employee_id', '=', t1.id)])
scan('TOG0001')
second = env['hr.attendance'].search([('employee_id', '=', t1.id)])
check('two taps = one record with in and out set',
      len(first) == 1 and bool(second.check_in) and bool(second.check_out),
      f'{len(first)} record(s) in={bool(second.check_in)} out={bool(second.check_out)}')

# ---- An unassigned tag must not clock anybody in --------------------------
before = env['hr.attendance'].search_count([])
r = scan('NOTASSIGNED')
after = env['hr.attendance'].search_count([])
check('an unassigned RFID tag creates nothing', not r and before == after,
      f'count {before}->{after}')

# ---- Badge ID max length: RFID UIDs are short, but confirm the actual limit
uid_emp = env['hr.employee'].create({
    'name': 'Long UID', 'company_id': company.id,
})
try:
    with env.cr.savepoint():
        uid_emp.write({'barcode': 'F' * 18})
    check('18-character UID (the maximum) is accepted', uid_emp.barcode == 'F' * 18,
          f'{len(uid_emp.barcode)} chars stored')
except Exception as e:
    check('18-character UID (the maximum) is accepted', False, f'{type(e).__name__}: {e}')

uid_emp.barcode = False
try:
    with env.cr.savepoint():
        uid_emp.write({'barcode': 'F' * 19})
    check('19-character UID is rejected (Odoo caps Badge ID at 18)', False,
          'it was accepted!')
except Exception:
    check('19-character UID is rejected (Odoo caps Badge ID at 18)', True,
          'as expected for any real RFID tag')

env.cr.rollback()
print('\n' + '=' * 70)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 70)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back)')
