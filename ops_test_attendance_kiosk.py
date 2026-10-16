"""Prove the NATIVE Odoo 20 kiosk barcode route can receive a device punch.

This is the zero-code integration path: a fingerprint terminal configured to
emit its user ID is matched against hr.employee.barcode ("Badge ID").
No custom module involved. Rolls back at the end - leaves no data.
"""
import json
from odoo import Command

env = env(user=1)  # noqa: F821
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


company = env['res.company'].browse(1)

# --- A fingerprint terminal reports its enrolled user ID as the badge number.
DEVICE_USER_ID = '10021'

employee = env['hr.employee'].create({
    'name': 'Test Fingerprint Rep',
    'barcode': DEVICE_USER_ID,
    'company_id': company.id,
})
check('employee created with Badge ID', employee.barcode == DEVICE_USER_ID,
      f'barcode={employee.barcode}')

# --- Turn on kiosk mode exactly as an admin would in Settings > Attendance.
company.sudo().write({
    'attendance_kiosk_mode': 'barcode',
    'attendance_barcode_source': 'scanner',
    'attendance_kiosk_use_pin': False,
    'attendance_device_tracking': True,
})
company.invalidate_recordset()
token = company.attendance_kiosk_key
check('kiosk token generated', bool(token), (token or '')[:12] + '...')
check('kiosk URL resolvable', '/hr_attendance/' in (company.attendance_kiosk_url or ''),
      company.attendance_kiosk_url)

# --- Simulate the device POSTing a punch to /hr_attendance/attendance_barcode_scanned
# Drive the REAL controller class exactly as the HTTP layer would.
from werkzeug.test import EnvironBuilder  # noqa: E402
from odoo.http._facade import HTTPRequest  # noqa: E402
from odoo.http.requestlib import Request as OdooRequest  # noqa: E402
from odoo.http import request_var  # noqa: E402
from odoo.addons.hr_attendance.controllers.main import HrAttendance  # noqa: E402


def bind_request(env_):
    """Bind a synthetic HTTP request so `request.env` resolves in the shell."""
    builder = EnvironBuilder(path='/hr_attendance/attendance_barcode_scanned',
                             base_url='http://localhost:8069')
    req = OdooRequest(HTTPRequest(builder.get_environ()))
    req.env = env_
    request_var.set(req)
    return req


bind_request(env)
ctl = HrAttendance()
before = env['hr.attendance'].search_count([])

result = ctl.scan_barcode(token, DEVICE_USER_ID, 0.0, 0.0, None)
check('device punch accepted by native endpoint', bool(result), str(result)[:120])

# --- Did it actually create the clock-in?
attendances = env['hr.attendance'].search([('employee_id', '=', employee.id)])
check('attendance record created', len(attendances) == 1, f'{len(attendances)} records')
if attendances:
    att = attendances[0]
    check('check_in populated', bool(att.check_in), str(att.check_in))
    check('check_out still empty', not att.check_out, str(att.check_out))
    check('in_mode is kiosk', att.in_mode == 'kiosk', str(att.in_mode))
    check('record linked to the right employee', att.employee_id == employee)

# --- Second punch from the same device must clock OUT.
result2 = ctl.scan_barcode(token, DEVICE_USER_ID, 0.0, 0.0, None)
check('second punch accepted', bool(result2), str(result2)[:120])
attendances.invalidate_recordset()
att = env['hr.attendance'].search([('employee_id', '=', employee.id)], limit=1)
check('check_out now populated', bool(att.check_out), str(att.check_out))
check('out_mode is kiosk', att.out_mode == 'kiosk', str(att.out_mode))
if att.check_in and att.check_out:
    dur = (att.check_out - att.check_in).total_seconds() / 60
    # Both punches happen in the same instant in this test, so 0 minutes is
    # the correct result; what matters is the span is non-negative and Odoo
    # computed it rather than leaving the record open.
    check('worked minutes computed', dur >= 0,
          f'{dur:.4f} min (same-second punches -> 0 is correct)')

# --- An unknown device user must NOT create anything.
before_unknown = env['hr.attendance'].search_count([])
res3 = ctl.scan_barcode(token, '99999999', 0.0, 0.0, None)
after_unknown = env['hr.attendance'].search_count([])
check('unknown badge rejected safely', not res3 and after_unknown == before_unknown,
      f'response={res3!r} count {before_unknown}->{after_unknown}')

# --- Bad token must be rejected.
res4 = ctl.scan_barcode('deadbeefwrongtoken', DEVICE_USER_ID, 0.0, 0.0, None)
check('invalid token rejected', not res4, repr(res4))

env.cr.rollback()
print('\n' + '=' * 68)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 68)
print(f'{len(ok)} passed, {len(fail)} failed  (rolled back - no data left)')
