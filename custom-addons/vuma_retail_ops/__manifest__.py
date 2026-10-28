{
    "name": "Vuma Retail Operations",
    "version": "1.6.0",
    "category": "Sales/Retail",
    "summary": "Complete retail operations layer: procurement control, stock counts, "
               "POD and trips, debtor management, creditor claims and rebates, "
               "cash office and field approvals.",
    "description": """
Vuma Retail Operations
======================

The operational control layer that sits on top of the existing Vuma addons:

* purchase approvals, quote comparison, goods-receiving claims, returns to
  supplier, supplier agreements, rebates, supplier assets and scorecards;
* stock-take approval, cycle counts and consolidated picking by town;
* free-form pack sizes on one item (50ml, 1L, 6 Pack, Pallet), each with its
  own stock, barcode and price;
* product tabs for general details, spreadsheet pricing (gross cost,
  transport, nett cost, markup %, markup value and both selling prices, each
  one filling the others in), details, suppliers, recipes, printable
  shelf / price / barcode labels and used-in components, plus a stock
  department on every product;
* order checks, quote revisions, add-only customer maintenance, linked
  customers and live uplift approval;
* a Customer 360 tab with billing and delivery details, multiple contacts
  with mandatory email, account rating, customer groups, credit terms, a
  customer default discount and a combined invoice / credit note / payment
  history with a running balance;
* per-customer automatic statement dates, with a daily cron that emails the
  previous month's statement when it falls due;
* supplier-specific stock codes on each item, with one-click creation of one
  RFQ per supplier and the supplier code printed on the RFQ;
* dispatch trips, trip expenses, proof of delivery, delivery returns, failed
  deliveries and delivery KPIs;
* debtor ratings, consolidated statements, letters, forms, collections
  worklist and cashflow forecast;
* fixed assets, budgets, tax packs and an audit log;
* cash receipts, trip floats, cash-up sessions and cash exceptions.
""",
    "author": "Vuma Retail",
    "license": "LGPL-3",
    "depends": [
        "base",
        "mail",
        "portal",
        "web",
        "sale_management",
        "sale_margin",
        "purchase_stock",
        "stock",
        "stock_landed_costs",
        "mrp",
        "fleet",
        "fleet_maintenance",
        "account",
        "cognify_remittance",
        "customer_goods_pickup",
        "vuma_multi_tenant",
        "vuma_picking_slip",
        "vuma_credit_hold",
        "cognify_management_reports",
        "cognify_expiry_manager",
        "contacts",
    ],
    "data": [
        "security/vuma_retail_ops_security.xml",
        "security/ir.access.csv",
        "data/ir_sequence.xml",
        "data/vuma_retail_ops_data.xml",
        "data/pack_size_data.xml",
        "views/approval_views.xml",
        "views/procurement_views.xml",
        "views/supplier_codes_views.xml",
        "views/stock_views.xml",
        "views/pack_size_views.xml",
        "views/product_catalogue_views.xml",
        "views/sales_views.xml",
        "views/dispatch_views.xml",
        "views/debtor_views.xml",
        "views/customer_views.xml",
        "views/partner_lists_views.xml",
        "views/partner_domain_views.xml",
        "views/creditor_views.xml",
        "views/accounting_views.xml",
        "views/cash_views.xml",
        "views/report_views.xml",
        "report/report_templates.xml",
        "report/supplier_code_templates.xml",
        "report/report_actions.xml",
        "report/product_label_templates.xml",
        "report/product_label_actions.xml",
        "data/mail_templates.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": True,
}
