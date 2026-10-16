"""Repair Mixed Basket access + make stock ownership checkable.

Run after installing/updating `vuma_multi_tenant`:

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --no-http < ops_fix_mixed_basket_access.py

Why this exists
---------------
The module's post-init hook granted the two trading companies to the admins
but never granted `group_vuma_mixed_basket_user`. Every cross-company menu and
action is gated on that group, so the whole feature stayed invisible: users
reached a perfectly ordinary single-company Odoo and had nothing to check.

It also prints an ownership audit so it is obvious which company every unit of
stock belongs to. `stock.quant.company_id` is derived from
`location_id.company_id`, so a product stocked in two warehouses is only
traceable because each quant sits in that company's own warehouse.
"""
from odoo import _

from odoo.addons.vuma_multi_tenant.hooks import grant_mixed_basket_access

# Internal users that should work across both companies. Add logins here.
TARGET_LOGINS = [
    'admin',
    'demo.sales.moyo',
    'demo.sales.ncube',
    'demo.sales.dube',
    'demo.sales.kamau',
]


def hdr(text):
    print("\n" + "=" * 78)
    print(text)
    print("=" * 78)


hdr("1. GRANTING MIXED BASKET ACCESS")
users = grant_mixed_basket_access(env, only_logins=TARGET_LOGINS)
if not users:
    print("   no matching logins found:", TARGET_LOGINS)
for user in users:
    print(f"   {user.login:<22} companies={sorted(user.company_ids.mapped('name'))}")
env.cr.flush()

hdr("2. STOCK OWNERSHIP AUDIT (which company owns what)")
Quant = env['stock.quant'].sudo()
for company in env['res.company'].sudo().search([], order='id'):
    quants = Quant.search([
        ('quantity', '>', 0),
        ('location_id.company_id', '=', company.id),
    ])
    locations = sorted({q.location_id.complete_name for q in quants})
    print(f"   {company.name:<14} {len(quants):>3} quants  "
          f"qty={sum(quants.mapped('quantity')):>10,.0f}  {locations}")

orphan = Quant.search([
    ('quantity', '>', 0),
    ('location_id.company_id', '=', False),
    ('location_id.usage', 'in', ('internal', 'view')),
])
print(f"\n   warehouse stock with NO company: {len(orphan)}")

hdr("3. PRODUCTS OWNED PER COMPANY")
Template = env['product.template'].sudo()
for company in env['res.company'].sudo().search([], order='id'):
    owned = Template.search([('company_id', '=', company.id), ('sale_ok', '=', True)])
    print(f"   {company.name:<14} {len(owned):>2} owned: "
          f"{sorted(p.default_code for p in owned)}")
shared = Template.search([('company_id', '=', False),
                          ('vuma_cross_company_sale', '=', True),
                          ('product_variant_ids', '!=', False)])
print(f"   shared / unowned: {len(shared)} "
      f"(stock sits in each company's warehouse, so the ORDER LINE must say "
      f"which company ships it)")

hdr("4. MIXED BASKET COVERAGE")
SOL = env['sale.order.line'].sudo()
blank = SOL.search_count([('product_id', '!=', False),
                          ('vuma_source_company_id', '=', False)])
with_src = SOL.search_count([('product_id', '!=', False),
                             ('vuma_source_company_id', '!=', False)])
print(f"   order lines with a stock source : {with_src}")
print(f"   order lines without one          : {blank}")
split = env['sale.order'].sudo().search([('vuma_original_mixed_quote_id', '!=', False)])
print(f"   orders produced by a split basket: {len(split)}")

env.cr.commit()
print("\ncommitted")