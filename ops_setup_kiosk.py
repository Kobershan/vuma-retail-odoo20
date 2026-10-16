"""Configure the Odoo 20 attendance kiosk for BADGE / BARCODE scanning.

Settings chosen:
  mode        = Barcode/RFID + Manual Selection  -> badges primary, fallback exists
  source      = Scanner                          -> USB barcode scanner (HID keyboard)
  use_pin     = ON                               -> manual fallback REQUIRES a PIN,
                                                    so a lost badge cannot be used by
                                                    someone else to clock in as you
  delay       = 10s                              -> cooldown so one badge = one punch

Employees need a Badge ID (hr.employee.barcode). Odoo renders it as a Code128
barcode on the built-in "Print Badge" report.

Idempotent. Commits.
"""
env = env(user=1)  # noqa: F821
company = env['res.company'].browse(1)

print('--- current kiosk settings ---')
for f in ('attendance_kiosk_mode', 'attendance_barcode_source', 'attendance_kiosk_use_pin',
          'attendance_kiosk_delay', 'attendance_device_tracking', 'attendance_capture_check_in'):
    print(f'  {f:<32} = {company.sudo()[f]!r}')

company.sudo().write({
    'attendance_kiosk_mode': 'barcode_manual',
    'attendance_barcode_source': 'scanner',
    'attendance_kiosk_use_pin': True,
    'attendance_kiosk_delay': 10,
})
company.invalidate_recordset()

print('\n--- applied ---')
for f in ('attendance_kiosk_mode', 'attendance_barcode_source', 'attendance_kiosk_use_pin',
          'attendance_kiosk_delay'):
    print(f'  {f:<32} = {company.sudo()[f]!r}')

print('\n--- kiosk URL ---')
print(f'  {company.sudo().attendance_kiosk_url}')
print(f'  token: {company.sudo().attendance_kiosk_key}')

# Badge IDs still to be assigned
employees = env['hr.employee'].search([])
no_badge = employees.filtered(lambda e: not e.barcode)
print('\n--- badge status ---')
print(f'  employees total          : {len(employees)}')
print(f'  without a Badge ID       : {len(no_badge)}')
if no_badge:
    print(f'    -> {[e.name for e in no_badge][:10]}')

# Highest badge number already in use, to seed a non-colliding sequence
codes = [e.barcode for e in employees if (e.barcode or '').isdigit()]
print(f'  numeric badge IDs in use : {len(codes)}'
      + (f' (max {max(codes)})' if codes else ''))

env.cr.commit()
print('\nCommitted.')
