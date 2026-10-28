# -*- coding: utf-8 -*-
"""Weighted costing and VAT treatment on the product profile.

Costing
-------
A product is rarely bought at one price.  A buyer might pick up 100 units at
R1 000 on one invoice and a single unit at R1 on another, and the cost has to
reflect what the business actually paid across the whole volume.  Averaging
the *price points* would be wrong: (1 000 + 1) / 2 = R500.50 ignores that a
hundred units were bought at the higher price.  What matters is the average
of what was *spent*, over what was *bought*::

    (100 x 1 000) + (1 x 1)      100 001
    -------------------------  = -------  = R990.11 per unit
              101                   101

So every price point carries the quantity bought at it, and the weighted
average -- total spent divided by total units -- becomes the cost the pricing
tab works from.  A price point with no quantity is treated as one unit, so a
single deliberate purchase still stands on its own.

When price points exist they drive **Gross Cost**; with none, the buyer types
it by hand exactly as before.  Transport Cost still sits on top, because a
landed price is transport the supplier did not charge.

VAT treatment
-------------
South Africa's VAT201 return separates supplies into boxes, and two supplies
that both charge nothing can still land in different boxes.  The taxes already
in this database carry those boxes as tags::

    15%         [1]  Standard Rate              rated
    0%          [2]  Zero Rate                  zero rated
    0% EX       [2A] Zero Rate (goods exported) zero rated, exported
    0% EXEMPT   [3]  Exempt and Non supplies    exempt

Marking the treatment picks the matching tax, so the clerk raising an order
never has to know which of the nine taxes applies -- and order lines, which
already follow the product's own taxes, charge the right amount on their own.
"""
from odoo import Command, _, api, fields, models
from odoo.tools import float_compare

from .product_pricing import PRICING_FIELDS, SOLVE_CONTEXT_KEY

COST_SYNC_CONTEXT_KEY = 'vuma_cost_syncing'


class VumaCostPricePoint(models.Model):
    """One price a product was bought at, and how much of it was bought."""

    _name = 'vuma.cost.price.point'
    _description = 'Cost Price Point'
    _order = 'sequence, id'
    _rec_name = 'display_name'

    product_tmpl_id = fields.Many2one(
        'product.template', string='Product', required=True, index=True,
        ondelete='cascade')
    sequence = fields.Integer(default=10)
    date = fields.Date(
        string='Date', help='When this price was paid. Purely for the '
             'buyer\'s reference when reviewing the cost later.')
    supplier_id = fields.Many2one(
        'res.partner', string='Supplier',
        domain="[('supplier_rank', '>', 0)]",
        help='Who supplied the goods at this price.')
    quantity = fields.Float(
        string='Quantity', digits='Product Unit of Measure', default=1.0,
        help='How many units were bought at this price. The cost is weighted '
             'by this number, so 100 units at R10 count ten times as much as '
             '10 units at R10. Treated as one unit when left at zero.')
    unit_price = fields.Float(
        string='Unit Price Excl VAT', digits='Product Price', required=True,
        default=0.0,
        help='What one unit cost, before VAT. Enter the price as the supplier '
             'charged it; the weighted average below becomes the cost.')
    subtotal = fields.Monetary(
        string='Total Spent', compute='_compute_subtotal', store=True,
        currency_field='currency_id',
        help='Quantity x Unit Price: what was spent at this price point.')
    display_name = fields.Char(
        string='Description', compute='_compute_display_name')
    currency_id = fields.Many2one(
        related='product_tmpl_id.currency_id', readonly=True)
    company_id = fields.Many2one(
        related='product_tmpl_id.company_id', readonly=True)

    @api.depends('quantity', 'unit_price')
    def _compute_subtotal(self):
        for point in self:
            quantity = point.quantity or 0.0
            if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                quantity = 1.0
            point.subtotal = quantity * (point.unit_price or 0.0)

    @api.depends('quantity', 'unit_price', 'date', 'supplier_id')
    def _compute_display_name(self):
        for point in self:
            quantity = point.quantity or 0.0
            if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                quantity = 1.0
            bits = ['%s x %s' % (self.env['product.template']
                                 ._vuma_format_quantity(quantity),
                                 point.unit_price)]
            if point.supplier_id:
                bits.append(point.supplier_id.name)
            if point.date:
                bits.append(fields.Date.to_string(point.date))
            point.display_name = ', '.join(bits)

    @api.onchange('quantity', 'unit_price')
    def _onchange_amount(self):
        """Zero quantity means one unit, so a lone purchase still counts."""
        for point in self:
            if float_compare(point.quantity or 0.0, 0.0, precision_digits=6) <= 0:
                point.quantity = 1.0

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        if not self.env.context.get(COST_SYNC_CONTEXT_KEY):
            records.mapped('product_tmpl_id')._vuma_sync_gross_cost()
        return records

    def write(self, vals):
        res = super().write(vals)
        if not self.env.context.get(COST_SYNC_CONTEXT_KEY):
            self.mapped('product_tmpl_id')._vuma_sync_gross_cost()
        return res

    def unlink(self):
        templates = self.mapped('product_tmpl_id')
        res = super().unlink()
        if not self.env.context.get(COST_SYNC_CONTEXT_KEY):
            templates._vuma_sync_gross_cost()
        return res


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    # ------------------------------------------------------------- costing

    vuma_cost_line_ids = fields.One2many(
        'vuma.cost.price.point', 'product_tmpl_id', string='Cost Price Points',
        copy=True)
    vuma_cost_line_count = fields.Integer(
        string='Price Points', compute='_compute_cost_summary')
    vuma_cost_total_qty = fields.Float(
        string='Total Units', digits='Product Unit of Measure',
        compute='_compute_cost_summary', store=True,
        help='Every unit across all price points: the denominator of the '
             'weighted average.')
    vuma_cost_total_value = fields.Monetary(
        string='Total Spent', compute='_compute_cost_summary', store=True,
        currency_field='currency_id',
        help='Everything paid for those units: the numerator of the weighted '
             'average.')
    vuma_weighted_cost = fields.Monetary(
        string='Weighted Average Cost', compute='_compute_cost_summary',
        store=True, currency_field='currency_id',
        help='Total Spent divided by Total Units. This is the cost price '
             'excl VAT -- the number the pricing tab works from. It weights '
             'each price by how much was bought at it, so a bulk buy moves '
             'the cost far more than a single stray unit.')
    vuma_cost_low = fields.Monetary(
        string='Lowest Price', compute='_compute_cost_summary',
        currency_field='currency_id',
        help='Cheapest price point, to show the spread the average hides.')
    vuma_cost_high = fields.Monetary(
        string='Highest Price', compute='_compute_cost_summary',
        currency_field='currency_id',
        help='Dearest price point, to show the spread the average hides.')
    vuma_gross_cost_driven = fields.Boolean(
        string='Cost Driven By Price Points', compute='_compute_cost_summary',
        help='True when the price points are supplying the gross cost, so the '
             'field can be shown read-only.')

    VAT_TREATMENTS = [
        ('standard', 'Standard Rated (15%)'),
        ('zero_rated', 'Zero Rated (0%)'),
        ('zero_rated_export', 'Zero Rated Export (0%)'),
        ('exempt', 'VAT Exempt'),
    ]

    vuma_vat_treatment = fields.Selection(
        VAT_TREATMENTS, string='VAT Treatment', default='standard',
        help='How this item is treated for VAT. Picking one applies the '
             'matching sales tax, and every order line raised on this product '
             'then charges the right VAT without the clerk choosing it. '
             'Zero Rated and VAT Exempt both charge nothing, but they report '
             'in different boxes on the VAT return.')
    vuma_vat_box = fields.Char(
        string='VAT201 Box', compute='_compute_vuma_vat_box',
        help='The VAT201 return box this treatment reports in.')

    # --------------------------------------------------------------- maths

    @api.model
    def _vuma_format_quantity(self, quantity):
        """A tidy quantity for a description: ``5`` not ``5.000000``."""
        text = f'{quantity:.6f}'.rstrip('0').rstrip('.')
        return text or '0'

    @api.depends('vuma_cost_line_ids.subtotal', 'vuma_cost_line_ids.quantity',
                 'vuma_cost_line_ids.unit_price')
    def _compute_cost_summary(self):
        for template in self:
            points = template.vuma_cost_line_ids
            total_qty = 0.0
            total_value = 0.0
            for point in points:
                # A price point left at zero quantity is one deliberate unit,
                # not a line to be ignored.
                quantity = point.quantity or 0.0
                if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                    quantity = 1.0
                total_qty += quantity
                total_value += quantity * (point.unit_price or 0.0)

            currency = template.currency_id or template.env.company.currency_id
            raw_weighted = (total_value / total_qty) if total_qty else 0.0
            weighted = (
                currency.round(raw_weighted) if currency else raw_weighted)

            template.vuma_cost_line_count = len(points)
            template.vuma_cost_total_qty = total_qty
            template.vuma_cost_total_value = total_value
            template.vuma_weighted_cost = weighted
            prices = [point.unit_price for point in points]
            template.vuma_cost_low = min(prices) if prices else 0.0
            template.vuma_cost_high = max(prices) if prices else 0.0
            template.vuma_gross_cost_driven = bool(points)

    @api.depends('vuma_gross_cost', 'vuma_transport_cost',
                 'vuma_cost_line_ids.subtotal', 'vuma_cost_line_ids.quantity',
                 'vuma_cost_line_ids.unit_price')
    def _compute_vuma_nett_cost(self):
        return super()._compute_vuma_nett_cost()

    def _vuma_nett_cost_value(self):
        """Gross cost (from price points when present) plus transport cost."""
        self.ensure_one()
        if self.vuma_cost_line_ids:
            self._compute_cost_summary()
            return (self.vuma_weighted_cost or 0.0) + (
                self.vuma_transport_cost or 0.0)
        return super()._vuma_nett_cost_value()

    @api.depends('vuma_vat_treatment', 'taxes_id', 'taxes_id.amount')
    def _compute_vuma_vat_box(self):
        for template in self:
            tax = template._vuma_tax_for_treatment(
                template.vuma_vat_treatment)
            template.vuma_vat_box = tax._vuma_sars_box() if tax else ''

    # -------------------------------------------------- VAT treatment -> tax

    def _vuma_tax_for_treatment(self, treatment=None):
        """The sale tax matching this VAT treatment, for this company.

        Matched on the SARS box tag the tax carries, because that is what
        actually separates a zero rate from an exempt supply -- both charge
        nothing, and only the tag says where they report.  Falls back to the
        tax's name when no tags are configured.
        """
        self.ensure_one()
        treatment = treatment or self.vuma_vat_treatment or 'standard'
        company = self.company_id or self.env.company
        taxes = self.env['account.tax'].sudo().with_company(company).search([
            ('type_tax_use', '=', 'sale'),
            ('company_id', '=', company.id),
        ])
        box = {
            'standard': '[1]',
            'zero_rated': '[2]',
            'zero_rated_export': '[2A]',
            'exempt': '[3]',
        }[treatment]

        tagged = taxes.filtered(
            lambda tax: box in tax._vuma_sars_boxes())
        if tagged:
            return tagged.sorted(lambda tax: (len(tax.name), tax.id))[0]

        # No tags to go on: fall back on the amount and name, keeping the
        # plain tax of each family ahead of its variants.
        if treatment == 'standard':
            match = taxes.filtered(lambda tax: tax.amount > 0)
        else:
            match = taxes.filtered(lambda tax: not tax.amount)
            if treatment == 'exempt':
                exempt = match.filtered(
                    lambda tax: 'exempt' in tax.name.lower())
                match = exempt or match
            elif treatment == 'zero_rated_export':
                export = match.filtered(
                    lambda tax: 'ex' in tax.name.lower()
                    and 'exempt' not in tax.name.lower())
                match = export or match
            else:
                domestic = match.filtered(
                    lambda tax: 'ex' not in tax.name.lower())
                match = domestic or match
        return match.sorted(lambda tax: (len(tax.name), tax.id))[0] \
            if match else taxes.browse()

    def _vuma_treatment_from_taxes(self):
        """Infer the VAT treatment from the product's current sales taxes."""
        self.ensure_one()
        for tax in self.taxes_id:
            box = tax._vuma_sars_box()
            if box == '[3]':
                return 'exempt'
            if box == '[2A]':
                return 'zero_rated_export'
            if box == '[2]':
                return 'zero_rated'
            if box == '[1]':
                return 'standard'
        for tax in self.taxes_id.filtered(lambda t: t.amount_type == 'percent'):
            name = (tax.name or '').lower()
            if tax.amount > 0:
                return 'standard'
            if 'exempt' in name:
                return 'exempt'
            if 'ex' in name:
                return 'zero_rated_export'
            return 'zero_rated'
        return False

    def _vuma_apply_vat_treatment(self, treatment=None):
        """Put the matching tax on the product, keeping the rest intact.

        Only the current company's tax is replaced; a shared product carrying
        taxes from another company keeps them, whereas a company-scoped
        product carries only its own company's tax.
        """
        for template in self:
            chosen = treatment or template.vuma_vat_treatment or 'standard'
            tax = template._vuma_tax_for_treatment(chosen)
            if not tax:
                continue
            if template.company_id:
                new_taxes = tax
            else:
                family = template.taxes_id.filtered(
                    lambda t: t.company_id == tax.company_id)
                new_taxes = template.taxes_id - family + tax
            if self.env.context.get(COST_SYNC_CONTEXT_KEY):
                template.with_context(**{
                    SOLVE_CONTEXT_KEY: True,
                    COST_SYNC_CONTEXT_KEY: True,
                }).write({'taxes_id': [Command.set(new_taxes.ids)]})
            else:
                template.taxes_id = [Command.set(new_taxes.ids)]
        return True

    @api.onchange('vuma_vat_treatment')
    def _onchange_vuma_vat_treatment(self):
        self._vuma_apply_vat_treatment()
        self._compute_vuma_vat_box()
        self._compute_vuma_vat_percent()
        self._vuma_solve_onchange()

    @api.onchange('taxes_id')
    def _onchange_vuma_pricing_taxes(self):
        for template in self:
            inferred = template._vuma_treatment_from_taxes()
            if inferred and template.vuma_vat_treatment != inferred:
                template.vuma_vat_treatment = inferred
        self._compute_vuma_vat_box()
        return super()._onchange_vuma_pricing_taxes()

    # ------------------------------------------------------------- costing

    def _vuma_sync_gross_cost(self, provided=()):
        """Push the weighted average into Gross Cost, then re-solve prices.

        Called whenever the price points change.  The ordinary pricing solver
        already reacts to a moving gross cost, so markup and selling price
        follow without this method knowing anything about them.
        """
        provided = set(provided)
        self._compute_cost_summary()
        for template in self:
            if not template.vuma_gross_cost_driven:
                continue
            target = template.vuma_weighted_cost
            if float_compare(
                    template.vuma_gross_cost, target, precision_digits=6):
                template.with_context(**{
                    SOLVE_CONTEXT_KEY: True,
                    COST_SYNC_CONTEXT_KEY: True,
                }).write({'vuma_gross_cost': target})
            template._compute_vuma_nett_cost()
            template._vuma_solve_pricing(provided)
        return True

    @api.model_create_multi
    def create(self, vals_list):
        ctx_self = self.with_context(**{COST_SYNC_CONTEXT_KEY: True})
        templates = super(ProductTemplate, ctx_self).create(
            vals_list).with_env(self.env)
        for template, vals in zip(templates, vals_list):
            vat_applied = False
            if 'vuma_vat_treatment' in vals and 'taxes_id' not in vals:
                template.with_context(**{
                    COST_SYNC_CONTEXT_KEY: True,
                })._vuma_apply_vat_treatment(vals['vuma_vat_treatment'])
                vat_applied = True
            elif 'taxes_id' in vals and 'vuma_vat_treatment' not in vals:
                inferred = template._vuma_treatment_from_taxes()
                if inferred and template.vuma_vat_treatment != inferred:
                    template.with_context(**{
                        SOLVE_CONTEXT_KEY: True,
                        COST_SYNC_CONTEXT_KEY: True,
                    }).write({'vuma_vat_treatment': inferred})
            if 'vuma_cost_line_ids' in vals:
                provided = set(vals)
                if 'list_price' in vals and not (provided & set(PRICING_FIELDS)):
                    template._vuma_pricing_from_sales_price()
                    provided.add(template.vuma_pricing_driver)
                template._vuma_sync_gross_cost(provided=provided)
            elif vat_applied:
                template._compute_vuma_vat_percent()
                if 'list_price' in vals and not (set(vals) & set(PRICING_FIELDS)):
                    template._vuma_pricing_from_sales_price()
                else:
                    template._vuma_solve_pricing(vals)
        return templates

    def write(self, vals):
        if self.env.context.get(SOLVE_CONTEXT_KEY) or self.env.context.get(
                COST_SYNC_CONTEXT_KEY):
            return super().write(vals)

        ctx_self = self.with_context(**{COST_SYNC_CONTEXT_KEY: True})
        res = super(ProductTemplate, ctx_self).write(vals)

        vat_applied = False
        if 'vuma_vat_treatment' in vals and 'taxes_id' not in vals:
            ctx_self._vuma_apply_vat_treatment(vals['vuma_vat_treatment'])
            vat_applied = True
        elif 'taxes_id' in vals and 'vuma_vat_treatment' not in vals:
            for template in self:
                inferred = template._vuma_treatment_from_taxes()
                if inferred and template.vuma_vat_treatment != inferred:
                    template.with_context(**{
                        SOLVE_CONTEXT_KEY: True,
                        COST_SYNC_CONTEXT_KEY: True,
                    }).write({'vuma_vat_treatment': inferred})

        if 'vuma_cost_line_ids' in vals:
            provided = set(vals)
            if 'list_price' in vals and not (provided & set(PRICING_FIELDS)):
                self._vuma_pricing_from_sales_price()
                for template in self:
                    template._vuma_sync_gross_cost(
                        provided=provided | {template.vuma_pricing_driver})
            else:
                self._vuma_sync_gross_cost(provided=provided)
        elif vat_applied:
            self._compute_vuma_vat_percent()
            if 'list_price' in vals and not (set(vals) & set(PRICING_FIELDS)):
                self._vuma_pricing_from_sales_price()
            else:
                self._vuma_solve_pricing(vals)
        return res

    @api.onchange('vuma_cost_line_ids')
    def _onchange_vuma_cost_lines(self):
        """Mirror the saved behaviour while the form is still open."""
        self._compute_cost_summary()
        for template in self:
            if not template.vuma_gross_cost_driven:
                continue
            template.vuma_gross_cost = template.vuma_weighted_cost
            template._compute_vuma_nett_cost()
            template._vuma_solve_onchange()


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    def _compute_tax_ids(self):
        super()._compute_tax_ids()
        for line in self:
            product = line.product_id
            if (
                not product
                or line.product_type == 'combo'
                or line.tax_ids
                or not product.taxes_id
                or not product.vuma_vat_treatment
            ):
                continue
            # Shared product whose tax was saved under another company: pick
            # this order's company tax for the product's VAT treatment.
            company = line.company_id or self.env.company
            tax = product.product_tmpl_id.with_company(
                company)._vuma_tax_for_treatment(product.vuma_vat_treatment)
            if tax:
                fpos = line.order_id.fiscal_position_id
                line.tax_ids = fpos.map_tax(tax) if fpos else tax


class AccountMoveLine(models.Model):
    _inherit = 'account.move.line'

    def _get_computed_taxes(self):
        tax_ids = super()._get_computed_taxes()
        if (
            not tax_ids
            and self.move_id.is_sale_document(include_receipts=True)
            and self.product_id
            and self.product_id.sudo().taxes_id
            and self.product_id.vuma_vat_treatment
        ):
            company = self.move_id.company_id or self.env.company
            tax = self.product_id.product_tmpl_id.with_company(
                company)._vuma_tax_for_treatment(
                    self.product_id.vuma_vat_treatment)
            if tax:
                fpos = self.move_id.fiscal_position_id
                return fpos.map_tax(tax) if fpos else tax
        return tax_ids


class AccountTax(models.Model):
    _inherit = 'account.tax'

    def _vuma_sars_boxes(self):
        """Every VAT201 box tag this tax carries, e.g. ``['[1]', '[4]']``."""
        self.ensure_one()
        boxes = []
        for line in self.invoice_repartition_line_ids:
            for tag in line.tag_ids:
                name = tag.name
                if isinstance(name, dict):
                    name = name.get('en_US') or next(iter(name.values()), '')
                if name and name.startswith('['):
                    boxes.append(name.split(']', 1)[0] + ']')
        return boxes

    def _vuma_sars_box(self):
        """The headline VAT201 box this tax reports in."""
        self.ensure_one()
        boxes = self._vuma_sars_boxes()
        for box in ('[1]', '[2]', '[2A]', '[3]'):
            if box in boxes:
                return box
        return boxes[0] if boxes else ''
