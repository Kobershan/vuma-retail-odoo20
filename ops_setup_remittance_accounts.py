"""One-off configuration for cognify_remittance.

Creates the ledger accounts the module needs (the generic_coa chart has none
for field travel), maps each expense category onto its account, and stores the
remittance journal + cash account in the system parameters.

Idempotent: safe to run more than once.
"""
env = env(user=1)  # noqa: F821
company = env['res.company'].browse(1)
AA = env['account.account']
created = []


def find(code):
    return AA.search([('code', '=', code), ('company_ids', 'in', company.id)], limit=1)


def ensure_account(code, name, account_type):
    acc = find(code)
    if acc:
        print(f'  = {code:<8} {name} (exists, id={acc.id})')
        return acc
    acc = AA.create({
        'code': code,
        'name': name,
        'account_type': account_type,
        'company_ids': [(6, 0, company.ids)],
    })
    created.append(f'{code} {name}')
    print(f'  + {code:<8} {name} (created, id={acc.id})')
    return acc


print('--- ledger accounts -------------------------------------------------')
# 60xxxx is the operating-expense block in generic_coa; 601xxx is unused.
accounts = {
    'FOOD': ensure_account('601000', 'Travel - Food and Catering', 'expense'),
    'LODGING': ensure_account('601100', 'Travel - Lodging', 'expense'),
    'FUEL': ensure_account('601200', 'Travel - Fuel', 'expense'),
    'AIRTIME': ensure_account('601300', 'Travel - Airtime', 'expense'),
    'TOLL': ensure_account('601400', 'Travel - Toll', 'expense'),
    'OTHER': ensure_account('601500', 'Travel - Other Expenses', 'expense'),
}
# Reps hand physical cash to the office, so this is petty cash rather than bank.
cash_account = ensure_account('101500', 'Cash on Hand (Field Reps)', 'asset_cash')

print('\n--- expense categories ---------------------------------------------')
for cat in env['cognify.expense.category'].search([('code', 'in', list(accounts))], order='sequence'):
    target = accounts[cat.code]
    if cat.account_id != target:
        cat.account_id = target
        print(f'  ~ {cat.code:<8} -> {target.code} {target.name}')
    else:
        print(f'  = {cat.code:<8} -> {target.code} {target.name}')

print('\n--- remittance settings --------------------------------------------')
journal = env['account.journal'].search(
    [('type', '=', 'general'), ('company_id', '=', company.id)], limit=1
) or env['account.journal'].search([('type', '=', 'general')], limit=1)
env['res.config.settings'].create({
    'remittance_journal_id': journal.id,
    'remittance_cash_account_id': cash_account.id,
}).execute()
print(f'  ~ journal          -> {journal.code} {journal.name}')
print(f'  ~ cash account     -> {cash_account.code} {cash_account.name}')

print('\n--- verification ----------------------------------------------------')
# Re-read from scratch, exactly as the crediting code does.
env.invalidate_all()
cfg = env['res.config.settings'].sudo().get_remittance_config()
print(f"  stored journal : {cfg.get('remittance_journal_id')}")
print(f"  stored cash    : {cfg.get('remittance_cash_account_id')}")

unmapped = env['cognify.expense.category'].search(
    [('active', '=', True), ('account_id', '=', False)]
)
print(f"  categories without an account: {len(unmapped)} "
      f"{[c.code for c in unmapped] if unmapped else ''}")

env.cr.commit()
print(f'\nDone. {len(created)} account(s) created; commit written.')
