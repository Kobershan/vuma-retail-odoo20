# -*- coding: utf-8 -*-
"""Stock health: what is on hand, what it cost, and what is not moving.

Three questions a shop floor manager actually asks, none of which the stock
menus answer on their own:

* **What is the stock worth?** On hand at cost and at retail, and the margin
  sitting between them.
* **What is dead?** Money tied up in lines nobody buys. A product with no sale
  in the recent past is dead stock whatever the quantity says.
* **What is about to run out?** Stock below what the product is actually
  selling, so the reorder can be raised before the shelf is empty.

Cost is read from ``product_product.standard_price``. Two things about that
field are worth knowing: it lives on the variant rather than the template, and
it is stored as jsonb keyed by company id, so it has to be read with
``->>`` against the company key rather than as a plain column. The template's
copy of the field is computed and not stored, so there is nothing to read
there.
"""
from odoo import fields, models, tools


class StockHealth(models.Model):
    _name = 'cognify.stock.health'
    _description = 'Stock Health, Value and Dead Stock'
    _auto = False
    _log_access = False
    _order = 'value_retail desc'

    product_id = fields.Many2one('product.product', string='Product', readonly=True)
    product_name = fields.Char('Product', readonly=True)
    default_code = fields.Char('Reference', readonly=True)
    categ_id = fields.Many2one('product.category', string='Category', readonly=True)
    uom_id = fields.Many2one('uom.uom', string='Unit', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Currency',
                                          readonly=True)


    qty_on_hand = fields.Float('On Hand', readonly=True, digits=(12, 2))
    qty_reserved = fields.Float('Reserved', readonly=True, digits=(12, 2))
    qty_available = fields.Float('Available', readonly=True, digits=(12, 2),
                                 help='On hand less what is already promised to a '
                                      'customer or a purchase order.')
    unit_cost = fields.Float('Unit Cost', readonly=True, digits=(12, 4))
    list_price = fields.Float('Retail Price', readonly=True, digits=(12, 4))
    value_cost = fields.Monetary('Value at Cost', readonly=True, currency_field='company_currency_id')
    value_retail = fields.Monetary('Value at Retail', readonly=True, currency_field='company_currency_id')
    margin_value = fields.Monetary('Margin in Stock', readonly=True, currency_field='company_currency_id',
                                   help='What the stock on hand would make if it all '
                                        'sold at retail.')
    margin_percent = fields.Float('Margin %', readonly=True, digits=(6, 2),
                                  aggregator='avg',
                                  help='Margin as a share of the retail value of the '
                                       'stock on hand.')

    # Movement
    qty_sold_30 = fields.Float('Sold (30 days)', readonly=True, digits=(12, 2))
    qty_sold_90 = fields.Float('Sold (90 days)', readonly=True, digits=(12, 2))
    qty_sold_365 = fields.Float('Sold (12 months)', readonly=True, digits=(12, 2))
    revenue_365 = fields.Monetary('Revenue (12 months)', readonly=True, currency_field='company_currency_id')
    last_sold = fields.Date('Last Sold', readonly=True)
    never_sold = fields.Boolean('Never Sold', readonly=True,
                                help='The product has never sold at all. Worth '
                                     'knowing apart from a product that sold long '
                                     'ago: the first has no demand, the second may '
                                     'have had some.')
    days_since_sale = fields.Integer('Days Since Last Sale', readonly=True,
                                     help='Days since the last sale. Odoo reads an '
                                          'empty value here as zero, so use Never '
                                          'Sold to tell a line that has never sold '
                                          'apart from one that sold today.')
    weeks_cover = fields.Float('Weeks of Cover', readonly=True, digits=(8, 2),
                               help='Weeks the stock on hand would last at the rate it '
                                    'has been selling over the last 90 days. Reads as '
                                    'zero for a line that is not selling, because the '
                                    'real answer there is infinite.')

    # Judgements
    is_dead = fields.Boolean('Dead Stock', readonly=True,
                             help='Nothing sold in the recent past while stock is still '
                                  'held, so the money is tied up in a line that does '
                                  'not move. A product that has never sold counts too: '
                                  'it has no demand at all, not merely a stale one.')
    is_out = fields.Boolean('Out of Stock', readonly=True)
    is_low = fields.Boolean('Low Stock', readonly=True,
                            help='Less than a month of cover at the current selling rate.')
    is_slow = fields.Boolean('Slow Mover', readonly=True,
                             help='Selling, but too slowly to count as healthy.')
    days_out_of_stock = fields.Integer('Days Out of Stock', readonly=True,
                                       help='How long this product has been unavailable.')
    stock_status = fields.Selection([
        ('dead', 'Dead stock'),
        ('out', 'Out of stock'),
        ('low', 'Low'),
        ('slow', 'Slow moving'),
        ('ok', 'Healthy'),
    ], string='Status', readonly=True)

    # Odoo 20 reads a system parameter with _get, which returns the value and
    # the id it came from. get_param is gone from the model.
    def _setting(self, key, default):
        value = self.env['ir.config_parameter'].sudo()._get(key)[0]
        return default if value is None else value

    def _dead_days(self):
        return int(self._setting('cognify_management_reports.dead_stock_days', 90))

    def _slow_qty(self):
        return float(self._setting('cognify_management_reports.slow_stock_qty', 5))

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_stock_health')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_stock_health AS (
                WITH sold AS (
                    SELECT
                        sol.product_id                                    AS product_id,
                        SUM(sol.product_uom_qty) FILTER (
                            WHERE so.date_order::date >= CURRENT_DATE - 30
                        )                                               AS qty_30,
                        SUM(sol.product_uom_qty) FILTER (
                            WHERE so.date_order::date >= CURRENT_DATE - 90
                        )                                               AS qty_90,
                        SUM(sol.product_uom_qty)                        AS qty_365,
                        SUM(sol.price_subtotal)                          AS revenue_365,
                        MAX(so.date_order::date)                         AS last_sold
                    FROM sale_order_line sol
                    JOIN sale_order so ON so.id = sol.order_id
                    WHERE so.state = 'sale'
                    GROUP BY sol.product_id
                ),
                -- Stock on hand, and what is already promised away from it.
                -- Restricted to internal locations on purpose. Summing every
                -- quant for a product also counts the units already sitting in
                -- a customer's hands, which are not stock: a line that has run
                -- out would still read as having plenty on it and would never
                -- be flagged. The negative quant at the supplier is the other
                -- end of the same problem.
                quant AS (
                    SELECT
                        q.product_id                                     AS product_id,
                        SUM(q.quantity)                                  AS qty_on_hand,
                        SUM(COALESCE(q.reserved_quantity, 0))            AS qty_reserved
                    FROM stock_quant q
                    JOIN stock_location sl ON sl.id = q.location_id
                    WHERE sl.usage = 'internal'
                    GROUP BY q.product_id
                ),
                -- The cost each variant carries for this company. standard_price
                -- is jsonb keyed by company id, so it cannot be compared or
                -- summed as a number until it is pulled out by that key.
                priced AS (
                    SELECT
                        p.id                                           AS product_id,
                        pt.company_id                                  AS company_id,
                        COALESCE(pc.currency_id, 1)                    AS currency_id,
                        COALESCE((p.standard_price ->>
                                  (COALESCE(pt.company_id, 1))::text)::float, 0)
                                                                    AS unit_cost,
                        COALESCE(pt.list_price, 0)                     AS list_price
                    FROM product_product p
                    JOIN product_template pt ON pt.id = p.product_tmpl_id
                    LEFT JOIN res_company pc ON pc.id = pt.company_id
                )
                SELECT
                    p.id                                               AS id,
                    p.id                                               AS product_id,
                    -- product_template.name is translated, so it is jsonb keyed
                    -- by language. to_jsonb() leaves a jsonb column alone and
                    -- turns a plain text one into a quoted string, so the same
                    -- expression reads correctly whichever way the database
                    -- happens to be storing it.
                    COALESCE(to_jsonb(pt.name) ->> 'en_US', pt.name::text) AS product_name,
                    p.default_code                                     AS default_code,
                    pt.categ_id                                        AS categ_id,
                    pt.uom_id                                          AS uom_id,
                    pr.company_id                                      AS company_id,
                    pr.currency_id                                     AS company_currency_id,

                    COALESCE(q.qty_on_hand, 0)                         AS qty_on_hand,
                    COALESCE(q.qty_reserved, 0)                        AS qty_reserved,
                    COALESCE(q.qty_on_hand, 0)
                        - COALESCE(q.qty_reserved, 0)                  AS qty_available,
                    pr.unit_cost                                       AS unit_cost,
                    pr.list_price                                      AS list_price,
                    COALESCE(q.qty_on_hand, 0) * pr.unit_cost          AS value_cost,
                    COALESCE(q.qty_on_hand, 0) * pr.list_price        AS value_retail,
                    COALESCE(q.qty_on_hand, 0)
                        * (pr.list_price - pr.unit_cost)              AS margin_value,
                    CASE
                        WHEN COALESCE(q.qty_on_hand, 0) * pr.list_price > 0
                        THEN (pr.list_price - pr.unit_cost)
                             / pr.list_price * 100
                        ELSE 0
                    END                                               AS margin_percent,

                    COALESCE(s.qty_30, 0)                              AS qty_sold_30,
                    COALESCE(s.qty_90, 0)                              AS qty_sold_90,
                    COALESCE(s.qty_365, 0)                             AS qty_sold_365,
                    COALESCE(s.revenue_365, 0)                         AS revenue_365,
                    s.last_sold                                        AS last_sold,
                    -- The explicit flag, because an empty date reads as zero
                    -- through the ORM and "never sold" would look the same as
                    -- "sold today".
                    s.last_sold IS NULL                                 AS never_sold,

                    -- Blank rather than zero when nothing has ever sold: the
                    -- two say different things to whoever is reading this.
                    CASE
                        WHEN s.last_sold IS NULL THEN NULL
                        ELSE CURRENT_DATE - s.last_sold
                    END                                               AS days_since_sale,
                    -- Weeks of cover at the 90 day rate, blank when the rate is
                    -- zero because there is nothing to divide by.
                    CASE
                        WHEN COALESCE(s.qty_90, 0) <= 0 THEN NULL
                        ELSE COALESCE(q.qty_on_hand, 0) / (s.qty_90 / 13.0)
                    END                                               AS weeks_cover,

                    -- One expression for "not moving" is reused three times
                    -- below. Postgres has no reusable LET, so repeating it is
                    -- clearer than hiding it behind a view.
                    COALESCE(q.qty_on_hand, 0) > 0
                        AND (s.last_sold IS NULL
                             OR s.last_sold < CURRENT_DATE - %s)       AS is_dead,
                    COALESCE(q.qty_on_hand, 0) <= 0                     AS is_out,
                    COALESCE(q.qty_on_hand, 0) > 0
                        AND COALESCE(s.qty_90, 0) > 0
                        AND COALESCE(q.qty_on_hand, 0) / (s.qty_90 / 13.0) < 4
                                                                    AS is_low,
                    COALESCE(q.qty_on_hand, 0) > 0
                        AND COALESCE(s.qty_90, 0) < %s
                        AND NOT (s.last_sold IS NULL
                             OR s.last_sold < CURRENT_DATE - %s)       AS is_slow,
                    CASE
                        WHEN COALESCE(q.qty_on_hand, 0) > 0
                             AND (s.last_sold IS NULL
                                  OR s.last_sold < CURRENT_DATE - %s)
                        THEN 'dead'
                        WHEN COALESCE(q.qty_on_hand, 0) <= 0 THEN 'out'
                        WHEN COALESCE(s.qty_90, 0) > 0
                             AND COALESCE(q.qty_on_hand, 0) / (s.qty_90 / 13.0) < 4
                        THEN 'low'
                        WHEN COALESCE(s.qty_90, 0) < %s THEN 'slow'
                        ELSE 'ok'
                    END                                               AS stock_status,
                    -- How long the shelf has been empty, counting from the last
                    -- sale. A line never sold here has no date to count from.
                    CASE
                        WHEN COALESCE(q.qty_on_hand, 0) > 0 THEN 0
                        WHEN s.last_sold IS NOT NULL
                        THEN CURRENT_DATE - s.last_sold
                        ELSE 0
                    END                                               AS days_out_of_stock
                FROM product_product p
                JOIN product_template pt ON pt.id = p.product_tmpl_id
                JOIN priced          pr ON pr.product_id = p.id
                LEFT JOIN quant       q ON q.product_id = p.id
                LEFT JOIN sold        s ON s.product_id = p.id
                WHERE pt.is_storable
            )
        """, (self._dead_days(), self._slow_qty(), self._dead_days(),
              self._dead_days(), self._slow_qty()))

    def name_get(self):
        return [(row.id, row.product_name or '') for row in self]