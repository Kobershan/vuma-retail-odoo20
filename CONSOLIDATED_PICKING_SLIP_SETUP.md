# Consolidated Picking Slip — usage

Module: `custom-addons/vuma_picking_slip`

## What it does

Select several customer invoices in one list and turn them into a single
consolidated picking slip. Quantities are added together per product, so a
product on three invoices appears once with the total quantity.

The slip is saved as a real record under **Sales → Picking Slips**, so
a picker can open it on a tablet, enter what was actually found, mark each line
as picked, and finish the trip. The same record can be printed as a PDF, with
the captured quantities on it.

The slip is a document only: it does **not** reserve stock, create transfers,
or post anything to accounting.

## Use it from invoices

1. Open **Sales → Invoicing → Customer Invoices** (or the cross-company invoice
   list).
2. Tick the invoices for this picking trip.
3. Press **Create Picking Slip**.
4. The tablet picking screen opens for the new saved slip.

**Print Picking PDF** next to it skips the saved record and prints a blank
picking sheet straight from the selected invoices.

Draft and posted invoices are both accepted. Credit notes, vendor bills,
services, section/note lines and zero-quantity lines are left off.

## Delivery document for a truck load

The same invoice selection can also become a **Delivery Document**:

1. Select the invoices whose goods are going on the truck. The selection may
   mix **Trade Rite** and **Siyaya DC** invoices; the document prints both
   companies and shows the company on every invoice and product line.
2. Press **Create Delivery Document** on the invoice list.
3. The saved document opens with:
   * every invoice on the truck, and the option to add or remove invoices;
   * a **Load Weight** summary in the order dispatch reads it:
     **Total Product Weight** (invoiced goods only), then **Pallets**, then
     **Boxes**, then **Total Weight** (product weight + pallet weight + box
     weight, each counted exactly once);
   * the pallets physically loaded -- how many, and how much each weighs --
     typed in on the **Pallets** tab;
   * the cardboard boxes physically loaded -- how many, and how much each
     weighs -- typed in on the **Boxes** tab;
   * the date and time the document was generated and the user who generated
     it;
   * the **Invoices on Account** section with its own **Total Rand Value on
     Account**: every invoice with money still owing;
   * the **Cash Invoices** section with its own **Total Rand Value Cash**:
     every invoice with money already received;
   * the **Total Rand Value (Both)**: the cash and on-account sections added
     together, equal to the selling value of the goods on the truck
     including tax.
4. Press **Print Delivery Document** for the PDF.

Fully paid invoices appear only under **Cash Invoices**; unpaid and draft
invoices appear only under **Invoices on Account**. A part-paid invoice
appears in both sections: the amount already paid under Cash and the balance
still owing under On Account.

Saved documents are listed under **Sales → Delivery Documents**. The document
does not reserve or move stock. It is intended to travel with the truck and to
give dispatch and the cash office one summary of the load: weight on one side,
the cash/account split on the other.

## Pick on a tablet

Open **Sales → Picking Slips** and press **Pick**, or press
**Tablet Picking** on the slip form.

On the tablet screen:

* each product is a large card showing the ordered quantity, barcode and
  source location;
* **−** / **+** and the large number box set the quantity actually found;
* **Tap to pick** marks the line done and fills in the full ordered quantity;
  reduce the number first (or afterwards) to record a short pick;
* every change is written to the saved slip immediately, so putting the tablet
  down does not lose the work;
* **Print PDF** downloads the slip with the quantities captured so far;
* **Finish** closes the trip, but only when every line has been marked picked.
  Mark a missing line as picked with quantity `0` to finish with a shortage;
* **Reopen** puts a finished slip back into picking mode.

The same form view remains available for office users, with an editable line
list and the same PDF button.

## How the lines are built

* Only storable products are considered.
* Quantities are converted to each product's base unit of measure, then added
  together.
* Lines are grouped per product **per source company**, because goods from
  different companies live in different warehouses. Single-company selections
  therefore show exactly one line per product.
* When mixed-basket invoice lines carry a source location, the slip prints the
  location and the quantity taken from it. This uses the optional
  `vuma_source_location_id` / `vuma_source_company_id` fields from
  `vuma_multi_tenant` when that module is installed.

## Install / upgrade

```bash
cd /home/kob/Documents/vuma-retail

# First install
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail -i vuma_picking_slip \
    --stop-after-init --workers=0 --http-port=8199

# Upgrade after changes
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail -u vuma_picking_slip \
    --stop-after-init --workers=0 --http-port=8199

# Tests
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail -u vuma_picking_slip \
    --stop-after-init --test-enable --test-tags=/vuma_picking_slip \
    --workers=0 --http-port=8199
```

Reload the running server after an upgrade:

```bash
kill -HUP 2125
```

## Files

```
custom-addons/vuma_picking_slip/
├── data/ir_sequence.xml                         # PS/YYYY/00001 numbering
├── models/account_move.py                       # invoice aggregation + buttons
├── models/vuma_picking_slip.py                  # saved slip and captured lines
├── models/vuma_delivery_document.py             # truck load weight + cash split
├── report/consolidated_picking_slip_report.xml  # blank PDF from invoices
├── report/picking_slip_report.xml               # PDF from a saved slip
├── report/delivery_document_report.xml          # PDF for a truck load
├── security/ir.access.csv                       # access and company rules
├── static/src/js/picking_slip_tablet.js         # tablet picking screen
├── static/src/scss/picking_slip_tablet.scss
├── static/src/xml/picking_slip_tablet.xml
├── views/vuma_picking_slip_views.xml            # list/form/search + Sales menu
├── views/vuma_delivery_document_views.xml       # delivery document views + menu
├── views/account_move_views.xml                 # invoice list buttons
├── tests/test_consolidated_picking_slip.py
└── tests/test_delivery_document.py
```
