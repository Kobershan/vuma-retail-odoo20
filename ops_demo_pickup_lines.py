"""Show what the representative now sees, and what the flow does with it.

Walks the two real paths the request describes:

* the browser's path -- the record is built in memory and the invoice is chosen,
  which runs the onchange, so the form fills itself before anything is saved;
* the saved path -- `action_reload_invoice_lines`, which is the same fill on
  demand, for when the invoice has been amended since.

Then it enters an amount on one line, leaves the rest alone, and confirms.

Rolls back at the end, so it creates nothing.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_demo_pickup_lines.py
"""
partner = env['res.partner'].search(
    [('customer_rank', '>', 0), ('invoice_ids.move_type', '=', 'out_invoice'),
     ('invoice_ids.state', '=', 'posted')], limit=1)
invoices = partner.invoice_ids.filtered(
    lambda move: move.move_type == 'out_invoice' and move.state == 'posted')
if len(invoices) < 2:
    raise SystemExit('need a customer with two posted invoices to show the choice')

warehouse = env['stock.warehouse'].search(
    [('company_id', '=', env.company.id)], limit=1)
location = env['stock.location'].search([
    ('usage', '=', 'internal'), ('warehouse_id', '=', warehouse.id)], limit=1)

print(f"customer: {partner.name}")
print(f"their posted invoices ({len(invoices)}), and which one is chosen:")
for move in invoices.sorted('invoice_date'):
    print(f"  {move.name}  {move.invoice_date}  "
          f"{move.currency_id.symbol}{move.amount_total:,.2f}")

invoice = invoices.filtered(
    lambda m: len(m.invoice_line_ids.filtered(
        lambda line: line.display_type == 'product' and line.product_id)) >= 2
).sorted('invoice_date')[-1]
products = invoice.invoice_line_ids.filtered(
    lambda line: line.display_type == 'product' and line.product_id)
print(f"\nchosen: {invoice.name}, {len(products)} product line(s)")

# ---- the browser's path: choose the invoice, the form fills itself ----------
form = env['customer.goods.pickup'].new({
    'partner_id': partner.id,
    'invoice_id': invoice.id,
    'warehouse_id': warehouse.id,
    'dest_location_id': location.id,
    'pickup_reason': 'customer_request',
})
form._onchange_invoice_id()
print(f"\non choosing {invoice.name}, the form shows "
      f"{len(form.line_ids)} line(s):")
for line in form.line_ids:
    print(f"  {line.product_id.display_name:<30} invoiced "
          f"{line.invoiced_qty:>7.2f} {line.product_uom_id.name:<9} "
          f"collectable {line.eligible_qty:>7.2f}   "
          f"qty collected {line.qty_collected:>6.2f}")
assert len(form.line_ids) == len(products), \
    'the form did not offer every product line of the invoice'
assert all(not line.qty_collected for line in form.line_ids), \
    'nothing should be pre-filled as collected'

# ---- the saved path, then entering what is being taken --------------------
pickup = env['customer.goods.pickup'].create({
    'partner_id': partner.id,
    'invoice_id': invoice.id,
    'warehouse_id': warehouse.id,
    'dest_location_id': location.id,
    'pickup_reason': 'customer_request',
})
pickup.action_reload_invoice_lines()
print(f"\n{pickup.name} after reloading: "
      f"{len(pickup.line_ids)} line(s) offered")
assert len(pickup.line_ids) == len(products)

first = pickup.line_ids[0]
pickup.line_ids[0].write({'qty_collected': first.eligible_qty})
print(f"  entered {first.eligible_qty:g} {first.product_uom_id.name} of "
      f"{first.product_id.display_name}")
print(f"  left the other {len(pickup.line_ids) - 1} line(s) at zero, "
      f"meaning not taking them")

pickup.action_confirm_collection()
print(f"\n  confirmed: state={pickup.state}, {len(pickup.line_ids)} line(s), "
      f"total collected {pickup.total_collected_qty:g}")
assert len(pickup.line_ids) == 1, \
    'a line left at zero should simply not be collected'
assert pickup.state == 'collected'
assert pickup.total_collected_qty == pickup.line_ids[0].qty_collected
print("  zero-quantity lines were dropped rather than rejected")

env.cr.rollback()
print("\nrolled back; nothing was written")
