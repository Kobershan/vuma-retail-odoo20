"""Bind an RFID tag to an employee the way a real kiosk does, and prove it works.

Two ways to bind a tag:
  A) Kiosk screen -> "Add Employee" -> scan the tag
     (endpoint /hr_attendance/set_badge, which writes hr.employee.barcode)
  B) HR > Employees > HR Settings tab -> type the Badge ID

This drives (A), the path an employee follows on their first day.

DRY RUN BY DEFAULT. Set RFID_BIND_LIVE=1 to actually commit.

    RFID_BIND_LIVE=0 ./venv/bin/python odoo/odoo-bin shell -c odoo.conf \
        -d vuma_retail --http-port=8199 --log-level=error < ops_bind_rfid_badge.py
"""
import os

LIVE = os.environ.get('RFID_BIND_LIVE') == '1'

env = env(user=1)  # noqa: F821
from werkzeug.test import EnvironBuilder  # noqa: E402
from odoo.http._facade import HTTPRequest  # noqa: E402
from odoo.http.requestlib import Request as OdooRequest  # noqa: E402
from odoo.http import request_var  # noqa: E402
from odoo.addons.hr_attendance.controllers.main import HrAttendance  # noqa: E402

company = env['res.company'].browse(1)
company.sudo().write({
    'attendance_kiosk_mode': 'barcode',
    'attendance_barcode_source': 'scanner',
})
company.invalidate_recordset()
token = company.attendance_kiosk_key

_b = EnvironBuilder(path='/hr_attendance/attendance_barcode_scanned',
                    base_url='http://localhost:8069')
_r = OdooRequest(HTTPRequest(_b.get_environ()))
_r.env = env
request_var.set(_r)
ctl = HrAttendance()

ok, fail = [], []
made = []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


def attempt(fn):
    """Run fn, returning True on success. Uses a savepoint so a rejected write
    does not poison the rest of the script."""
    try:
        with env.cr.savepoint():
            fn()
        return True
    except Exception:
        return False


def bind(emp, tag):
    return ctl.set_badge(emp.id, tag, token)


def punch(tag):
    return ctl.scan_barcode(token, tag, 0.0, 0.0, None)


def fresh(name, tag=None):
    e = env['hr.employee'].create({'name': name, 'company_id': company.id})
    made.append(e.id)
    if tag:
        bind(e, tag)
    env.invalidate_all()
    return e


print('=' * 70)
print('LIVE - will COMMIT' if LIVE else 'DRY RUN - nothing will be written')
print('=' * 70)

# ------------------------------------------------- bind a tag to a new employee
emp = fresh('RFID Pilot Rep')
check('employee created with no Badge ID', not emp.barcode, f'barcode={emp.barcode!r}')

res = bind(emp, 'A1B2C3D4')
check('kiosk set_badge accepted the tag', res.get('status') == 'success', str(res))

env.invalidate_all()
emp.invalidate_recordset()
check('Badge ID now holds the RFID UID', emp.barcode == 'A1B2C3D4', f'{emp.barcode!r}')

# -------------------------------------------------------- the bound tag works
r1 = punch('A1B2C3D4')
att = env['hr.attendance'].search([('employee_id', '=', emp.id)])
check('bound tag clocks the employee in', bool(r1) and len(att) == 1 and bool(att.check_in),
      f'resp={bool(r1)} records={len(att)}')

punch('A1B2C3D4')
att.invalidate_recordset()
check('second tap clocks out', bool(att.check_out), f'out={att.check_out}')
check('in and out on ONE record', len(att) == 1, f'{len(att)} record(s)')
check('mode recorded as kiosk', att.in_mode == 'kiosk' and att.out_mode == 'kiosk',
      f'in={att.in_mode} out={att.out_mode}')
env['hr.attendance'].search([('employee_id', '=', emp.id)]).unlink()

# -------------------------------------------------- case sensitivity matters
# Matching is exact ('='), so a reader emitting lowercase will NOT match an
# uppercase-stored Badge ID. This is the single most common field failure.
lower_first = fresh('Case Lower', 'abcd1234')
r_low = punch('ABCD1234')
check('lowercase-stored Badge ID does not match uppercase scan',
      not r_low, 'exact match is case sensitive')
env['hr.attendance'].search([('employee_id', '=', lower_first.id)]).unlink()

r_ok = punch('abcd1234')
check('scanning in the stored case works', bool(r_ok), f'resp={bool(r_ok)}')
env['hr.attendance'].search([('employee_id', '=', lower_first.id)]).unlink()

# ------------------------------------------------------- retire a lost badge
# Real scenario: badge lost, employee given a new tag. The old tag must die.
env.invalidate_all()
emp.invalidate_recordset()
old_tag = emp.barcode
check('pilot still holds the original tag', old_tag == 'A1B2C3D4', f'{old_tag!r}')

bind(emp, 'E5F60718')            # re-issue
env.invalidate_all()
emp.invalidate_recordset()
check('badge re-bound to the new tag', emp.barcode == 'E5F60718', f'{emp.barcode!r}')

old_punch = punch(old_tag)
check('RETIRED tag no longer clocks anyone in', not old_punch, f'resp={old_punch!r}')

new_punch = punch('E5F60718')
check('new tag clocks in after re-issue', bool(new_punch), f'resp={bool(new_punch)}')
env['hr.attendance'].search([('employee_id', '=', emp.id)]).unlink()

# --------------------------------------------- an unassigned tag is inert
before = env['hr.attendance'].search_count([])
r_unknown = punch('NOTASSIGNED')
after = env['hr.attendance'].search_count([])
check('unassigned tag creates nothing', not r_unknown and before == after,
      f'count {before}->{after}')

# ------------------------------------------- two tags, two people, no clash
a = fresh('RFID Rep A', 'AAAA0001')
b = fresh('RFID Rep B', 'BBBB0002')
ra = punch('AAAA0001')
rb = punch('BBBB0002')
check('each tag clocks in only its own employee',
      bool(ra) and bool(rb)
      and env['hr.attendance'].search_count([('employee_id', '=', a.id)]) == 1
      and env['hr.attendance'].search_count([('employee_id', '=', b.id)]) == 1,
      'A and B both clocked in separately')
env['hr.attendance'].search([('employee_id', 'in', (a | b).ids)]).unlink()

# duplicate UID must be impossible
dup_blocked = not attempt(
    lambda: env['hr.employee'].create({'name': 'RFID Dup', 'barcode': 'AAAA0001',
                                       'company_id': company.id})
)
check('two employees cannot share one tag UID', dup_blocked,
      'blocked by hr_employee_barcode_uniq')

# ------------------------------------------------- Badge ID value constraints
print('\n--- Badge ID rules (Odoo validates on write) ---')
probe = env['hr.employee'].create({'name': 'RFID Rule Probe', 'company_id': company.id})
made.append(probe.id)
for value, desc, should_pass in (
    ('A1B2C3D4',      '8 hex chars', True),
    ('a1b2c3d4',      'lowercase hex', True),
    ('0000FFFF',      'leading zeros', True),
    ('04A1B2C3D4',    '10 hex (Mifare 1K UID)', True),
    ('RFID 123',      'contains a space', False),
    ('A' * 19,        '19 chars (limit is 18)', False),
    ('TAB\tHERE',     'tab character', False),
    # An empty string is treated as "no badge assigned" (falsy -> False), which
    # is correct: it clears the field rather than storing a blank tag.
    ('',              'empty clears the field', True),
):
    probe.barcode = False
    got = attempt(lambda v=value: probe.write({'barcode': v}))
    if value == '':
        check(f'{desc}', got and not probe.barcode,
              f'barcode is now {probe.barcode!r} (cleared)')
    else:
        check(f'{desc}: {"accepted" if should_pass else "rejected"}',
              got == should_pass, f'{value!r}')

env.cr.rollback() if not LIVE else None

print('\n' + '=' * 70)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 70)
print(f'{len(ok)} passed, {len(fail)} failed')

if LIVE:
    env.cr.commit()
    print(f'\nCOMMITTED: {emp.name} bound to {emp.barcode}')
    print('Delete the test employees when done, or keep them as pilots.')
else:
    env.cr.rollback()
    print('\nRolled back. Nothing written. Re-run with RFID_BIND_LIVE=1 to apply.')
