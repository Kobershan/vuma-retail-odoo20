{
    'name': 'Customer Goods Pickup & Receiving',
    'version': '1.1.0',
    'category': 'Sales/Field Sales',
    'summary': 'Collect customer goods against a posted invoice, receive and inspect '
               'them in the store, accept, part-accept or reject them, put the accepted '
               'quantity into stock and raise a traceable credit note — in that order.',
    'description': """
Customer Goods Pickup & Receiving
=================================

A sales representative collects goods from a customer, carries them back to the
store, and the store decides what actually goes back into sellable stock. The
money side only follows that decision.

Nothing reaches the books on collection
---------------------------------------
Creating a pickup, or confirming that the rep has the goods in the bag, touches
neither inventory nor accounting. There is no stock move, no quant, no journal
entry and no change to the customer's receivable until the store has physically
inspected the goods and accepted them.

The four steps that are kept apart
---------------------------------
1. **Collection** — the rep records what was taken from the customer and
   confirms collection. Still no stock, still no money.
2. **Physical receipt** — the store opens a receiving document and counts what
   was actually presented.
3. **Stock acceptance** — what the store accepts is moved into the warehouse
   through a genuine incoming stock transfer. Rejected quantity never becomes
   sellable stock: it is moved into a real **quarantine** location through a
   genuine incoming stock transfer of its own, so the goods have a traceable
   physical location, a real quant and a real value — but sit outside every
   warehouse, so availability, reservation and replenishment cannot see them.
   A manager can later re-inspect them, hand them back to the customer or
   scrap them, and every one of those is a real stock transfer too.
4. **Accounting credit** — an accountant reviews the accepted quantity and
   raises a customer credit note against the original invoice for those
   quantities only. The credit note is created in **draft**, so posting remains
   a deliberate act by a user who already holds the standard accounting
   permission.

Only an existing, posted, non-reversed customer invoice of the right customer
and company can be picked, and only the product lines on that invoice can be
collected against.

Working out what is still eligible
----------------------------------
Every pickup line shows the invoiced quantity, what previous credit notes have
already credited, what other open pickups have already reserved, and what is
therefore still collectable. The same arithmetic runs again when collection is
confirmed, so two representatives cannot quietly over-allocate the same
invoiced quantity.

Partial everything
------------------
Goods can be collected in several trips, presented at the store in several
receipts, part-accepted each time, and credited in several credit notes. Every
step is bounded by the quantity still outstanding, previously credited
quantities can never be credited twice, and rejected quantity is never
eligible.

Where the records live
----------------------
``Customer → Invoice → Pickup → Store receipt → accepted / rejected →
Credit note``, with smart buttons in both directions and the original invoice
number shown on the pickup and the receiving screen.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['stock', 'stock_account', 'account', 'sale_management'],
    'data': [
        'security/customer_goods_pickup_security.xml',
        'security/ir.access.csv',
        'data/ir_sequence.xml',
        # The shared virtual root of the customer-returns area. The company
        # internal children below it are created idempotently in Python the
        # first time they are needed, so they are not declared here.
        'data/stock_location.xml',
        # Wizard views and their actions are loaded first: the pickup form
        # references those actions by xml id, which Odoo resolves while the
        # view is being created.
        'views/goods_pickup_wizard_views.xml',
        'views/goods_pickup_views.xml',
        'views/goods_receipt_views.xml',
        'views/goods_quarantine_views.xml',
        'views/account_move_views.xml',
        'views/stock_picking_views.xml',
        'views/res_config_settings_views.xml',
        'views/customer_goods_pickup_menus.xml',
    ],
    'installable': True,
    'application': True,
}