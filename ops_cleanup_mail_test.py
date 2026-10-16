"""Remove the mail-flow test data."""
env = env(user=1)  # noqa: F821

inv = env['account.move'].search([('name', '=', '/TEST/MAIL/SIGN/0001')])
if inv:
    env['mail.mail'].search([('mail_message_id', 'in', inv.message_ids.ids)]).unlink()
    # posted moves cannot be deleted until reset to draft
    if inv.state == 'posted':
        inv.button_draft()
    inv.unlink()
    print('  removed test invoice (reset to draft first)')

partner = env['res.partner'].search([('email', '=', 'customer@vuma-retail.test')])
if partner:
    partner.unlink()
    print('  removed test partner')

pt = env['product.template'].search([('name', '=', 'Sink Test Widget')])
if pt:
    pt.active = False
    print('  archived test product (still referenced by move lines)')

env.cr.commit()
print()
print('  account_move rows :', env['account.move'].search_count([]))
print('  probe partners    :', env['res.partner'].search_count([
    ('name', 'ilike', 'Sink Test'), ('name', 'ilike', 'Probe')]))
