{
    'name': 'Vuma Consolidated Picking Slip',
    'version': '1.7.0',
    'category': 'Sales/Sales',
    'summary': 'Turn selected customer invoices into one consolidated picking '
               'slip or a delivery document for the truck.',
    'description': """
Consolidated Picking Slip
=========================

The warehouse often receives a batch of customer invoices and has to pick the
goods for all of them in one trip. This module adds **Create Picking Slip** to
the customer invoice list:

1. select any number of customer invoices;
2. press **Create Picking Slip**;
3. one consolidated picking slip opens, listing the goods once per product
   with the quantities added together across every selected invoice.

The slip is also saved under **Sales > Picking Slips**. Open it there
to see every invoice on the trip, print the blank PDF for a paper pick, or use
the large-button **Tablet Picking** screen. On a tablet the picker taps the
quantity actually found, marks each line picked as they go, and finishes the
slip. The captured quantities are then included when the slip is printed.

The slip is a document only. It does not reserve stock, create transfers, or
touch accounting in any way. The delivery itself stays in Odoo's normal
Inventory flow.

Only storable products are listed. Services, section/note lines, cancelled
invoices and zero or negative quantities are skipped. Quantities are converted
to each product's base unit of measure so the same product sold in different
packaging still appears as a single line. If the selected invoices use stock
from more than one company, the slip shows a Company column and keeps the
companies on separate lines, because they are picked from different
warehouses.

Invoices may be draft or posted; the slip is useful while the paperwork is
still being prepared. Credit notes and vendor bills are ignored, and the user
gets a clear message if the selection contains nothing that can be picked.

Delivery Documents
==================

The same invoice selection can also become a **Delivery Document** for one
truck load. It records:

* every invoice loaded on the truck, from Trade Rite, Siyaya DC or both;
* a clear weight summary in the order dispatch reads it: **Total Product
  Weight**, then **Pallets**, then **Boxes**, then **Total Weight** (product
  weight + pallet weight + box weight, each counted exactly once);
* the pallets on the truck -- how many, and how much each weighs -- typed
  in by dispatch on the document's **Pallets** tab;
* the cardboard boxes on the truck -- how many, and how much each weighs --
  typed in on the document's **Boxes** tab;
* a separate **Invoices on Account** section, with its own total rand value;
* a separate **Cash Invoices** section, with its own total rand value;
* the **Total Rand Value (Both)** of the cash and on-account sections
  together;
* the date and time the document was generated and the user who generated
  it;
* which vehicle from the fleet carries the load, and who is on the truck: a
  driver plus at most two helpers, all three picked from employees, so one
  truck never carries more than three people.

Part-paid invoices appear in both invoice sections: the amount already paid
under **Cash** and the balance still owing under **On Account**. On a mixed
load the company is printed on every invoice and product line, and the
header names both companies.

A delivery document is printable and remains available under
**Sales > Delivery Documents**. Invoices can still be added to or removed from
the document before it is printed.
""",
    'author': 'Vuma Retail',
    'license': 'LGPL-3',
    'depends': ['account', 'fleet', 'hr', 'sale_management', 'stock'],
    'data': [
        'security/ir.access.csv',
        'data/ir_sequence.xml',
        'report/consolidated_picking_slip_report.xml',
        'report/picking_slip_report.xml',
        'report/delivery_document_report.xml',
        'views/vuma_picking_slip_views.xml',
        'views/vuma_delivery_document_views.xml',
        'views/account_move_views.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'vuma_picking_slip/static/src/js/picking_slip_tablet.js',
            'vuma_picking_slip/static/src/xml/picking_slip_tablet.xml',
            'vuma_picking_slip/static/src/scss/picking_slip_tablet.scss',
        ],
    },
    'installable': True,
    'application': False,
}
