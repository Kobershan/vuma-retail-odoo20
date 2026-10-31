# -*- coding: utf-8 -*-
{
    'name': 'Vuma Multi-Tenant Retail',
    'version': '2.2.3',
    'category': 'Sales/Retail',
    'summary': 'Trade Rite + Siyaya DC from ONE screen: mixed-basket quotes, '
               'cross-company cash collection and crediting.',
    'description': """
Vuma Multi-Tenant Retail v2
===========================

Two trading companies operated from one login without ever switching profile:

* **Trade Rite** and **Siyaya DC**, fully configured (warehouses, sequences,
  journals, pricelists, payment terms, fiscal positions).
* **One quote → two companies**: the sales rep types a single mixed quote;
  lines can pull stock from either company (the product selector shows
  “Stock Source” for every product). Clicking **Confirm** automatically
  splits it into two company quotations which are then confirmed and
  invoiced independently (TR/INV/... and SY/INV/...).
* **Every line is traceable**: each order line records the company whose
  stock it uses, the exact stock location it is drawn from and the quantity
  available there — on the quote, on the stock move and on the invoice line.
  Confirming a basket with an unassigned line is refused rather than
  guessed. Use *Vuma Cross-Company → Operations → Stock / Company
  Traceability* to audit or repair any unattributed stock.
* **Cash collection in one screen**: reps collect money for both companies
  on one session; every payment lands in the right company's cash journal.
* **Crediting across companies**: the credit wizard lists the customer's
  open invoices from BOTH companies, user picks which invoices to credit,
  and one draft credit note is created per company.
* **Company visible everywhere**: lists, dashboards, invoices and payments
  show a Company column; users granted Mixed Basket see both companies'
  records thanks to standard multi-company record rules.
""",
    'author': 'Vuma Retail',
    'license': 'LGPL-3',
    'depends': [
        'base', 'sale', 'sale_management', 'stock', 'stock_account',
        'account', 'product', 'web', 'mail', 'l10n_za',
    ],
    'data': [
        'security/vuma_multi_tenant_security.xml',
        'security/ir.access.csv',
        'data/company_data.xml',
        'data/sequence_data.xml',
        'data/warehouse_data.xml',
        'data/pricelist_data.xml',
        'data/payment_term_data.xml',
        'data/fiscal_position_data.xml',
        'data/product_seed_data.xml',
        'views/vuma_cross_company_views.xml',
        'views/sale_order_views.xml',
        'views/product_template_views.xml',
        'views/res_users_views.xml',
        'views/vuma_multi_tenant_menus.xml',
    ],
    'post_init_hook': 'vuma_post_init',
    'assets': {
        'web.assets_backend': [
            'vuma_multi_tenant/static/src/dashboard_company_switch/company_switch.js',
            'vuma_multi_tenant/static/src/dashboard_company_switch/company_switch.xml',
            'vuma_multi_tenant/static/src/dashboard_company_switch/company_switch.scss',
        ],
    },
    'installable': True,
    'application': True,
}