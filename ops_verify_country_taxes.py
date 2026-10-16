"""Prove a quotation and an invoice now compute South African tax.

The point of the repair was that Odoo refused these moves, so the check that
matters is not "the taxes have the right country" but "a real document computes
and posts". This raises a quotation and an invoice from scratch, rolls back,
and reports the taxes, the totals and the tax amount that came out.

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_verify_country_taxes.py
"""
company = env.company
country = company.account_fiscal_country_id
print(f"company {company.name!r} fiscal country {country.name}, "
      f"currency {company.currency_id.name}")

tax = env['account.tax'].search([
    ('type_tax_use', '=', 'sale'), ('company_id', '=', company.id),
    ('active', '=', True), ('amount', '>', 0)], limit=1)
product = env['product.product'].search([
    ('sale_ok', '=', True),
    ('company_id', 'in', (False, company.id)),
    ('taxes_id', '!=', False),
], limit=1)
if not product:
    raise SystemExit('no saleable product carries a tax; nothing to prove')
partner = env['res.partner'].create({'name': 'Tax Country Check'})
print(f"using tax {tax.name!r} ({tax.amount}%) on {product.display_name}")

# --- a quotation -----------------------------------------------------------
quote = env['sale.order'].create({
    'partner_id': partner.id,
    'order_line': [(0, 0, {
        'product_id': product.id,
        'product_uom_qty': 3,
        'price_unit': 1000.0,
    })],
})
print(f"\nquotation {quote.name}")
print(f"  currency        {quote.currency_id.name}")
print(f"  taxes on lines  {quote.order_line.mapped('tax_ids').mapped('name')}")
print(f"  country of tax  "
      f"{quote.order_line.tax_ids.mapped('country_id').mapped('name')}")
print(f"  untaxed         {quote.amount_untaxed:.2f}")
print(f"  tax             {quote.amount_tax:.2f}")
print(f"  total           {quote.amount_total:.2f} "
      f"{company.currency_id.symbol}{quote.amount_total:,.2f}")
assert quote.order_line.tax_ids, 'the quotation charged no tax at all'
assert all(t.country_id == country for t in quote.order_line.tax_ids), \
    'a tax on the quotation belongs to another country'
assert quote.amount_tax > 0, 'the quotation computed no tax'

# --- an invoice, posted ----------------------------------------------------
invoice = env['account.move'].create({
    'move_type': 'out_invoice',
    'partner_id': partner.id,
    'invoice_line_ids': [(0, 0, {
        'product_id': product.id,
        'quantity': 2,
        'price_unit': 500.0,
        'tax_ids': [(6, 0, tax.ids)],
    })],
})
invoice.action_post()
print(f"\ninvoice {invoice.name} posted={invoice.state}")
print(f"  currency        {invoice.currency_id.name}")
tax_lines = invoice.line_ids.filtered('tax_line_id')
print(f"  tax lines       {[line.tax_repartition_line_id.tax_id.name for line in tax_lines]}")
print(f"  tax amount      {sum(tax_lines.mapped('balance')):.2f}")
print(f"  total           {invoice.amount_total:.2f} "
      f"{invoice.currency_id.symbol}{invoice.amount_total:,.2f}")
assert invoice.state == 'posted', 'the invoice would not post'
assert invoice.amount_total > invoice.amount_untaxed, 'no tax was charged'

env.cr.rollback()  # this check creates nothing
print("\nrolled back; nothing was written")
