# -*- coding: utf-8 -*-
"""
Sale Order / Order Line extensions for the Vuma dual-company mixed basket.

* New quotes created by Mixed Basket users are mixed by default.
* On confirm a mixed quote auto-splits into one sale order per company.
* EVERY line records which company's stock it draws from -- not just the
  company name, but the exact stock location and the quantity available
  there -- so stock is always traceable from the order line down to the
  quant.

Stock ownership rules
---------------------
A product may be owned by one company (``product.company_id`` set) or be
shared across both (``company_id`` empty, stock held in both warehouses).
The LINE, not the product, is what carries the company: that is what decides
which company quote the line joins and which company's stock is invoiced.
Shared products therefore resolve to the quote's own company unless the rep
picks the other one explicitly, and the choice is always visible.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    vuma_is_mixed_basket = fields.Boolean(
        string='Mixed Basket Quote',
        default=False,
        index=True,
        help="Quote holding lines from both companies; auto-split on confirm.",
    )
    vuma_split_quote_trade_rite_id = fields.Many2one(
        'sale.order', string='Trade Rite Quote', readonly=True, copy=False)
    vuma_split_quote_siyaya_dc_id = fields.Many2one(
        'sale.order', string='Siyaya DC Quote', readonly=True, copy=False)
    vuma_original_mixed_quote_id = fields.Many2one(
        'sale.order', string='Original Mixed Quote', readonly=True, copy=False, index=True)

    # ------------------------------------------------------------------
    # Traceability summary shown on the quote
    # ------------------------------------------------------------------
    vuma_source_company_summary = fields.Char(
        string='Stock Source Summary', compute='_compute_vuma_source_summary',
        help="Which company's stock each line draws from.")
    vuma_untraced_line_ids = fields.Many2many(
        'sale.order.line', 'vuma_order_untraced_line_rel', 'order_id', 'line_id',
        string='Lines Without Stock Source', compute='_compute_vuma_source_summary',
        help="Lines that have a product but no stock-source company assigned.")
    vuma_mixed_basket_available = fields.Boolean(
        string='Mixed Basket Available', compute='_compute_vuma_mixed_basket_available',
        help="True only while Trade Rite and Siyaya DC are both in the active "
             "company scope. A mixed basket spans both companies, so it cannot "
             "be captured from a single-company scope.")

    @api.depends_context('allowed_company_ids')
    def _compute_vuma_mixed_basket_available(self):
        available = (
            self.env.user.has_group(
                'vuma_multi_tenant.group_vuma_mixed_basket_user')
            and self.env.user._vuma_has_both_trading_companies()
        )
        for order in self:
            order.vuma_mixed_basket_available = available

    @api.depends('order_line.vuma_source_company_id',
                 'order_line.product_id')
    def _compute_vuma_source_summary(self):
        for order in self:
            untraced = order.order_line.filtered(
                lambda l: l.product_id and not l.vuma_source_company_id)
            order.vuma_untraced_line_ids = untraced
            per_company = {}
            for line in order.order_line:
                company = line.vuma_source_company_id
                if not company:
                    continue
                per_company[company.name] = per_company.get(company.name, 0) + 1
            order.vuma_source_company_summary = ', '.join(
                f'{name} ({count})' for name, count in sorted(per_company.items()))

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        # Mixed quotes are the default for Mixed Basket users, but only while
        # both trading companies are active. On a single-company scope the
        # quote must stay a normal single-company quote: a Siyaya stock line
        # cannot be captured while the user is working in Trade Rite.
        if 'vuma_is_mixed_basket' in fields_list and not res.get('vuma_is_mixed_basket'):
            res['vuma_is_mixed_basket'] = (
                self.env.user.has_group(
                    'vuma_multi_tenant.group_vuma_mixed_basket_user')
                and self.env.user._vuma_has_both_trading_companies()
            )
        return res

    @api.model_create_multi
    def create(self, vals_list):
        company_to_check = self.env['res.company']
        for vals in vals_list:
            if vals.get('company_id'):
                company_to_check |= self.env['res.company'].browse(
                    vals['company_id'])
            if vals.get('vuma_is_mixed_basket'):
                self.env.user._vuma_require_both_trading_companies(
                    _('capture an order across both companies'))
        self.env.user._vuma_require_companies(
            company_to_check, _('capture a sales order'))
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('company_id'):
            self.env.user._vuma_require_company(
                self.env['res.company'].browse(vals['company_id']),
                _('move this sales order to another company'))
        if vals.get('vuma_is_mixed_basket'):
            self.env.user._vuma_require_both_trading_companies(
                _('capture an order across both companies'))
        return super().write(vals)

    def action_confirm(self):
        mixed = self.filtered('vuma_is_mixed_basket')
        if mixed:
            # Splitting a basket creates one order per company. That is a
            # cross-company capture and is only allowed with both companies
            # active, even when the quote was prepared earlier.
            self.env.user._vuma_require_both_trading_companies(
                _('confirm a mixed-basket order across both companies'))
            return mixed[0]._action_confirm_and_split()

        self._vuma_validate_single_company_lines()
        return super().action_confirm()

    def _vuma_validate_single_company_lines(self):
        """A normal quote may only use its own company's stock.

        ``vuma_source_company_id`` is the field that decides which company
        invoices and delivers the line. Letting it point at the other company
        while the order stays in this one would silently book stock and
        revenue in the wrong company, so the confirm is refused instead.
        """
        for order in self:
            lines = order.order_line.filtered('product_id')
            lines.filtered(
                lambda l: not l.vuma_source_company_id
            )._vuma_apply_source_company()
            wrong = lines.filtered(
                lambda l: l.vuma_source_company_id
                and l.vuma_source_company_id != order.company_id)
            if not wrong:
                continue
            raise UserError(_(
                "This order belongs to %(order_company)s, but these lines use "
                "another company's stock:\n%(lines)s\n\n"
                "Switch the company selector to the line's company (or to "
                "'Trade Rite + Siyaya DC' for a mixed basket) before confirming.",
                order_company=order.company_id.display_name,
                lines='\n'.join(
                    "  - %(product)s (%(qty)g) — stock source %(source)s" % {
                        'product': line.product_id.display_name,
                        'qty': line.product_uom_qty,
                        'source': line.vuma_source_company_id.display_name,
                    }
                    for line in wrong
                ),
            ))

    # ------------------------------------------------------------------
    # Split
    # ------------------------------------------------------------------
    @api.constrains('company_id', 'order_line')
    def _check_order_line_company_id(self):
        """Mixed-basket quotes legitimately hold lines from both companies."""
        if self.env.context.get('vuma_mixed_basket'):
            return
        orders = self - self.filtered('vuma_is_mixed_basket')
        if orders:
            return super(SaleOrder, orders)._check_order_line_company_id()

    def _action_confirm_and_split(self):
        self.ensure_one()
        self.env.user._vuma_require_both_trading_companies(
            _('split this order across both companies'))
        trade, siyaya = self._vuma_mixed_basket_companies()
        if not trade or not siyaya:
            raise UserError(_('Both Trade Rite and Siyaya DC companies must exist to split a mixed quote.'))

        lines = self.order_line.filtered(lambda l: l.product_id)
        if not lines:
            raise UserError(_('Add at least one product line before confirming a mixed quote.'))

        # Never guess. Every line must state which company's stock it uses,
        # otherwise the invoice, the stock move and the revenue would all be
        # attributed to an arbitrary company.
        untraced = lines.filtered(lambda l: not l.vuma_source_company_id)
        if untraced:
            raise UserError(_(
                'These lines have no stock-source company, so the basket cannot be '
                'split without guessing which company owns the stock:\n%(lines)s\n\n'
                'Open the "Stock Source" column on each line and pick the company '
                'that holds the stock, then confirm again.'
            ) % {
                'lines': '\n'.join(
                    f'  - {l.product_id.display_name} (qty {l.product_uom_qty:g})'
                    for l in untraced),
            })

        buckets = {trade: self.env['sale.order.line'], siyaya: self.env['sale.order.line']}
        for line in lines:
            company = line.vuma_source_company_id
            if company not in buckets:
                raise UserError(_(
                    'Line "%(product)s" is assigned to company %(company)s, which is '
                    'not one of the two trading companies (Trade Rite / Siyaya DC).'
                ) % {'product': line.product_id.display_name, 'company': company.display_name})
            buckets[company] |= line
        buckets = {c: l for c, l in buckets.items() if l}

        # A company-owned product may only ever be sold by its owner.
        for company, company_lines in buckets.items():
            wrong = company_lines.filtered(
                lambda l, c=company: l.product_id.company_id and l.product_id.company_id != c)
            if wrong:
                raise UserError(_(
                    'These products belong to another company and cannot be sold by '
                    '%(company)s:\n%(products)s\n\nPick the owning company as the stock '
                    'source on those lines.'
                ) % {
                    'company': company.display_name,
                    'products': '\n'.join(
                        f'  - {l.product_id.display_name} (owned by '
                        f'{l.product_id.company_id.display_name})' for l in wrong),
                })

        created = {}
        for company, company_lines in buckets.items():
            order = self._create_company_quote(company, company_lines)
            created[company.name] = order
            if company == trade:
                self.vuma_split_quote_trade_rite_id = order
            else:
                self.vuma_split_quote_siyaya_dc_id = order
            order.vuma_is_mixed_basket = False

        created_orders = list(created.values())
        for o in created_orders:
            o.vuma_original_mixed_quote_id = self

        self.message_post(body=_(
            'Mixed basket split into company quotations: %s'
        ) % (', '.join(q.name or _('(draft)') for q in created_orders)))

        # Odoo 20 has no `active` on sale.order: the shell quote is CANCELLED
        # (grey in lists, excluded from active quotation filters) so the
        # split orders remain the single source of truth.
        self.action_cancel()

        return self._action_view_created(created_orders)

    def _vuma_mixed_basket_companies(self):
        """(Trade Rite, Siyaya DC) in that order."""
        companies = self.env['res.company'].sudo().search([
            '|', ('name', '=', 'Trade Rite'), ('name', '=', 'Siyaya DC'),
        ])
        return (
            companies.filtered(lambda c: c.name == 'Trade Rite')[:1],
            companies.filtered(lambda c: c.name == 'Siyaya DC')[:1],
        )

    def _create_company_quote(self, company, lines):
        self.ensure_one()
        self.env.user._vuma_require_company(
            company, _('create the %s quotation', company.display_name))
        Order = self.env['sale.order'].with_company(company).with_context(
            vuma_mixed_basket=True)
        partner = self.partner_id
        pricelist = self._vuma_company_pricelist(company, partner)
        warehouse = self._vuma_company_warehouse(company)
        if not warehouse:
            raise UserError(_(
                'No stock warehouse is configured for %(company)s, so its stock '
                'cannot be reserved. Create a warehouse for the company first.'
            ) % {'company': company.display_name})

        vals = {
            'partner_id': partner.id,
            # The split orders are NORMAL single-company orders (never re-split).
            'vuma_is_mixed_basket': False,
            'partner_shipping_id': (self.partner_shipping_id or self.partner_id).id,
            'partner_invoice_id': (self.partner_invoice_id or self.partner_id).id,
            'pricelist_id': pricelist and pricelist.id,
            'warehouse_id': warehouse.id,
            'validity_date': self.validity_date,
            'note': self.note,
            'company_id': company.id,
            'origin': _('Split from mixed quote %s') % (self.name or _('(draft)')),
            'client_order_ref': self.client_order_ref,
            'user_id': self.user_id.id or self.env.uid,
            'order_line': [
                (0, 0, l._vuma_company_line_vals(company, pricelist)) for l in lines
            ],
        }
        order = Order.create(vals)
        order.message_post(body=_(
            'Created from mixed-basket quote %s (lines: %s)'
        ) % (self.name or _('(draft)'), len(lines)))
        # The rep confirms ONCE on the mixed quote: both company orders go
        # to sales-order state immediately (no profile switching).
        order.action_confirm()
        return order

    def _vuma_company_pricelist(self, company, partner):
        pl = partner.with_company(company).property_product_pricelist
        if pl and pl.company_id == company:
            return pl
        return self.env.ref('vuma_multi_tenant.pricelist_trade_rite_retail') if company.name == 'Trade Rite' \
            else self.env.ref('vuma_multi_tenant.pricelist_siyaya_dc_retail')

    def _vuma_company_warehouse(self, company):
        """Available as instance helper via env."""
        return self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', company.id)], limit=1)

    def _action_view_created(self, orders):
        ids = [o.id for o in orders]
        if len(ids) == 1:
            return {
                'name': _('Split Quote'), 'type': 'ir.actions.act_window',
                'res_model': 'sale.order', 'res_id': ids[0],
                'view_mode': 'form', 'target': 'current',
            }
        return self.action_view_split_quotes()

    def action_view_split_quotes(self):
        self.ensure_one()
        ids = [q.id for q in (self.vuma_split_quote_trade_rite_id,
                              self.vuma_split_quote_siyaya_dc_id) if q]
        if not ids:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'name': _('Split Quotes'), 'type': 'ir.actions.act_window',
            'res_model': 'sale.order', 'view_mode': 'list,form',
            'domain': [('id', 'in', ids)], 'target': 'current',
        }

    def action_view_original_mixed_quote(self):
        self.ensure_one()
        if not self.vuma_original_mixed_quote_id:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'name': _('Original Mixed Quote'), 'type': 'ir.actions.act_window',
            'res_model': 'sale.order', 'res_id': self.vuma_original_mixed_quote_id.id,
            'view_mode': 'form', 'target': 'current',
        }

    def action_new_mixed_basket(self):
        """Open a blank mixed-basket quote form.

        ``sale_order.partner_id`` is NOT NULL in the database, so this cannot
        pre-create the record: doing so raised a raw ``NotNullViolation``
        (500) on every click. We hand the client a defaulted action instead and
        let the form create the record once the customer is chosen.
        """
        self.ensure_one()
        self.env.user._vuma_require_both_trading_companies(
            _('start a mixed-basket order'))
        company = (
            self.company_id
            or self.env.user.vuma_default_mixed_basket_company_id
            or self.env.user.company_id
        )
        return {
            'name': _('New Mixed Basket Quote'),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order',
            'view_mode': 'form',
            'target': 'current',
            'context': dict(
                self.env.context,
                vuma_mixed_basket=True,
                default_vuma_is_mixed_basket=True,
                default_company_id=company.id or False,
            ),
        }

    def action_view_untraced_lines(self):
        """Jump straight to the lines that still need a stock source."""
        self.ensure_one()
        lines = self.order_line.filtered(
            lambda l: l.product_id and not l.vuma_source_company_id)
        if not lines:
            return {'type': 'ir.actions.act_window_close'}
        return {
            'name': _('Lines Without Stock Source'),
            'type': 'ir.actions.act_window',
            'res_model': 'sale.order.line',
            'view_mode': 'list,form',
            'domain': [('id', 'in', lines.ids)],
            'target': 'current',
            'context': dict(self.env.context, vuma_mixed_basket=True),
        }


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    vuma_source_company_id = fields.Many2one(
        'res.company', string='Stock Source',
        index=True,
        help="Company whose stock this line uses. It decides which company "
             "quote the line joins and which company's stock is invoiced. "
             "Shared products (stock in both warehouses) must be assigned to "
             "one company explicitly.")
    vuma_source_location_id = fields.Many2one(
        'stock.location', string='Stock Location', readonly=True, copy=False,
        help="Internal location the line's stock is drawn from. This is the "
             "traceable link from the order line down to the stock quant.")
    vuma_source_available_qty = fields.Float(
        string='Available', compute='_compute_vuma_source_available_qty',
        help="Units currently on hand in the stock source location.")

    # ------------------------------------------------------------------
    # Stock source resolution
    # ------------------------------------------------------------------
    @api.model
    def _vuma_mixed_basket_companies(self):
        return self.env['res.company'].sudo().search([
            '|', ('name', '=', 'Trade Rite'), ('name', '=', 'Siyaya DC'),
        ])

    def _vuma_cross_company_active(self):
        """Is the product picker allowed to reach the OTHER trading company?

        An explicit flag in the context wins even when it is False. The order
        line context carries the order's own ``vuma_is_mixed_basket`` (see the
        ``order_line`` field in the form view), so on a single-company quote it
        is present and False. Falling through to the group check in that case
        left the picker showing the other company's goods on every ordinary
        order: the rep was offered a Siyaya DC product, picked it, and was then
        refused by Odoo's company constraint with a message about a product
        they had just chosen. The group is only the fallback when no order has
        spoken yet (RPCs, imports, on a brand-new quote).

        The active company scope is checked first. A user who is working in
        Trade Rite alone must not be offered Siyaya DC products, even if the
        context still carries a mixed-basket flag from a quote prepared while
        both companies were active.
        """
        if not self.env.user._vuma_has_both_trading_companies():
            return False
        flag = self.env.context.get('vuma_mixed_basket')
        if flag is not None:
            return bool(flag)
        return self.env.user.has_group('vuma_multi_tenant.group_vuma_mixed_basket_user')

    def _vuma_stock_location(self, company):
        """Internal stock location of the company's warehouse."""
        warehouse = self.env['stock.warehouse'].sudo().search(
            [('company_id', '=', company.id)], limit=1)
        return warehouse.lot_stock_id if warehouse else self.env['stock.location'].browse()

    def _vuma_candidate_source_companies(self, product=None):
        """Companies that actually hold stock for ``product``.

        Ordering is deliberate: the two trading companies first, then any
        other company that still holds stock, so the picker only ever offers
        a company that can actually supply the line.
        """
        self.ensure_one()
        product = product or self.product_id
        if not product:
            return self.env['res.company'].browse()
        mixed = self._vuma_mixed_basket_companies()
        quants = self.env['stock.quant'].sudo().search([
            ('product_id', '=', product.id),
            ('quantity', '>', 0),
            ('location_id.usage', 'in', ('internal', 'view')),
        ])
        holders = quants.mapped('company_id')
        return (mixed & holders) | (holders - mixed)

    def _vuma_resolve_source_company(self, product=None, order=None):
        """Pick the company whose stock this line should use.

        Never returns an empty recordset when a product and an order are
        known: the field is the backbone of traceability, so it is filled at
        the data level (create/write), not only by the UI onchange.
        """
        self.ensure_one()
        product = product or self.product_id
        order = order or self.order_id
        if not product:
            return self.env['res.company'].browse()

        candidates = self._vuma_candidate_source_companies(product)
        owner = product.company_id

        # A company-owned product can only ever be sourced from its owner.
        if owner:
            return owner
        # Keep an explicit choice when it is still sellable.
        if self.vuma_source_company_id and (
                self.vuma_source_company_id in candidates
                or not candidates):
            return self.vuma_source_company_id
        # Prefer the company the rep is working in.
        order_company = order.company_id if order else self.env.company
        if order_company and (not candidates or order_company in candidates):
            return order_company
        if candidates:
            return candidates[:1]
        return order_company or self.env.company

    def _vuma_apply_source_company(self):
        """Set the source company + its stock location on these lines.

        Accepts a recordset so a backfill can resolve thousands of lines in
        one call.
        """
        for line in self:
            if not line.product_id:
                line.write({
                    'vuma_source_company_id': False,
                    'vuma_source_location_id': False,
                })
                continue
            company = line._vuma_resolve_source_company()
            vals = {'vuma_source_company_id': company.id}
            location = line._vuma_stock_location(company)
            # Only record a location when that company really holds the
            # product, so the field never implies stock that does not exist.
            if location and line._vuma_location_holds_product(location, company):
                vals['vuma_source_location_id'] = location.id
            else:
                vals['vuma_source_location_id'] = False
            if vals != {
                'vuma_source_company_id': line.vuma_source_company_id.id,
                'vuma_source_location_id': line.vuma_source_location_id.id,
            }:
                line.write(vals)

    def _vuma_location_holds_product(self, location, company):
        self.ensure_one()
        quants = self.env['stock.quant'].sudo().search([
            ('product_id', '=', self.product_id.id),
            ('location_id', 'child_of', location.id),
            ('quantity', '>', 0),
        ], limit=1)
        return bool(quants) or not self.env['stock.quant'].sudo().search_count([
            ('product_id', '=', self.product_id.id),
            ('location_id.company_id', '=', company.id),
            ('quantity', '>', 0),
        ])

    @api.depends('vuma_source_company_id', 'vuma_source_location_id', 'product_id')
    def _compute_vuma_source_available_qty(self):
        Quant = self.env['stock.quant'].sudo()
        for line in self:
            if not line.product_id:
                line.vuma_source_available_qty = 0.0
                continue
            domain = [('product_id', '=', line.product_id.id), ('quantity', '>', 0)]
            if line.vuma_source_location_id:
                domain += [('location_id', 'child_of',
                            line.vuma_source_location_id.id)]
            elif line.vuma_source_company_id:
                domain += [('location_id.company_id', '=',
                            line.vuma_source_company_id.id)]
            else:
                line.vuma_source_available_qty = 0.0
                continue
            quants = Quant.search(domain)
            line.vuma_source_available_qty = sum(quants.mapped('quantity'))

    # ------------------------------------------------------------------
    # Keep the field populated at the data level
    # ------------------------------------------------------------------
    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        # Covers RPC writes, imports and data loads where the UI onchange
        # never runs.
        for line in lines:
            if line.vuma_source_company_id or not line.product_id:
                if not line.vuma_source_location_id and line.product_id:
                    line._vuma_apply_source_company()
            else:
                line._vuma_apply_source_company()
        lines._vuma_check_source_company_scope()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if 'product_id' in vals or 'vuma_source_company_id' in vals or 'order_id' in vals:
            for line in self:
                if line.product_id:
                    line._vuma_apply_source_company()
            if 'vuma_source_company_id' in vals or 'product_id' in vals:
                self._vuma_check_source_company_scope()
        return res

    def _vuma_check_source_company_scope(self):
        """The stock source must be inside the companies the user is working in.

        This is the line-level half of the company switch. Saving a Trade Rite
        order while only Trade Rite is active cannot quietly attach Siyaya DC
        stock; the rep must switch to Siyaya DC (or to both companies for a
        mixed basket) first.
        """
        for line in self:
            if line.vuma_source_company_id:
                line.env.user._vuma_require_company(
                    line.vuma_source_company_id,
                    _('use this company as the stock source on this order line'),
                )

    @api.onchange('product_id')
    def _onchange_product_id_vuma_source_company(self):
        if self.product_id:
            self._vuma_apply_source_company()

    @api.onchange('vuma_source_company_id')
    def _onchange_vuma_source_company_id(self):
        if self.vuma_source_company_id and self.product_id:
            location = self._vuma_stock_location(self.vuma_source_company_id)
            if location and self._vuma_location_holds_product(
                    location, self.vuma_source_company_id):
                self.vuma_source_location_id = location
            else:
                self.vuma_source_location_id = False

    @api.model
    def _domain_product_id(self):
        """Show BOTH companies' products for Mixed Basket users."""
        domain = list(super()._domain_product_id())
        if self._vuma_cross_company_active():
            companies = self._vuma_mixed_basket_companies()
            if companies:
                domain += [
                    '|', ('company_id', 'in', companies.ids),
                    ('company_id', '=', False),
                ]
        return domain

    def _vuma_company_line_vals(self, company, pricelist):
        product = self.product_id.with_company(company)
        price = self.price_unit
        if pricelist and self.product_id:
            try:
                price = pricelist._get_product_price(
                    product, self.product_uom_qty or 1.0)
            except Exception:
                price = self.price_unit
        vals = {
            'product_id': self.product_id.id,
            'name': self.name or product.get_product_multiline_description_sale(),
            'product_uom_qty': self.product_uom_qty,
            'product_uom_id': self.product_uom_id.id,
            'price_unit': price,
            'discount': self.discount,
            'vuma_source_company_id': company.id,
        }
        location = self._vuma_stock_location(company)
        if location:
            vals['vuma_source_location_id'] = location.id
        taxes = [t.id for t in self.tax_ids if t.company_id == company]
        if taxes:
            vals['tax_ids'] = [(6, 0, taxes)]
        return vals

    def _prepare_invoice_line(self, **optional_values):
        res = super()._prepare_invoice_line(**optional_values)
        if self.vuma_source_company_id:
            res['vuma_source_company_id'] = self.vuma_source_company_id.id
            if self.vuma_source_location_id:
                res['vuma_source_location_id'] = self.vuma_source_location_id.id
        return res