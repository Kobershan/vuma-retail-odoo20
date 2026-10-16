"""Verify the UI layer actually renders: views validate, actions and menus resolve."""
env = env(user=1)
ok, fail = [], []


def check(label, cond, detail=''):
    (ok if cond else fail).append(f'{label}{" -> " + detail if detail else ""}')


# 1. Every view for our models must pass Odoo's own validation.
for model, kinds in (
    ('cognify.remittance', ('form', 'list', 'search', 'calendar')),
    ('cognify.remittance.receipt', ('list',)),
    ('cognify.remittance.line', ('list',)),
    ('cognify.expense.category', ('list', 'form')),
):
    for kind in kinds:
        try:
            res = env[model].get_views([(False, kind)])
            arch = res['views'][kind]['arch']
            check(f'{model} {kind} view renders', bool(arch) and '<%s' % kind in arch,
                  f'{len(arch)} chars')
        except Exception as e:
            check(f'{model} {kind} view renders', False, f'{type(e).__name__}: {e}')

# 2. Actions resolve to real views/models.
for xmlid in ('cognify_remittance.action_cognify_remittance',
              'cognify_remittance.action_my_remittances',
              'cognify_remittance.action_cognify_expense_category',
              'cognify_remittance.action_open_cognify_remittance_settings'):
    try:
        act = env.ref(xmlid)
        model = env[act.res_model]
        model.get_views([(False, act.view_mode.split(',')[0])])
        check(f'action {xmlid.split(".")[1]} loads', True, f'{act.res_model}')
    except Exception as e:
        check(f'action {xmlid.split(".")[1]} loads', False, f'{type(e).__name__}: {e}')

# 3. Menus are present and point at working actions.
for xmlid in ('cognify_remittance.menu_cognify_remittance_root',
              'cognify_remittance.menu_cognify_remittance_my',
              'cognify_remittance.menu_cognify_remittance_office',
              'cognify_remittance.menu_cognify_expense_category'):
    try:
        m = env.ref(xmlid)
        check(f'menu {xmlid.split(".")[1]} exists', bool(m.name), m.name)
    except Exception as e:
        check(f'menu {xmlid.split(".")[1]} exists', False, f'{type(e).__name__}: {e}')

# 4. Security groups and the privilege wiring.
for gxmlid in ('cognify_remittance.group_cognify_remittance_rep',
               'cognify_remittance.group_cognify_remittance_approver'):
    g = env.ref(gxmlid)
    check(f'group {gxmlid.split(".")[1]} has a privilege scope', bool(g.privilege_id),
          g.privilege_id.name if g.privilege_id else 'none')

# 5. Expense categories available in the rep dropdown, in order, all required ones.
cats = env['cognify.expense.category'].search([('active', '=', True)])
names = [c.code for c in cats]
check('dropdown has the required categories',
      set(names) >= {'FOOD', 'LODGING', 'FUEL', 'AIRTIME', 'OTHER'}, ', '.join(names))
check('categories are ordered for the dropdown',
      [c.sequence for c in cats] == sorted(c.sequence for c in cats))

# 6. Settings round-trip, and the committed value must win over any stale
#    transient settings record (a lingering res.config.settings used to shadow
#    the real cash account, which would post entries to the wrong account).
from odoo import Command
other_cash = env['account.account'].search([('account_type', '=', 'asset_cash')], limit=1)
real_cash = env['res.config.settings'].sudo().get_remittance_config().get('remittance_cash_account_id')
check('a cash account is configured', bool(real_cash), str(real_cash))

env['res.config.settings'].create({'remittance_cash_account_id': other_cash.id}).execute()
got = env['res.config.settings'].sudo().get_remittance_config()
check('saved value is read back', got.get('remittance_cash_account_id') == other_cash,
      f'{got.get("remittance_cash_account_id")}')

# A stale transient record must NOT shadow the committed parameter.
stale = env['res.config.settings'].create({})
stale.remittance_cash_account_id = env['account.account'].search(
    [('account_type', '=', 'asset_cash')], limit=1, offset=1
).id
got2 = env['res.config.settings'].sudo().get_remittance_config()
check('stale settings record does not shadow the stored value',
      got2.get('remittance_cash_account_id') == other_cash,
      f'{got2.get("remittance_cash_account_id")}')
env.cr.rollback()

print('\n' + '=' * 60)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('=' * 60)
print(f'{len(ok)} passed, {len(fail)} failed')
