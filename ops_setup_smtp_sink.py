"""Point Odoo at the local SMTP sink so the real mail path can be tested.

Creates an ir.mail_server on 127.0.0.1:1025 and makes it the default.
COMMITS - that is the point of the exercise.
"""
env = env(user=1)  # noqa: F821

HOST, PORT = '127.0.0.1', 1025

existing = env['ir.mail_server'].search([('smtp_host', '=', HOST), ('smtp_port', '=', PORT)])
if existing:
    srv = existing
    print('  reusing existing sink server', srv.id)
else:
    srv = env['ir.mail_server'].create({
        'name': 'Local Test Sink',
        'smtp_host': HOST,
        'smtp_port': PORT,
        'smtp_encryption': 'none',
        'smtp_authentication': 'login',
        'smtp_user': 'odoo',
        'smtp_pass': 'sink',
        'from_filter': '',
        'sequence': 5,
    })
    print('  created SMTP sink server id =', srv.id)

# make it the default for outgoing mail
# (Odoo 20 dropped set_param/get_param; use set_str/get_str)
params = env['ir.config_parameter'].sudo()
params.set_str('mail.default.from', 'accounts@vuma-retail.test')
params.set_str('mail.default.from_filter', '')
print('  default from :', params.get_str('mail.default.from'))

env.cr.commit()
print('  committed. SMTP_SERVER_ID=%d' % srv.id)
