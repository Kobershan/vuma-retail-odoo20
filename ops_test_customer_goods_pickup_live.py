"""End-to-end check of the goods pickup workflow against a live database.

Run it through ``odoo shell`` against the database the module is installed
in. It walks the whole path a shop actually walks and prints the stock and
the money either side of every step, then rolls back so no data is left.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail --no-http \
        < ops_test_customer_goods_pickup_live.py
"""

from odoo import Command, fields

ok, fail = [], []


def check(label, condition, detail=''):
    (ok if condition else fail).append(
        '%s%s' % (label, ' -> %s' % detail if detail else ''))


def on_hand(product, location):
    quants = env['stock.quant'].search([
        ('product_id', '=', product.id),
        ('location_id', 'child_of', location.id),
    ])
    return sum(quants.mapped('quantity'))


def receivable(partner, company):
    return sum(env['account.move.line'].search([
        ('partner_id', '=', partner.id),
        ('account_type', '=', 'asset_receivable'),
        ('parent_state', '=', 'posted'),
        ('company_id', '=', company.id),
    ]).mapped('balance'))


company = env.ref('base.main_company')
warehouse = env['stock.warehouse'].search(
    [('company_id', '=', company.id)], limit=1)
journal = env['account.journal'].search(
    [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1)
tax = env['account.tax'].search(
    [('type_tax_use', '=', 'sale'), ('company_id', '=', company.id)], limit=1)
goods = env.ref('product.product_category_goods')

manager = env['res.users'].search(
    [('all_group_ids', 'in', [env.ref('stock.group_stock_manager').id])],
    limit=1)
assert manager, 'this database needs a stock manager'
manager.group_ids = [Command.link(env.ref(
    'customer_goods_pickup.group_goods_pickup_manager').id)]

customer = env['res.partner'].create({
    'name': 'Pickup Check Ltd', 'customer_rank': 1})
product = env['product.product'].create({
    'name': 'Pickup check widget', 'default_code': 'PCW-1',
    'type': 'consu', 'is_storable': True, 'list_price': 30.0,
    'standard_price': 20.0, 'categ_id': goods.id,
    'taxes_id': [(6, 0, tax.ids)] if tax else []})

print('\n1. Post a customer invoice')
invoice = env['account.move'].create({
    'move_type': 'out_invoice',
    'partner_id': customer.id,
    'invoice_date': fields.Date.context_today(company),
    'journal_id': journal.id,
    'invoice_line_ids': [Command.create({
        'product_id': product.id,
        'quantity': 8,
        'price_unit': 30.0,
        'tax_ids': [Command.set(product.taxes_id.ids)],
        'name': product.name,
    })],
})
invoice.action_post()
check('invoice posted', invoice.state == 'posted', invoice.name)

print('2. Representative raises a pickup and confirms collection')
stock_before = on_hand(product, warehouse.lot_stock_id)
money_before = receivable(customer, company)
pickup = env['customer.goods.pickup'].create({
    'partner_id': customer.id,
    'invoice_id': invoice.id,
    'sales_rep_id': manager.id,
    'warehouse_id': warehouse.id,
    'dest_location_id': warehouse.lot_stock_id.id,
    'pickup_reason': 'customer_request',
})
pickup.write({'line_ids': [Command.create({
    'invoice_line_id': invoice.invoice_line_ids[0].id,
    'product_id': product.id,
    'product_uom_id': invoice.invoice_line_ids[0].product_uom_id.id,
    'qty_collected': 5,
    'return_reason': 'customer_request',
})]})
check('pickup reference', pickup.name.startswith('PICK-'), pickup.name)
check('invoice number on the pickup', pickup.invoice_number == invoice.name)
pickup.action_confirm_collection()
check('collection moved no stock',
      on_hand(product, warehouse.lot_stock_id) == stock_before)
check('collection moved no money',
      receivable(customer, company) == money_before)

print('3. Store part-accepts: 5 presented, 4 accepted, 1 rejected')
wizard = env['customer.goods.receipt.receive'].create({'pickup_id': pickup.id})
line = wizard.line_ids[0]
line.write({'qty_received': 5, 'qty_accepted': 4,
            'rejection_reason': 'One unit is cracked'})
wizard.action_confirm_receiving()
receipt = pickup.receipt_ids.filtered(lambda r: r.state == 'draft')[-1]
receipt.action_validate_receipt()
check('receipt validated', receipt.state == 'done', receipt.name)
check('only the accepted quantity entered stock',
      on_hand(product, warehouse.lot_stock_id) == stock_before + 4,
      'on hand %s' % on_hand(product, warehouse.lot_stock_id))
check('stock transfer is a real Odoo receipt',
      receipt.picking_id.state == 'done'
      and receipt.picking_id.location_dest_id == warehouse.lot_stock_id)
check('rejected quantity has a reason',
      receipt.line_ids.qty_rejected == 1
      and bool(receipt.line_ids.rejection_reason))
check('rejected quantity is not in stock',
      receipt.total_accepted_qty == 4 and receipt.total_rejected_qty == 1)
check('pickup status', pickup.state == 'partially_rejected', pickup.state)

print('4. Accountant approves the credit and raises the credit note')
check('credit awaits approval',
      receipt.credit_state == 'awaiting_approval', receipt.credit_state)
receipt.action_approve_credit()
receipt.action_create_credit_note()
credit = receipt.move_id
check('credit note is a customer credit note',
      credit.move_type == 'out_refund' and credit.state == 'draft')
check('credit note references the invoice',
      credit.reversed_entry_id == invoice
      and credit.partner_id == customer
      and credit.company_id == company)
check('credit note covers only the accepted quantity',
      sum(credit.invoice_line_ids.mapped('quantity')) == 4)
check('draft note changed no money',
      receivable(customer, company) == money_before)
check('draft note is not counted as credited',
      receipt.total_credited_qty == 0.0)

print('5. Accountant posts the credit note')
money_before_post = receivable(customer, company)
credit.action_post()
check('credit note posted', credit.state == 'posted')
check('receivable fell by the credited amount',
      abs((receiptable := money_before_post - receivable(customer, company))
          - abs(credit.amount_total)) < 0.01,
      '%.2f' % receiptable)
check('receipt reports the credit', receipt.credit_state == 'posted')
check('pickup reports the credit',
      pickup.total_credited_qty == 4.0, pickup.total_credited_qty)

print('6. A second pickup cannot take more than what is left')
second = env['customer.goods.pickup'].create({
    'partner_id': customer.id,
    'invoice_id': invoice.id,
    'sales_rep_id': manager.id,
    'warehouse_id': warehouse.id,
    'dest_location_id': warehouse.lot_stock_id.id,
})
second.write({'line_ids': [Command.create({
    'invoice_line_id': invoice.invoice_line_ids[0].id,
    'product_id': product.id,
    'product_uom_id': invoice.invoice_line_ids[0].product_uom_id.id,
    'qty_collected': 2,
})]})
second.action_confirm_collection()
check('second pickup within the remaining quantity',
      second.state == 'collected')
third = env['customer.goods.pickup'].create({
    'partner_id': customer.id,
    'invoice_id': invoice.id,
    'sales_rep_id': manager.id,
    'warehouse_id': warehouse.id,
    'dest_location_id': warehouse.lot_stock_id.id,
})
third.write({'line_ids': [Command.create({
    'invoice_line_id': invoice.invoice_line_ids[0].id,
    'product_id': product.id,
    'product_uom_id': invoice.invoice_line_ids[0].product_uom_id.id,
    'qty_collected': 3,
})]})
try:
    third.action_confirm_collection()
    check('over-collection refused', False)
except Exception:
    check('over-collection refused', True)

print('7. Full rejection of a third pickup leaves stock alone')
third.write({'line_ids': [Command.update(
    third.line_ids[0].id, {'qty_collected': 1})]})
stock_before_reject = on_hand(product, warehouse.lot_stock_id)
third.action_confirm_collection()
reject_wizard = env['customer.goods.receipt.receive'].create(
    {'pickup_id': third.id})
reject_wizard.action_confirm_receiving()
reject_receipt = third.receipt_ids.filtered(
    lambda r: r.state == 'draft')[-1]
env['customer.goods.receipt.reject'].create({
    'receipt_id': reject_receipt.id,
    'rejection_reason': 'Goods are in good condition and do not qualify for '
                        'return.',
    'rejected_disposition': 'return_to_customer',
    'rejected_disposition_notes': 'Rep collects them on Friday.',
}).action_reject()
reject_receipt.action_validate_receipt()
check('rejection added no stock',
      on_hand(product, warehouse.lot_stock_id) == stock_before_reject)
check('rejected goods have a physical home',
      bool(reject_receipt.rejected_location_id))
check('rejected pickup cannot be credited',
      reject_receipt.credit_state == 'not_eligible'
      and reject_receipt.total_credited_qty == 0.0)
check('pickup marked rejected', third.state == 'rejected', third.state)

env.cr.rollback()

print('\n' + '=' * 70)
for line in ok:
    print('  PASS  ' + line)
for line in fail:
    print('  FAIL  ' + line)
print('%d passed, %d failed  (rolled back - no data left)' % (len(ok), len(fail)))
