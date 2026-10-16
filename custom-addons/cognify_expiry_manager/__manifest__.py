{
    'name': 'Cognify Expiry Manager',
    'version': '1.0.0',
    'category': 'Supply Chain/Inventory',
    'summary': 'Expiry-date control for received and in-stock goods: FEFO picking, '
               'expiry date on delivery slips and configurable expiry alerts.',
    'description': """
Expiry Date Manager
===================

Sits on top of the standard ``product_expiry`` module and fills in the parts a
retail operation actually needs.

Goods received
--------------
The expiry date printed by the supplier can be typed on the **Purchase Order
line**. It is carried automatically onto the lot that gets created when the
receipt is validated, so the date on the physical label is the date on the
system. If nothing is typed, Odoo's own shelf-life calculation still applies.

Picking the closest to expiry
-----------------------------
Set a product category (or a single location) to **First Expiry First Out**. From
then on reservations and the detailed operations screen allocate the lot whose
expiry date comes first. The *Pick From* column on the transfer tells the
picker exactly which lot to take.

Alert window per product
------------------------
Each product carries its own alert lead time, expressed as a **value plus a
unit** (Days / Weeks / Months / Years), so one product can alert 1 day before
expiry while another alerts 3 months before. Months and years use real calendar
arithmetic, so "3 months before 31 August" is 31 May, not "90 days ago".

Alerts
------
When a lot crosses into its alert window and still has stock in an internal
location, the module can fire all four channels:

* an activity on the lot's chatter,
* an in-app notification popup,
* an email to the product's responsible user,
* a badge on the **Expiry Alerts** menu.

Every alert is recorded once per lot, so re-running the scheduled action does
not spam anyone. Changing a product's alert window re-arms its lots.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['stock', 'product_expiry', 'purchase_stock', 'mail'],
    'data': [
        'security/cognify_expiry_security.xml',
        'security/ir.access.csv',
        'data/ir_cron_data.xml',
        'data/mail_template_expiry.xml',
        'report/report_deliveryslip.xml',
        'report/report_expiry_stock.xml',
        'views/product_template_views.xml',
        'views/purchase_order_views.xml',
        'views/stock_lot_views.xml',
        'views/res_config_settings_views.xml',
        'views/cognify_expiry_menus.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cognify_expiry_manager/static/src/js/expiry_alert_plugin.js',
        ],
    },
    'installable': True,
    'application': True,
}