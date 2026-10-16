{
    'name': 'Cognify Product Pictures on Documents',
    'version': '1.0.2',
    'category': 'Inventory/Inventory',
    'summary': 'Print the product picture on picking slips, purchase orders and '
               'stock take sheets, with a switch per document.',
    'description': """
Product Pictures on Printed Documents
=====================================

Paperwork is read by people standing over a box or a shelf, not at a desk.
Printing what the goods actually look like removes a lot of guessing: the
picker stops matching a code to a product, the storekeeper stops ordering the
wrong size, and the person doing the stock take stops counting the wrong tin.

Where the pictures appear
-------------------------
* **Picking Operations** — the internal sheet the picker works from.
* **Purchase Orders** — so the order can be checked against the shelf.
* **Stock Take Sheets** — the count sheet printed from Physical Inventory.

The picture is printed *inside* the product cell, to the left of the name, so
the existing column layout (and the barcode / lot / package columns) is left
exactly as Odoo draws it. Lines with no product, or a product with no picture,
print exactly as they did before.

Each document has its own switch under **Settings → Inventory → Product
Pictures**, so a shop can show pictures while picking but keep the purchase
order clean if a supplier objects. One thumbnail size is shared by all three.

How the picture is embedded
---------------------------
The picture travels inside the document as a ``data:`` URI rather than a link
to ``/web/image/...``, because the PDF renderer would otherwise have to fetch
it over HTTP while printing, which is slow and fails silently behind a
password or a proxy.

The PDF engine this server uses (wkhtmltopdf 0.12.6, Qt WebKit) drops WebP
without a word -- the space is simply blank on the printout. WebP pictures
are therefore re-encoded to JPEG on the way in, capped at 256px so a
40-line picking sheet stays a sane file size. JPEG, PNG, GIF and SVG are
passed through untouched.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['stock', 'purchase'],
    'data': [
        'views/res_config_settings_views.xml',
        'views/purchase_order_views.xml',
        'report/report_picking.xml',
        'report/report_purchase_order.xml',
        'report/report_stocktake.xml',
    ],
    'installable': True,
    'application': True,
}