# Customer Goods Pickup & Receiving — Odoo 20 setup & usage

Module: `custom-addons/customer_goods_pickup`

Everything below has already been applied to the `vuma_retail` database.

---

## What it does

A sales representative collects goods from a customer, carries them back to the
store, and the store decides what actually goes back into sellable stock. The
money side only follows that decision.

The module keeps four things apart, and never lets one stand in for another:

| Step | Who | What it changes |
| --- | --- | --- |
| Collection | Sales rep | Nothing. No stock, no journal entry, no receivable movement. |
| Physical receipt | Store | A receiving document with what actually turned up. |
| Stock acceptance | Store | Accepted quantity enters stock through a real Odoo incoming transfer. Rejected quantity never touches a quant. |
| Accounting credit | Accountant | A **draft** customer credit note for the accepted quantity only. Posting stays a separate, deliberate act. |

## Commands

```bash
cd /home/kob/Documents/vuma-retail

# Install (already done on vuma_retail)
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail -i customer_goods_pickup \
    --stop-after-init --workers=0 --http-port=8199

# Upgrade
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail -u customer_goods_pickup \
    --stop-after-init --workers=0 --http-port=8199

# Tests — rolls back, leaves no data behind
./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail -u customer_goods_pickup \
    --stop-after-init --test-enable --test-tags=/customer_goods_pickup \
    --workers=0 --http-port=8199

# Live walk-through of the whole workflow against a running database
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail --no-http \
    < ops_test_customer_goods_pickup_live.py
```

---

## The workflow

### 1. The representative collects the goods

`Customer Goods > My Goods Pickups > New`. Pick the posted customer invoice,
press **Add Invoice Lines**, tick what the customer handed back, enter the
quantity, then **Confirm Collection**.

The invoice list is filtered to posted, non-cancelled customer invoices of
this company and customer. Each offered line shows:

* **Invoiced** — what the customer was billed for,
* **Credited** — what earlier credit notes have already taken back,
* **On Other Pickups** — what other open pickups have reserved,
* **Still Collectable** — the ceiling for this line.

Confirming collection re-checks that arithmetic under a row lock, so two
representatives cannot quietly take the same invoiced quantity. Once
confirmed, the quantities are frozen: only a Goods Pickup Manager can change
them.

### 2. The store receives the goods

`Customer Goods > Store Receiving`, or **Start Store Receiving** on the
pickup. The receiving sheet lists every product still outstanding, pre-filled
with what the representative said they were bringing. The store changes the
numbers to what actually turned up:

* **Presented** — physically in front of the counter,
* **Accept Into Stock** — what the store will sell again,
* **Rejected** — the difference, which needs a reason.

Barcode scanning works with any USB reader that types the code and presses
Enter; nothing depends on the `stock_barcode` module.

**Reject Delivery** turns the whole delivery down even when the goods are in
good condition — they do not qualify for return, the wrong items were
collected, or company policy says no. It asks for a reason, inspection notes,
supporting photographs and where the goods are going.

### 3. Accepted goods enter stock

Confirming the receipt creates a genuine `stock.picking` of type *Receipts*,
from the customer location into the configured warehouse, and validates it the
way Odoo validates any other receipt. Valuation, costing and traceability are
Odoo's own — the module never writes a quant by hand. Incoming moves are
valued at the price the customer was invoiced, so stock and the credit note
tell the same story.

Tracked products get a lot (one serial number per unit for serial-tracked
goods).

**Rejected goods are never booked into stock.** They are recorded against a
holding location (`Customer Returns Rejected`, created under the warehouse the
first time it is needed, or whatever is set in Settings) with a mandatory
reason and a disposition, so the goods have a traceable physical home without
ever being counted as sellable stock.

### 4. The accountant issues the credit

`Customer Goods > Goods Awaiting Credit`. Each receipt shows what was accepted,
what is already credited and what is still free. **Approve Credit**, then
**Create Credit Note**.

The credit note is built with Odoo's own reversal of the original invoice, so
customer, company, currency, unit price, discount and taxes are whatever the
invoice said. Lines that are not being credited are removed; the rest are cut
down to the credited quantity. The note is left in **draft** — posting it is a
separate act for a user who already holds Odoon's standard accounting
permission.

Declining the credit is equally available and equally recorded. It does **not**
remove the goods from stock: accepted goods stay in stock and stay on the
books, only the money is refused.

---

## Working out what is still eligible

Every pickup line and every offered invoice line is derived from the same
arithmetic:

```
eligible = invoiced
         - already credited by posted credit notes
         - reserved by other open pickups
```

Credit notes raised **outside** the module still count: they are matched onto
the invoice's own lines of the same product, oldest line first. Several
pickups, several receipts and several partial credit notes can therefore all
exist against one invoice without any of them exceeding what was invoiced.

A draft credit note *reserves* its quantity without crediting anything, so a
second note cannot be raised for the same units, and the customer is never
shown as credited before the note is posted.

---

## Permissions

Access is granted through four groups, each implying the standard Odoo group
that already fits the role. The permissions are enforced in Python as well as
hidden in the views.

| Group | Privilege | Implies | Can | Cannot |
| --- | --- | --- | --- | --- |
| Goods Pickup: Sales Representative | Sales | `sales_team.group_sale_salesman`, `account.group_account_readonly` | Raise pickups, pick eligible invoices, record and confirm collection, see receiving outcomes and rejection reasons | Receive goods, validate stock, approve or raise credits |
| Goods Pickup: Store Receiving | Inventory | `stock.group_stock_user`, `account.group_account_readonly` | Open the receiving screen, inspect, accept / part-accept / reject, validate the permitted stock transfer, see the original invoice | Touch the credit workflow, reverse stock, post a credit note |
| Goods Pickup: Accountant | Accounting | `account.group_account_invoice` | Review accepted quantity, approve or decline the credit, raise the credit note, post it as Odoo normally allows | Re-count the goods or change what was received |
| Goods Pickup: Manager | Inventory | the two above, plus `base.group_system` | Everything above, plus authorised corrections: cancel a confirmed pickup, reverse a validated receipt, reopen a credit decision | — |

The stock manager is promoted to the Goods Pickup Manager.

---

## States

**Pickup** — `Draft → Collected → In Transit → Awaiting Store Receipt`, and
from receiving `Partially Received / Received / Partially Rejected / Rejected`,
plus `Cancelled`.

The status is recomputed from what the store actually did, not typed:

* something still outstanding → *Partially Received*,
* nothing accepted but something rejected → *Rejected*,
* something accepted and something rejected → *Partially Rejected*,
* everything presented and accepted → *Received*.

**Store receipt** — `Being Received → Received`, plus `Cancelled`.

**Credit** — a separate status bar on the receipt:
`Awaiting Credit Approval → Approved For Credit → Draft Credit Note → Credit Note
Posted`, with `Credit Declined` as the branch where the store's goods are kept
but the money is refused. Nothing reports a credit that has not been posted.

---

## Reports and filters

`Customer Goods > All Goods Pickups` carries the filters the shop actually
asks for:

* Draft / Collected / **Goods In Transit** / **Awaiting Store Receipt** /
  **Partially Received** / Rejected / Completed / Cancelled
* **Accepted Goods Awaiting Credit**
* **My Pickups**
* Group by status, sales representative, customer, warehouse, collection month

Two dedicated pivot views are on the menu for managers:
**Outstanding By Sales Representative** and **Returns By Customer And Invoice**.

---

## Corrections and reversals

Nothing confirmed is ever deleted:

* a confirmed pickup cannot be unlinked — cancel it, with a reason and a note
  of what happened to the goods;
* a validated receipt cannot be unlinked or re-edited — a Goods Pickup Manager
  reverses it, which creates a proper outgoing transfer so the inventory
  history still adds up;
* cancelling a pickup whose goods are already in stock is refused until that
  transfer exists;
* a posted credit note blocks both, because the money has to be reversed first.

---

## Files

```
custom-addons/customer_goods_pickup/
├── __init__.py
├── __manifest__.py                       # depends on stock, account, sale_management
├── access.py                             # role names + the server-side group guard
├── units.py                              # UoM helpers for Odoo 20's unit hierarchy
├── data/
│   └── ir_sequence.xml                   # PICK-00001, RCP-00001
├── models/
│   ├── goods_pickup.py                   # the pickup and its state machine
│   ├── goods_pickup_line.py              # what was collected, and what is left
│   ├── goods_receipt.py                  # store receiving, stock, reversal, credit
│   ├── goods_receipt_line.py             # presented / accepted / rejected
│   ├── account_move.py                   # credit notes, linked both ways
│   ├── stock_picking.py                  # transfer -> receipt -> pickup smart buttons
│   └── res_config_settings.py            # rejected-goods location, rep notification
├── security/
│   ├── customer_goods_pickup_security.xml
│   └── ir.access.csv
├── views/
│   ├── goods_pickup_wizard_views.xml     # loaded first: the forms reference its actions
│   ├── goods_pickup_views.xml
│   ├── goods_receipt_views.xml
│   ├── account_move_views.xml
│   ├── stock_picking_views.xml
│   ├── res_config_settings_views.xml
│   └── customer_goods_pickup_menus.xml
├── wizard/
│   ├── goods_pickup_line_wizard.py       # add invoice lines
│   ├── goods_receipt_wizard.py           # the Store Receiving screen
│   ├── goods_receipt_reject.py           # reject a whole delivery
│   └── goods_pickup_cancel.py            # cancel, with reason and disposition
└── tests/
    ├── test_goods_pickup.py              # the workflow, end to end
    ├── test_goods_pickup_security.py     # the four roles
    └── test_goods_pickup_integrity.py    # views, model wiring, sequences, groups
```

---

## Ops scripts

| Script | What it does |
| --- | --- |
| `ops_test_customer_goods_pickup_live.py` | Walks the whole workflow against a live database — posts an invoice, collects, part-accepts, credits, posts the credit, refuses an over-collection and rejects a delivery — printing the stock and the money either side of every step. Rolls back, so it leaves no data. Run it through `odoo shell`. |

---

## Settings

`Settings > Inventory > Customer Goods Pickup`

* **Rejected Goods Location** — where rejected goods are recorded. Leave it
  empty and the module uses (and creates) *Customer Returns Rejected* under
  the destination warehouse.
* **Notify The Sales Representative** — mention the representative on the
  pickup every time the store finishes receiving it.

---

## Notes on Odoo 20

A few things behave differently from earlier versions and the module follows
the installed code rather than the old habits:

* `stock.move` uses `uom_id`, not `product_uom`, and has no `name` field —
  the description lives on `description_picking_manual`.
* `uom.uom` has no `category_id`; a unit's family is its `relative_uom_id`
  chain, which `units.py` walks.
* `product.template` splits `type` (`consu` / `service` / `combo`) from
  `is_storable`, and untracked goods have `tracking = False` rather than
  `'none'`.
* `account.move` links a reversal with `reversed_entry_id`; there is no
  `number` field, only `name`.
* `ir.rule` was merged into `ir.access`, which now carries the `domain`
  column, so the access rows are scoped by group in `ir.access.csv`.
* Odoo resolves field defaults from the field definition rather than by
  calling `default_get` on create, so the wizards build their lines in
  `create()`.
* `_read_group` aggregates are written `field:sum`, not `sum(field)`.