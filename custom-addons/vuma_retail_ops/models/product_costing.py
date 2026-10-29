# -*- coding: utf-8 -*-
"""Configurable product costing methods, landed cost capture and VAT treatment.

Costing methods
---------------
Goods can be costed from their purchase price points using any of the methods
configured in **Settings** (or overridden on an individual product):

* **Weighted Average (by Volume / Quantity)**: ``Total Spent / Total Units``
  -- e.g. 100 units at R1 000 + 1 unit at R1 = R100 001 / 101 = R990.11.
* **Average Price (Simple Arithmetic Average)**: unweighted mean of the unit
  prices across batches.
* **Log Average (Logarithmic / Geometric Mean)**: ``exp(mean(ln(P_i)))``,
  dampening extreme single-invoice spikes.
* **Volume-Weighted Log Average**: ``exp(sum(Q_i * ln(P_i)) / sum(Q_i))``.
* **FIFO (First In, First Out)**: unit price of the oldest batch.
* **LIFO (Last In, First Out)**: unit price of the most recent batch.
* **Moving Average (Last 3 Batches Weighted)**: volume-weighted average of the
  three most recent batches.
* **Median Cost**: middle batch price when sorted from lowest to highest.
* **Harmonic Average**: ``n / sum(1 / P_i)`` (equal-spend rate average).
* **Highest / Replacement Cost**: highest batch price (conservative margin
  protection).
* **Lowest Cost**: lowest batch price.
* **Manual / Standard Fixed Cost**: Gross Cost is typed by hand and price
  points are kept for reference only.

Landed cost components
----------------------
Every landed cost component (Transport, Import Duty % / Amount, International
Shipping, Customs Clearing & Forwarding, Transit / Marine Insurance, Packaging
& Labelling, Handling & Cold Storage, Excise & Statutory Levies, FX & Bank
Charges, and Other Landed Costs) is available to capture on the Pricing tab,
and Settings controls which components are folded into Nett Cost.

VAT treatment
-------------
South Africa's VAT201 return separates supplies into boxes::

    15%         [1]  Standard Rate              rated
    0%          [2]  Zero Rate                  zero rated
    0% EX       [2A] Zero Rate (goods exported) zero rated, exported
    0% EXEMPT   [3]  Exempt and Non supplies    exempt
"""
import math

from odoo import Command, _, api, fields, models
from odoo.tools import float_compare

from .product_pricing import (
    ADDITIONAL_COST_FIELDS,
    LANDED_COST_COMPONENTS,
    PRICING_FIELDS,
    SOLVE_CONTEXT_KEY,
)

COST_SYNC_CONTEXT_KEY = 'vuma_cost_syncing'

COSTING_METHODS = [
    ('weighted_average', 'Weighted Average (by Volume / Quantity)'),
    ('simple_average', 'Average Price (Simple Arithmetic Average)'),
    ('log_average', 'Log Average (Logarithmic / Geometric Mean)'),
    ('weighted_log_average', 'Volume-Weighted Log Average'),
    ('fifo', 'FIFO (First In, First Out — Oldest Batch)'),
    ('lifo', 'LIFO (Last In, First Out — Latest Batch)'),
    ('moving_average', 'Moving Average (Last 3 Batches Weighted)'),
    ('median_cost', 'Median Cost (Middle Batch Price)'),
    ('harmonic_average', 'Harmonic Average (Equal-Spend Rate)'),
    ('highest_cost', 'Highest / Replacement Cost (Conservative Max)'),
    ('lowest_cost', 'Lowest Cost (Minimum Batch Price)'),
    ('manual', 'Manual / Standard Fixed Cost'),
]

COSTING_METHOD_LABELS = dict(COSTING_METHODS)

COMPANY_COST_SETTING_FIELDS = (
    'vuma_costing_method',
) + tuple(flag for _, flag in LANDED_COST_COMPONENTS)


class ResCompany(models.Model):
    _inherit = 'res.company'

    vuma_costing_method = fields.Selection(
        COSTING_METHODS,
        string='Default Goods Costing Method',
        default='weighted_average',
        required=True,
        help='How Gross Cost is worked out from a product\'s Cost Price '
             'Points across all goods using the company default.')
    vuma_cost_include_transport = fields.Boolean(
        string='Include Transport & Local Freight Cost in Cost',
        default=True,
        help='When ticked, Transport Cost captured on a product is added '
             'into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_import_duty = fields.Boolean(
        string='Include Import Duty / Customs Tariff in Cost',
        default=True,
        help='When ticked, Import Duty Cost captured on a product is added '
             'into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_shipping = fields.Boolean(
        string='Include International Shipping (Sea / Air Freight) in Cost',
        default=True,
        help='When ticked, International Shipping Cost captured on a product '
             'is added into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_clearance = fields.Boolean(
        string='Include Customs Clearing & Forwarding in Cost',
        default=True,
        help='When ticked, Clearing & Forwarding Cost captured on a product '
             'is added into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_insurance = fields.Boolean(
        string='Include Transit & Marine Insurance in Cost',
        default=True,
        help='When ticked, Transit & Marine Insurance Cost captured on a '
             'product is added into Nett Cost and written to the product\'s '
             'Cost.')
    vuma_cost_include_packaging = fields.Boolean(
        string='Include Packaging, Labelling & Repackaging in Cost',
        default=True,
        help='When ticked, Packaging & Labelling Cost captured on a product '
             'is added into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_handling = fields.Boolean(
        string='Include Handling, Warehousing & Cold Storage in Cost',
        default=True,
        help='When ticked, Handling & Storage Cost captured on a product is '
             'added into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_levy = fields.Boolean(
        string='Include Excise, Environmental & Statutory Levies in Cost',
        default=True,
        help='When ticked, Excise & Statutory Levies captured on a product '
             'are added into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_fx_bank = fields.Boolean(
        string='Include Foreign Exchange & Bank / LC Charges in Cost',
        default=True,
        help='When ticked, FX & Bank Charges captured on a product are added '
             'into Nett Cost and written to the product\'s Cost.')
    vuma_cost_include_other = fields.Boolean(
        string='Include Other Landed / Sundry Costs in Cost',
        default=True,
        help='When ticked, Other Landed Cost captured on a product is added '
             'into Nett Cost and written to the product\'s Cost.')

    def write(self, vals):
        res = super().write(vals)
        if any(field in vals for field in COMPANY_COST_SETTING_FIELDS):
            self._vuma_recompute_company_product_costs()
        return res

    def _vuma_recompute_company_product_costs(self, reset_overrides=False):
        """Re-evaluate product costs and pricing when company settings change."""
        ProductTmpl = self.env['product.template'].sudo()
        total_updated = 0
        for company in self:
            templates = ProductTmpl.with_company(company).search([
                ('company_id', 'in', [False, company.id]),
            ])
            if reset_overrides:
                overridden = templates.filtered(
                    lambda t: t.vuma_costing_method != 'company')
                if overridden:
                    overridden.with_context(**{
                        SOLVE_CONTEXT_KEY: True,
                        COST_SYNC_CONTEXT_KEY: True,
                    }).write({'vuma_costing_method': 'company'})
            templates._compute_cost_summary()
            for template in templates:
                has_points = bool(template.vuma_cost_line_ids)
                has_costs = has_points or bool(template.vuma_gross_cost) or any(
                    bool(template[f]) for f in ADDITIONAL_COST_FIELDS
                )
                if not has_costs:
                    continue
                if has_points and template.vuma_gross_cost_driven:
                    template._vuma_sync_gross_cost()
                else:
                    template._vuma_sync_import_duty_from_percent()
                    template._compute_vuma_nett_cost()
                    template._vuma_solve_pricing()
                total_updated += 1
        return total_updated


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    vuma_costing_method = fields.Selection(
        related='company_id.vuma_costing_method',
        readonly=False,
        string='Default Goods Costing Method')
    vuma_cost_include_transport = fields.Boolean(
        related='company_id.vuma_cost_include_transport',
        readonly=False)
    vuma_cost_include_import_duty = fields.Boolean(
        related='company_id.vuma_cost_include_import_duty',
        readonly=False)
    vuma_cost_include_shipping = fields.Boolean(
        related='company_id.vuma_cost_include_shipping',
        readonly=False)
    vuma_cost_include_clearance = fields.Boolean(
        related='company_id.vuma_cost_include_clearance',
        readonly=False)
    vuma_cost_include_insurance = fields.Boolean(
        related='company_id.vuma_cost_include_insurance',
        readonly=False)
    vuma_cost_include_packaging = fields.Boolean(
        related='company_id.vuma_cost_include_packaging',
        readonly=False)
    vuma_cost_include_handling = fields.Boolean(
        related='company_id.vuma_cost_include_handling',
        readonly=False)
    vuma_cost_include_levy = fields.Boolean(
        related='company_id.vuma_cost_include_levy',
        readonly=False)
    vuma_cost_include_fx_bank = fields.Boolean(
        related='company_id.vuma_cost_include_fx_bank',
        readonly=False)
    vuma_cost_include_other = fields.Boolean(
        related='company_id.vuma_cost_include_other',
        readonly=False)

    def action_vuma_recalculate_all_costs(self):
        """Apply the selected costing method & landed cost options to all goods."""
        self.ensure_one()
        company = self.company_id or self.env.company
        count = company._vuma_recompute_company_product_costs(
            reset_overrides=True)
        label = COSTING_METHOD_LABELS.get(
            company.vuma_costing_method, company.vuma_costing_method)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'title': _('Product Costs Recalculated'),
                'message': _(
                    '%(count)s product(s) recalculated using %(method)s.',
                    count=count, method=label),
                'type': 'success',
                'sticky': False,
            },
        }


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
        string='Date', help='When this price was paid. Used by FIFO, LIFO '
             'and Moving Average costing as well as buyer reference.')
    supplier_id = fields.Many2one(
        'res.partner', string='Supplier',
        domain="[('supplier_rank', '>', 0)]",
        help='Who supplied the goods at this price.')
    reference = fields.Char(
        string='Invoice / Shipment Ref',
        help='Optional supplier invoice, PO or container reference.')
    quantity = fields.Float(
        string='Quantity', digits='Product Unit of Measure', default=1.0,
        help='How many units were bought at this price. Volume-weighted '
             'methods weight the price by this number; treated as one unit '
             'when left at zero.')
    unit_price = fields.Float(
        string='Unit Price Excl VAT', digits='Product Price', required=True,
        default=0.0,
        help='What one unit cost from the supplier, before VAT.')
    transport_cost = fields.Float(
        string='Transport / Unit', digits='Product Price', default=0.0,
        help='Optional batch-specific transport cost per unit.')
    import_duty_cost = fields.Float(
        string='Import Duty / Unit', digits='Product Price', default=0.0,
        help='Optional batch-specific import duty cost per unit.')
    other_cost = fields.Float(
        string='Other Cost / Unit', digits='Product Price', default=0.0,
        help='Optional batch-specific clearing, shipping, insurance or '
             'other landed cost per unit.')
    effective_unit_price = fields.Float(
        string='Landed Batch Unit Cost', digits='Product Price',
        compute='_compute_subtotal', store=True,
        help='Unit Price + any batch-specific Transport, Import Duty and '
             'Other Cost per unit.')
    subtotal = fields.Monetary(
        string='Total Spent', compute='_compute_subtotal', store=True,
        currency_field='currency_id',
        help='Quantity x Landed Batch Unit Cost: what was spent on this batch.')
    display_name = fields.Char(
        string='Description', compute='_compute_display_name')
    currency_id = fields.Many2one(
        related='product_tmpl_id.currency_id', readonly=True)
    company_id = fields.Many2one(
        related='product_tmpl_id.company_id', readonly=True)

    @api.depends(
        'quantity', 'unit_price', 'transport_cost',
        'import_duty_cost', 'other_cost')
    def _compute_subtotal(self):
        for point in self:
            quantity = point.quantity or 0.0
            if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                quantity = 1.0
            effective = (
                (point.unit_price or 0.0)
                + (point.transport_cost or 0.0)
                + (point.import_duty_cost or 0.0)
                + (point.other_cost or 0.0)
            )
            point.effective_unit_price = effective
            point.subtotal = quantity * effective

    @api.depends('quantity', 'unit_price', 'date', 'supplier_id', 'reference')
    def _compute_display_name(self):
        for point in self:
            quantity = point.quantity or 0.0
            if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                quantity = 1.0
            bits = ['%s x %s' % (self.env['product.template']
                                 ._vuma_format_quantity(quantity),
                                 point.effective_unit_price or point.unit_price)]
            if point.supplier_id:
                bits.append(point.supplier_id.name)
            if point.reference:
                bits.append(point.reference)
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

    vuma_costing_method = fields.Selection(
        [('company', 'Use Company Setting (Default)')] + COSTING_METHODS,
        string='Costing Method',
        default='company',
        required=True,
        help='How Gross Cost Excl VAT is calculated from the Cost Price '
             'Points below. Leave on "Use Company Setting (Default)" to '
             'follow the method chosen in Settings, or pick a specific '
             'method for this item.')
    vuma_effective_costing_method_label = fields.Char(
        string='Active Costing Formula',
        compute='_compute_cost_summary',
        help='The costing formula currently driving this product\'s cost.')
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
    vuma_calculated_cost = fields.Monetary(
        string='Calculated Base Cost (Selected Method)',
        compute='_compute_cost_summary', store=True,
        currency_field='currency_id',
        help='The base cost per unit produced by the active Costing Method '
             '(Weighted Average, Simple Average, Log Average, FIFO, LIFO, '
             'etc.). Feeds Gross Cost Excl VAT.')
    vuma_weighted_cost = fields.Monetary(
        string='Weighted Average Cost', compute='_compute_cost_summary',
        store=True, currency_field='currency_id',
        help='Total Spent divided by Total Units (volume-weighted average).')
    vuma_simple_avg_cost = fields.Monetary(
        string='Average Price (Simple)', compute='_compute_cost_summary',
        store=True, currency_field='currency_id',
        help='Unweighted arithmetic mean of all batch unit prices.')
    vuma_log_avg_cost = fields.Monetary(
        string='Log Average Cost', compute='_compute_cost_summary',
        store=True, currency_field='currency_id',
        help='Logarithmic / geometric mean of the batch unit prices.')
    vuma_fifo_cost = fields.Monetary(
        string='FIFO Cost (Oldest Batch)', compute='_compute_cost_summary',
        store=True, currency_field='currency_id',
        help='Unit cost from the earliest / oldest batch.')
    vuma_lifo_cost = fields.Monetary(
        string='LIFO Cost (Latest Batch)', compute='_compute_cost_summary',
        store=True, currency_field='currency_id',
        help='Unit cost from the most recent / latest batch.')
    vuma_cost_low = fields.Monetary(
        string='Lowest Price', compute='_compute_cost_summary',
        currency_field='currency_id',
        help='Cheapest price point across all batches.')
    vuma_cost_high = fields.Monetary(
        string='Highest Price', compute='_compute_cost_summary',
        currency_field='currency_id',
        help='Dearest price point across all batches.')
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

    def _vuma_effective_costing_method(self):
        """Return the active costing method key for this product."""
        self.ensure_one()
        if self.vuma_costing_method and self.vuma_costing_method != 'company':
            return self.vuma_costing_method
        company = self.company_id or self.env.company
        return company.vuma_costing_method or 'weighted_average'

    @api.depends(
        'vuma_costing_method',
        'company_id.vuma_costing_method',
        'vuma_cost_line_ids.subtotal',
        'vuma_cost_line_ids.quantity',
        'vuma_cost_line_ids.unit_price',
        'vuma_cost_line_ids.transport_cost',
        'vuma_cost_line_ids.import_duty_cost',
        'vuma_cost_line_ids.other_cost',
        'vuma_cost_line_ids.date',
        'vuma_cost_line_ids.sequence',
    )
    def _compute_cost_summary(self):
        for template in self:
            points = template.vuma_cost_line_ids
            total_qty = 0.0
            total_value = 0.0
            pairs = []
            chronological_items = []
            for idx, point in enumerate(points):
                quantity = point.quantity or 0.0
                if float_compare(quantity, 0.0, precision_digits=6) <= 0:
                    quantity = 1.0
                price = (
                    (point.unit_price or 0.0)
                    + (point.transport_cost or 0.0)
                    + (point.import_duty_cost or 0.0)
                    + (point.other_cost or 0.0)
                )
                total_qty += quantity
                total_value += quantity * price
                pairs.append((quantity, price))
                point_id = point.id if isinstance(point.id, int) else idx
                chronological_items.append((
                    0 if point.date else 1,
                    point.date or fields.Date.today(),
                    point.sequence or 0,
                    point_id,
                    idx,
                    quantity,
                    price,
                ))

            currency = template.currency_id or template.env.company.currency_id
            round_curr = (
                currency.round if currency else (lambda val: val))

            prices = [p for _, p in pairs]
            pos_prices = [p for p in prices if p > 0.0]
            pos_pairs = [(q, p) for q, p in pairs if p > 0.0]

            # 1. Weighted Average (by Volume)
            raw_weighted = (total_value / total_qty) if total_qty else 0.0
            # 2. Simple Arithmetic Average
            raw_simple = (sum(prices) / len(prices)) if prices else 0.0
            # 3. Log Average (Geometric Mean)
            raw_log = (
                math.exp(sum(math.log(p) for p in pos_prices) / len(pos_prices))
                if pos_prices else 0.0
            )
            # 4. Volume-Weighted Log Average
            pos_qty = sum(q for q, _ in pos_pairs)
            raw_weighted_log = (
                math.exp(
                    sum(q * math.log(p) for q, p in pos_pairs) / pos_qty)
                if pos_qty else 0.0
            )
            # 5 & 6. FIFO (Oldest) & LIFO (Latest) & Moving Average (Last 3)
            chronological_items.sort(key=lambda item: item[:5])
            raw_fifo = chronological_items[0][6] if chronological_items else 0.0
            raw_lifo = (
                chronological_items[-1][6] if chronological_items else 0.0)
            recent_three = chronological_items[-3:]
            recent_qty = sum(item[5] for item in recent_three)
            raw_moving = (
                sum(item[5] * item[6] for item in recent_three) / recent_qty
                if recent_qty else 0.0
            )
            # 7. Median Cost
            sorted_prices = sorted(prices)
            n_prices = len(sorted_prices)
            if not n_prices:
                raw_median = 0.0
            elif n_prices % 2 == 1:
                raw_median = sorted_prices[n_prices // 2]
            else:
                raw_median = (
                    sorted_prices[n_prices // 2 - 1]
                    + sorted_prices[n_prices // 2]
                ) / 2.0
            # 8. Harmonic Average
            raw_harmonic = (
                len(pos_prices) / sum(1.0 / p for p in pos_prices)
                if pos_prices else 0.0
            )
            # 9 & 10. Lowest & Highest
            raw_low = min(prices) if prices else 0.0
            raw_high = max(prices) if prices else 0.0

            method = template._vuma_effective_costing_method()
            method_map = {
                'weighted_average': raw_weighted,
                'simple_average': raw_simple,
                'log_average': raw_log,
                'weighted_log_average': raw_weighted_log,
                'fifo': raw_fifo,
                'lifo': raw_lifo,
                'moving_average': raw_moving,
                'median_cost': raw_median,
                'harmonic_average': raw_harmonic,
                'highest_cost': raw_high,
                'lowest_cost': raw_low,
                'manual': template.vuma_gross_cost or 0.0,
            }
            raw_selected = method_map.get(method, raw_weighted)

            template.vuma_cost_line_count = len(points)
            template.vuma_cost_total_qty = total_qty
            template.vuma_cost_total_value = total_value
            template.vuma_weighted_cost = round_curr(raw_weighted)
            template.vuma_simple_avg_cost = round_curr(raw_simple)
            template.vuma_log_avg_cost = round_curr(raw_log)
            template.vuma_fifo_cost = round_curr(raw_fifo)
            template.vuma_lifo_cost = round_curr(raw_lifo)
            template.vuma_cost_low = round_curr(raw_low)
            template.vuma_cost_high = round_curr(raw_high)
            template.vuma_calculated_cost = round_curr(raw_selected)
            template.vuma_effective_costing_method_label = (
                COSTING_METHOD_LABELS.get(method, method))
            template.vuma_gross_cost_driven = (
                bool(points) and method != 'manual')

    @api.depends(
        'vuma_gross_cost',
        'vuma_import_duty_percent',
        'vuma_costing_method',
        'company_id.vuma_costing_method',
        'vuma_cost_line_ids.subtotal',
        'vuma_cost_line_ids.quantity',
        'vuma_cost_line_ids.unit_price',
        'vuma_cost_line_ids.transport_cost',
        'vuma_cost_line_ids.import_duty_cost',
        'vuma_cost_line_ids.other_cost',
        *ADDITIONAL_COST_FIELDS,
    )
    def _compute_vuma_nett_cost(self):
        return super()._compute_vuma_nett_cost()

    def _vuma_base_gross_cost_value(self):
        """Base supplier cost from the chosen costing method when points exist."""
        self.ensure_one()
        if self.vuma_cost_line_ids:
            self._compute_cost_summary()
            if self.vuma_gross_cost_driven:
                return self.vuma_calculated_cost or 0.0
        return super()._vuma_base_gross_cost_value()

    @api.depends('vuma_vat_treatment', 'taxes_id', 'taxes_id.amount')
    def _compute_vuma_vat_box(self):
        for template in self:
            tax = template._vuma_tax_for_treatment(
                template.vuma_vat_treatment)
            template.vuma_vat_box = tax._vuma_sars_box() if tax else ''

    # -------------------------------------------------- VAT treatment -> tax

    def _vuma_tax_for_treatment(self, treatment=None):
        """The sale tax matching this VAT treatment, for this company."""
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
        """Put the matching tax on the product, keeping the rest intact."""
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
        """Push the active method's calculated cost into Gross Cost & solve."""
        provided = set(provided)
        self._compute_cost_summary()
        for template in self:
            if not template.vuma_gross_cost_driven:
                continue
            target = template.vuma_calculated_cost
            if float_compare(
                    template.vuma_gross_cost, target, precision_digits=6):
                template.with_context(**{
                    SOLVE_CONTEXT_KEY: True,
                    COST_SYNC_CONTEXT_KEY: True,
                }).write({'vuma_gross_cost': target})
            template.with_context(**{
                COST_SYNC_CONTEXT_KEY: True,
            })._vuma_sync_import_duty_from_percent()
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
            if 'vuma_cost_line_ids' in vals or 'vuma_costing_method' in vals:
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

        if 'vuma_cost_line_ids' in vals or 'vuma_costing_method' in vals:
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

    @api.onchange('vuma_cost_line_ids', 'vuma_costing_method')
    def _onchange_vuma_cost_lines(self):
        """Mirror the saved behaviour while the form is still open."""
        self._compute_cost_summary()
        for template in self:
            if not template.vuma_gross_cost_driven:
                continue
            template.vuma_gross_cost = template.vuma_calculated_cost
            if (template.vuma_import_duty_percent or 0.0) > 0.0:
                template.vuma_import_duty_cost = (
                    template._vuma_round_pricing_field(
                        'vuma_import_duty_cost',
                        template.vuma_gross_cost
                        * template.vuma_import_duty_percent / 100.0))
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
