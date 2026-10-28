# -*- coding: utf-8 -*-
"""Spreadsheet pricing on the product profile.

A buyer prices an item the way a spreadsheet does it -- seven numbers that
lean on each other::

    Gross Cost      typed by hand
    Transport Cost  typed by hand (zero when transport is inside the cost)
    Nett Cost       = Gross Cost + Transport Cost
    Markup %        one of these four is typed, the
    Markup Value    other three are worked out from it
    Selling ex VAT  :
    Selling inc VAT :

The three cost lines are fixed: Nett Cost is always gross plus transport.
Off that nett cost, exactly one of the markup / selling figures is a
*constant* (the cell the user typed last) and the other three are the
*formulas*.  ``vuma_pricing_driver`` remembers which one is the constant, so
changing the gross cost later moves every figure except the one the buyer
pinned -- which is exactly how the same sheet behaves in Excel.

Two of the seven then feed Odoo's own price fields on save:

* **Selling incl VAT** -> *Sales Price* (``list_price``), so labels, pricelists,
  POS and quotations all pick the shelf price up.  When the product's taxes are
  not included in the price, the VAT-exclusive figure is written instead, so
  Odoo never taxes a price twice;
* **Nett Cost** -> *Cost* (``standard_price``), so margins and stock valuation
  follow the landed cost.

The VAT percentage is read from the product's own sales taxes rather than
typed, so the inclusive price on the tab always agrees with what an invoice
charges.
"""
from odoo import api, fields, models
from odoo.tools import float_compare, float_round

#: The four figures solved from the nett cost, in precedence order.  When a
#: write carries exactly one of them, that one is the figure being typed.
PRICING_FIELDS = (
    'vuma_markup_percent',
    'vuma_markup_value',
    'vuma_selling_ex_vat',
    'vuma_selling_inc_vat',
)

#: The two inputs that define the nett cost.
COST_FIELDS = (
    'vuma_gross_cost',
    'vuma_transport_cost',
)

#: Everything whose change means the solved figures may be out of date.
PRICING_TRIGGER_FIELDS = PRICING_FIELDS + COST_FIELDS + (
    'vuma_pricing_driver',
    'taxes_id',
)

#: Set while the solver is writing its own answers, so write() does not try
#: to solve them all over again.
SOLVE_CONTEXT_KEY = 'vuma_pricing_solved'


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vuma_gross_cost = fields.Float(
        string='Gross Cost', digits='Product Price', default=0.0,
        help='What the supplier charges for one unit, before transport is '
             'added.')
    vuma_transport_cost = fields.Float(
        string='Transport Cost', digits='Product Price', default=0.0,
        help='Delivery landed on one unit. Leave at zero when transport is '
             'already inside the gross cost.')
    vuma_nett_cost = fields.Float(
        string='Nett Cost', digits='Product Price',
        compute='_compute_vuma_nett_cost', store=True,
        help='Gross Cost + Transport Cost. Every figure below is worked out '
             'from this number.')
    vuma_vat_percent = fields.Float(
        string='VAT %', digits='Product Price',
        compute='_compute_vuma_vat_percent',
        help='Rate taken from this item\'s sales taxes. The VAT-inclusive '
             'selling price is worked out with it.')
    vuma_markup_percent = fields.Float(
        string='Markup %', digits=(16, 6), min_display_digits=2, default=0.0,
        help='Markup on the nett cost: 30 means the item sells for the nett '
             'cost plus 30% of it.')
    vuma_markup_value = fields.Float(
        string='Markup Value', digits='Product Price', default=0.0,
        help='Profit on one unit: selling price excluding VAT minus the nett '
             'cost.')
    vuma_selling_ex_vat = fields.Float(
        string='Selling Excl VAT', digits='Product Price', default=0.0,
        help='Nett cost plus the markup value, before VAT.')
    vuma_selling_inc_vat = fields.Float(
        string='Selling Incl VAT', digits='Product Price', default=0.0,
        help='The shelf price: selling excluding VAT plus VAT.')
    vuma_pricing_driver = fields.Selection([
        ('vuma_markup_percent', 'Markup %'),
        ('vuma_markup_value', 'Markup Value'),
        ('vuma_selling_ex_vat', 'Selling Excl VAT'),
        ('vuma_selling_inc_vat', 'Selling Incl VAT'),
    ], string='Price Source', default='vuma_markup_percent',
        help='Which of the four figures was typed by hand. The other three '
             'are worked out from it, the way a spreadsheet works out the '
             'cells holding a formula.')

    # ------------------------------------------------------------------ maths

    @api.depends('vuma_gross_cost', 'vuma_transport_cost')
    def _compute_vuma_nett_cost(self):
        for template in self:
            template.vuma_nett_cost = template._vuma_nett_cost_value()

    @api.depends('taxes_id', 'taxes_id.amount')
    def _compute_vuma_vat_percent(self):
        for template in self:
            template.vuma_vat_percent = template._vuma_vat_percent_value()

    def _vuma_nett_cost_value(self):
        """Gross cost plus transport cost, read live.

        Computed from the two inputs instead of ``vuma_nett_cost`` so that an
        onchange, which can run before the stored field is recomputed, never
        solves a pricing chain from a stale cost.
        """
        self.ensure_one()
        return (self.vuma_gross_cost or 0.0) + (self.vuma_transport_cost or 0.0)

    def _vuma_company_taxes(self):
        """Sales taxes for this item's company (or the active company).

        A shared product created while several companies are active in
        ``env.companies`` can carry a default sale tax from each company; only
        the current company's tax applies to one invoice or price calculation.
        """
        self.ensure_one()
        taxes = self.taxes_id
        if not taxes:
            return taxes
        company = self.company_id or self.env.company
        scoped = taxes.filtered(lambda tax: tax.company_id == company)
        return scoped or taxes[:1]

    def _vuma_vat_percent_value(self):
        """The percentage rate of this item's sales taxes, read live."""
        self.ensure_one()
        taxes = self._vuma_company_taxes().filtered(
            lambda tax: tax.amount_type == 'percent')
        return sum(taxes.mapped('amount')) if taxes else 0.0

    def _vuma_round_pricing_field(self, field_name, value):
        """Round ``value`` the way ``field_name`` is stored."""
        self.ensure_one()
        digits = self._fields[field_name].get_digits(self.env)
        return float_round(value, precision_digits=digits[1]) if digits else value

    def _vuma_pricing_solution(self, driver=None):
        """The four solved figures, starting from the pinned one.

        The pinned value is read as it stands; everything else is derived
        from it in one go (never from another derived figure), so rounding a
        value for storage can never walk the chain away from the number the
        buyer typed.
        """
        self.ensure_one()
        driver = driver or self.vuma_pricing_driver or 'vuma_markup_percent'
        nett = self._vuma_nett_cost_value()
        rate = (self._vuma_vat_percent_value() or 0.0) / 100.0

        if driver == 'vuma_markup_percent':
            markup_value = nett * (self.vuma_markup_percent or 0.0) / 100.0
            selling_ex = nett + markup_value
        elif driver == 'vuma_markup_value':
            markup_value = self.vuma_markup_value or 0.0
            selling_ex = nett + markup_value
        elif driver == 'vuma_selling_ex_vat':
            selling_ex = self.vuma_selling_ex_vat or 0.0
            markup_value = selling_ex - nett
        else:
            selling_ex = (
                (self.vuma_selling_inc_vat or 0.0) / (1.0 + rate)
                if rate > -1.0 else 0.0)
            markup_value = selling_ex - nett

        markup_percent = (markup_value / nett * 100.0) if nett else 0.0
        return {
            'vuma_markup_percent': markup_percent,
            'vuma_markup_value': markup_value,
            'vuma_selling_ex_vat': selling_ex,
            'vuma_selling_inc_vat': selling_ex * (1.0 + rate),
        }

    def _vuma_price_includes_vat(self):
        """Does Odoo's Sales Price hold a VAT-inclusive figure?"""
        self.ensure_one()
        taxes = self._vuma_company_taxes()
        if taxes:
            return all(tax.price_include for tax in taxes)
        company = self.company_id or self.env.company
        return company.account_price_include == 'tax_included'

    def _vuma_sales_price_value(self, figures):
        """What Odoo's Sales Price should be set to, rounded to a cent.

        ``figures`` maps the four pricing fields onto the values they will
        hold once the solve has been written.  Odoo wants the VAT-inclusive
        figure when the product's taxes sit inside the price and the
        VAT-exclusive one when they are added on top, so that a price is
        never taxed twice.
        """
        self.ensure_one()
        key = (
            'vuma_selling_inc_vat' if self._vuma_price_includes_vat()
            else 'vuma_selling_ex_vat')
        selling = figures.get(key) or 0.0
        if selling <= 0:
            return 0.0
        currency = self.currency_id or self.env.company.currency_id
        return currency.round(selling) if currency else selling

    def _vuma_cost_value(self):
        """What Odoo's Cost should be set to, rounded to a cent."""
        self.ensure_one()
        nett = self._vuma_nett_cost_value()
        if nett <= 0:
            return 0.0
        currency = self.cost_currency_id or self.env.company.currency_id
        return currency.round(nett) if currency else nett

    # ----------------------------------------------------------------- solve

    def _vuma_solve_pricing(self, provided=()):
        """Fill in every pricing figure the caller did not supply.

        ``provided`` names the fields that arrived with the change.  A field
        the caller sent is left exactly as sent; the rest are worked out from
        the pinned figure, so a hand-typed number always wins over the
        arithmetic.
        """
        provided = set(provided)
        for template in self:
            driver = template.vuma_pricing_driver or 'vuma_markup_percent'
            solution = template._vuma_pricing_solution(driver)
            values = {}
            for field_name, value in solution.items():
                if field_name == driver or field_name in provided:
                    continue
                current = template[field_name] or 0.0
                target = template._vuma_round_pricing_field(field_name, value)
                if float_compare(current, target, precision_digits=6):
                    values[field_name] = value
            # The four figures as they will stand once ``values`` is written:
            # the ones still to be written come from the solution, the rest --
            # the pinned figure and anything the caller sent -- are already on
            # the record.  The Sales Price is worked out from these, not from
            # what happens to be stored yet.
            figures = {
                name: (
                    template._vuma_round_pricing_field(name, values[name])
                    if name in values else template[name])
                for name in PRICING_FIELDS
            }
            values.update(template._vuma_price_write_back(figures))
            if values:
                template.with_context(**{
                    SOLVE_CONTEXT_KEY: True,
                }).write(values)
        return True

    def _vuma_price_write_back(self, figures):
        """Feed the solved numbers into Odoo's own price fields."""
        self.ensure_one()
        values = {}
        sales_price = self._vuma_sales_price_value(figures)
        if sales_price and float_compare(
                self.list_price, sales_price, precision_digits=6):
            values['list_price'] = sales_price
        cost = self._vuma_cost_value()
        if cost and float_compare(
                self.standard_price, cost, precision_digits=6):
            values['standard_price'] = cost
        return values

    def _vuma_pricing_from_sales_price(self):
        """The Sales Price was changed elsewhere: follow it here.

        Returns whether every record was followed.  Only products that
        already carry a cost are followed, so an item with no pricing on the
        tab yet does not grow a half-filled one the first time somebody
        edits its price.
        """
        followed = True
        for template in self:
            if not template._vuma_nett_cost_value() or not template.list_price:
                followed = False
                continue
            driver = (
                'vuma_selling_inc_vat' if template._vuma_price_includes_vat()
                else 'vuma_selling_ex_vat')
            template.with_context(**{SOLVE_CONTEXT_KEY: True}).write({
                'vuma_pricing_driver': driver,
                driver: template.list_price,
            })
            template._vuma_solve_pricing({driver})
        return followed

    @api.model
    def _vuma_pricing_driver_from_vals(self, vals):
        """Which figure does this change pin down?

        One figure sent on its own is somebody typing it.  Several figures
        together are this solver's own answer coming back from the client, so
        the stored pin is kept.
        """
        pinned = vals.get('vuma_pricing_driver')
        if pinned in PRICING_FIELDS:
            return pinned
        sent = [name for name in PRICING_FIELDS if name in vals]
        return sent[0] if len(sent) == 1 else False

    # --------------------------------------------------------- ORM overrides

    def write(self, vals):
        # The solver's own write: every value in it was already worked out.
        if self.env.context.get(SOLVE_CONTEXT_KEY):
            return super().write(vals)
        pricing_touched = any(
            name in vals for name in PRICING_TRIGGER_FIELDS + ('list_price',))
        if not pricing_touched:
            return super().write(vals)

        driver = self._vuma_pricing_driver_from_vals(vals)
        if driver:
            vals = dict(vals, vuma_pricing_driver=driver)
        res = super().write(vals)
        if any(name in vals for name in PRICING_FIELDS):
            # One of the four figures came in on its own: it is the new pin.
            self._vuma_solve_pricing(vals)
        elif 'list_price' in vals:
            # The Sales Price was changed by hand: the tab follows it.
            self._vuma_pricing_from_sales_price()
        else:
            # Costs, taxes or the pin itself moved: re-solve from the pin.
            self._vuma_solve_pricing(vals)
        return res

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            driver = self._vuma_pricing_driver_from_vals(vals)
            if driver:
                vals['vuma_pricing_driver'] = driver
        templates = super().create(vals_list)
        for template, vals in zip(templates, vals_list):
            if any(name in vals for name in PRICING_FIELDS):
                template._vuma_solve_pricing(vals)
            elif 'list_price' in vals:
                # A price arriving with the item is the pin, so the costs
                # that came with it are worked out from it, not over it.
                template._vuma_pricing_from_sales_price()
            elif any(name in vals for name in PRICING_TRIGGER_FIELDS):
                template._vuma_solve_pricing(vals)
        return templates

    # ------------------------------------------------------------- onchange

    @api.onchange('vuma_gross_cost', 'vuma_transport_cost')
    def _onchange_vuma_pricing_costs(self):
        self._compute_vuma_nett_cost()
        self._vuma_solve_onchange()

    @api.onchange('taxes_id')
    def _onchange_vuma_pricing_taxes(self):
        self._compute_vuma_vat_percent()
        self._vuma_solve_onchange()

    @api.onchange('list_price')
    def _onchange_vuma_sales_price(self):
        for template in self:
            if not template._vuma_nett_cost_value():
                continue
            driver = (
                'vuma_selling_inc_vat' if template._vuma_price_includes_vat()
                else 'vuma_selling_ex_vat')
            template.vuma_pricing_driver = driver
            template[driver] = template.list_price
            template._vuma_solve_onchange(driver)

    @api.onchange('vuma_markup_percent')
    def _onchange_vuma_markup_percent(self):
        self._vuma_pin_onchange('vuma_markup_percent')

    @api.onchange('vuma_markup_value')
    def _onchange_vuma_markup_value(self):
        self._vuma_pin_onchange('vuma_markup_value')

    @api.onchange('vuma_selling_ex_vat')
    def _onchange_vuma_selling_ex_vat(self):
        self._vuma_pin_onchange('vuma_selling_ex_vat')

    @api.onchange('vuma_selling_inc_vat')
    def _onchange_vuma_selling_inc_vat(self):
        self._vuma_pin_onchange('vuma_selling_inc_vat')

    def _vuma_pin_onchange(self, driver):
        """Pin ``driver`` when the user really typed into it.

        The form fires this method again for every figure the solver has just
        filled in (onchange runs one pass per changed field).  Those answers
        already agree with the pinned figure, so they do not move the pin;
        only a number that disagrees with the current chain is a new one typed
        by hand.
        """
        for template in self:
            pinned = template.vuma_pricing_driver or 'vuma_markup_percent'
            if driver != pinned:
                implied = template._vuma_pricing_solution(pinned)[driver]
                typed = template[driver] or 0.0
                if not float_compare(
                        typed,
                        template._vuma_round_pricing_field(driver, implied),
                        precision_digits=6):
                    continue
            template.vuma_pricing_driver = driver
            template._vuma_solve_onchange(driver)

    def _vuma_solve_onchange(self, driver=None):
        """Fill the form in the same way write() fills the database."""
        for template in self:
            pinned = (
                driver or template.vuma_pricing_driver
                or 'vuma_markup_percent')
            solution = template._vuma_pricing_solution(pinned)
            for field_name, value in solution.items():
                if field_name == pinned:
                    continue
                template[field_name] = value
