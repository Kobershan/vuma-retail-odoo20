# Product Pictures on Printed Documents — Odoo 20 setup & usage

Module: `custom-addons/cognify_product_images`

Everything below has already been applied to the `vuma_retail` database.

---

## What it does

| Requirement | How it is met |
|---|---|
| Picture on **picking slips** | The **Picking Operations** sheet (`stock.report_picking`) prints a thumbnail beside the product name. |
| Picture on **purchase orders** | The Purchase Order prints a thumbnail in the Description column. |
| Picture on **stock takes** | The **Count Sheet** printed from *Inventory → Adjustments → Physical Inventory* prints a thumbnail beside the product. |
| Lines without a product stay unchanged | A description-only purchase line prints exactly as Odoo ships it — no blank box, no error. |
| Products without a picture stay unchanged | Nothing is printed in their cell. |
| **Turn it off** | Each of the three documents has its own switch in *Settings → Inventory → Product Pictures*. |
| **Size** | One thumbnail size for all three documents: Small 1.2 cm / Medium 2 cm / Large 3 cm. |

The Delivery Slip is deliberately **not** touched: it is the customer-facing
document, and pictures on it change what goes outside the shop.

---

## Commands

```bash
cd /home/kob/Documents/vuma-retail

# Install
./venv/bin/python odoo/odoo-bin module install -c odoo.conf -d vuma_retail \
    cognify_product_images

# Run the module's tests (22 tests, rolls back, leaves no data)
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail \
    -u cognify_product_images --stop-after-init \
    --test-enable --test-tags=/cognify_product_images \
    --workers=0 --http-port=8199

# End-to-end against the RUNNING server, as a real logged-in user over HTTP:
# buy -> receive -> deliver -> count, printing each document and checking the
# pictures landed in the PDF. Writes PDFs to /tmp/opencode/live/.
./venv/bin/python ops_test_pictures_live.py

# Remove what that run created (the seeded logins stay)
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_cleanup_picture_test.py

# Re-create the test login
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_seed_picture_tester.py

# End-to-end check: builds a picking, a PO and a stock take with PNG and WebP
# pictures, renders all three through the real PDF engine, writes the PDFs to
# /tmp/opencode/pictures/ so they can be looked at. Rolls back everything.
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --http-port=8199 --log-level=error < ops_test_product_pictures.py
```

`--http-port=8199` avoids the port clash with the running service.

---

## Setting it up

**1. Put a picture on the product.** Nothing else is needed — the picture
prints wherever the product is printed.

*Products → the product → the product photo* (drag an image onto the form, or
use the menu). For a multi-variant product, set the picture on the **variant**
form and it overrides the template's for that variant only.

**2. Choose what gets pictures.**

*Settings → Inventory → Product Pictures*

| Setting | Effect |
|---|---|
| Picking Operations | Thumbnail on the Picking Operations sheet |
| Purchase Orders | Thumbnail on Purchase Orders |
| Stock Take Sheets | Thumbnail on the Count Sheet |
| Thumbnail Size | Small (1.2 cm), Medium (2 cm), Large (3 cm) |

Medium is the default: on a 40-line picking sheet the thumbnails still leave the
page count unchanged, and a picture is big enough to recognise on a shelf.

**3. Print.** The switches take effect on the next print; nothing is cached.

---

## How it works, and why it is built this way

**Pictures travel inside the document.** Each thumbnail is embedded as a
`data:` URI, not linked to `/web/image/...`. A link would make the PDF renderer
fetch every picture over HTTP while printing — slow, and it fails silently
behind a proxy or a login. Measured on a 40-line picking sheet with 40
photographic products: **+0.2 s and +424 KiB**, same number of pages.

**WebP is converted, everything else is not.** The PDF engine here is
wkhtmltopdf 0.12.6 (Qt WebKit), which **cannot decode WebP and drops it without
a word** — the space is simply blank on the printout. That was measured, not
assumed: JPEG, PNG, GIF and base64 SVG all render, WebP does not. So a WebP
picture is re-encoded to JPEG on the way in, capped at 256 px and flattened onto
white if it has transparency (JPEG has no alpha channel, and transparent areas
would otherwise turn black).

Odoo leaves WebP files alone when it builds the resized variants (see
`fields.Binary._image_process`), so `image_128` can still be the full-size
original — capping it here is what keeps the PDF small.

**Pillow does not know about WebP inside Odoo.** `odoo/tools/image.py` loads
Pillow with `Image.preinit()` (core formats only) and then pins
`Image._initialized = 2`, which stops Pillow from ever running its own `init()`
and registering the rest. A real server process therefore has **no WebP handler
at all**, and `Image.open()` on a WebP raises `UnidentifiedImageError`. This
module imports the plugin itself before converting. The trap is that any test
which imports `PIL.WebPImagePlugin` for its own fixtures hides the problem
completely — the tests have to run in a process that has not done so, which is
what `test_webp_converts_in_a_process_where_odoo_has_pinned_pillow` does.

**The 128 px variant is read**, not the original. It is stored as an attachment,
so it costs one small row read per product and is already larger than any
thumbnail box at print resolution.

**The thumbnail is top-aligned and inline.** A float would hang below the cell
on a one-line product name; `vertical-align: middle` pushes the name down towards
the bottom of a tall picture, where it reads as if it belongs to the next line.
Top alignment keeps each name beside its own picture.

**Lines with no product print nothing.** The template asks for a picture and
prints whatever comes back, empty included, so notes, sections and made-to-order
lines need no special handling.

**Pictures are read as superuser.** They are printed next to the product name,
which the document already shows to whoever is printing. A portal user
downloading a purchase order PDF has no read access to `product.product` and
should not get an `AccessError` over a cosmetic picture.

---

## Troubleshooting

**A picture is missing from a printed document**

1. Does the product actually have a picture? *Products → the product* shows it
   on the form.
2. Is that document's switch on in *Settings → Inventory → Product Pictures*?
3. Is the line the product itself, or a description-only line? Lines without a
   product never get a picture — that is intended.

**A picture is blank on paper but visible on screen**

The stored file is WebP and the PDF engine cannot draw it. This should not
happen — the module converts WebP — so check `data/logs/odoo.log` for
`cognify_product_images: cannot read the picture`, which is logged when a
picture cannot be read at all. If that line names a WebP product, the worker
running the print had no Pillow WebP handler; that only happens if the process
started before this module was installed, so restart the service.

**The picking sheet looks unchanged**

The Picking Operations sheet only prints its product table once the moves carry
reserved quantities. A draft or unreserved transfer prints no product rows for
Odoo's own reasons, with or without this module.

**A picture is bigger than it should be**

Switch the size down. Nothing else changes; the printed box is set in one place
(`THUMBNAIL_SIZES` in `models/product_product.py`).

---

## Files

```
custom-addons/cognify_product_images/
├── __manifest__.py
├── models/
│   ├── ir_qweb.py              # cognify_product_image() for templates + settings
│   ├── product_product.py      # constants, WebP handling, the shared helper
│   └── res_config_settings.py  # the three switches and the size
├── report/
│   ├── report_picking.xml      # Picking Operations
│   ├── report_purchase_order.xml
│   └── report_stocktake.xml    # Count Sheet
├── tests/test_product_images.py
└── views/res_config_settings_views.xml

ops_seed_picture_tester.py    # creates pictures.tester (password in .picturetesterpass)
ops_test_pictures_live.py     # 24 checks against the running server over HTTP
ops_cleanup_picture_test.py   # removes what the live run created
ops_test_product_pictures.py  # 17 checks in-process, writes sample PDFs
```

The three report files are template inheritances: each adds one line inside the
cell that already prints the product name, so Odoo's column layout (barcode,
From/To, lot and package columns) is untouched.

## Test logins

| Login | Password | For |
|---|---|---|
| `pictures.tester` | `.picturetesterpass` | The live suite: purchasing, inventory, sales, Settings. |
| `pictures.portal` | `PortalTest-2026` | A portal buyer, used to check picture access. |

Delete them with *Settings → Users → Users → Delete*.

**After changing Python, restart the service.** Odoo's registry reload does not
re-import addon modules that are already in `sys.modules`, so new module code
only reaches the running workers on restart:

```bash
sudo systemctl restart odoo
```