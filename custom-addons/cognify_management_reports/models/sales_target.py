# -*- coding: utf-8 -*-
"""Sales targets, and what the team actually achieved against them.

Community Odoo has nowhere to put a target. ``sale.report`` tells you what was
sold, but with no target to measure it against, "did the rep do well" has no
answer, and a chart of sales by rep only shows who is biggest rather than who
did their job.

This adds the missing half: a target per salesperson per month, and a report
that sets it beside the sales that were actually made.

Targets are held per month rather than per year, because that is how they are
actually agreed and revised. A month with no target set reports the actual and
leaves attainment at zero with ``has_target`` off, which is what keeps a rep
without a goal from looking like they failed one: Odoo reads an empty number as
zero, so the two are told apart by the flag and not by the figure.
"""
from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models, tools
from odoo.exceptions import ValidationError


class SalesTarget(models.Model):
    _name = 'cognify.sales.target'
    _description = 'Sales Target'
    _order = 'date desc, user_id'
    _check_company_auto = True

    name = fields.Char('Reference', default='/', required=True)
    date = fields.Date('Month', required=True, default=fields.Date.context_today,
                       help='The first day of the month the target is for.')
    user_id = fields.Many2one('res.users', string='Salesperson', required=True)
    team_id = fields.Many2one('crm.team', string='Sales Team')
    company_id = fields.Many2one('res.company', string='Company', required=True,
                                 default=lambda self: self.env.company)
    # A Monetary field formats against a currency, not against a company, so
    # the target's currency has to be reachable as one.
    company_currency_id = fields.Many2one('res.currency', related='company_id.currency_id',
                                          string='Currency', readonly=True)

    target_revenue = fields.Monetary('Revenue Target', currency_field='company_currency_id',
                                     help='What this person is expected to invoice in '
                                          'the month.')
    target_units = fields.Float('Volume Target', digits=(12, 2),
                                help='Units expected to sell, if there is a volume goal. '
                                     'Zero means no volume target set.')
    target_margin = fields.Monetary('Margin Target', currency_field='company_currency_id',
                                    help='Gross profit expected from the month.')
    note = fields.Text('Notes')

    # Odoo 20 dropped _sql_constraints: the attribute is ignored
    # with a warning, and a UNIQUE declared there never reaches the
    # database. A table object is the supported way to the same
    # guarantee.
    _unique_month_user = models.Constraint(
        'UNIQUE(date, user_id, company_id)',
        'There is already a target for this salesperson and month.',
    )

    @api.constrains('date')
    def _check_month_start(self):
        for target in self:
            if target.date and target.date != target.date.replace(day=1):
                raise ValidationError(_(
                    'The month must be the first day of the month, not %(date)s.',
                    date=target.date))

    @api.model_create_multi
    def create(self, vals_list):
        # The team is worth filling in automatically: a rep belongs to one, and
        # making the user retype it on every monthly target is a good way to end
        # up with targets that do not roll up to a team.
        for vals in vals_list:
            if vals.get('team_id'):
                continue
            member = self.env['crm.team.member'].search(
                [('user_id', '=', vals.get('user_id'))], limit=1)
            if member:
                vals['team_id'] = member.crm_team_id.id
        return super().create(vals_list)

    def action_roll_forward(self):
        """Copy this target onto the month after it.

        Targets are set once and then repeated, so this saves retyping the same
        number twelve times a year. The copy carries no note, because a note
        written about one month is not automatically true of the next.
        """
        self.ensure_one()
        new_date = (self.date + relativedelta(months=1)).replace(day=1)
        if self.search_count([('user_id', '=', self.user_id.id),
                              ('date', '=', new_date),
                              ('company_id', '=', self.company_id.id)]):
            raise ValidationError(_(
                'A target already exists for %(user)s in %(month)s.',
                user=self.user_id.name, month=fields.Date.to_string(new_date)))
        return self.copy({'date': new_date, 'name': f'{new_date:%Y-%m}', 'note': False})

    def action_view_achievement(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Achievement'),
            'res_model': 'cognify.rep.achievement',
            'view_mode': 'pivot,list',
            'domain': [('user_id', '=', self.user_id.id), ('month', '=', self.date)],
            'context': {'create': False},
        }


class RepAchievement(models.Model):
    """Sales per rep per month, against the target for that month."""

    _name = 'cognify.rep.achievement'
    _description = 'Salesperson Achievement Against Target'
    _auto = False
    _log_access = False
    _order = 'month desc, attainment desc'

    month = fields.Date('Month', readonly=True)
    user_id = fields.Many2one('res.users', string='Salesperson', readonly=True)
    user_name = fields.Char('Salesperson', readonly=True)
    team_id = fields.Many2one('crm.team', string='Sales Team', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Currency',
                                          readonly=True)

    # Actual sales, net of discount and before tax.
    revenue = fields.Monetary('Revenue', readonly=True, currency_field='company_currency_id')
    units = fields.Float('Units', readonly=True, digits=(12, 2))
    margin = fields.Monetary('Margin', readonly=True, currency_field='company_currency_id')
    margin_percent = fields.Float('Margin %', readonly=True, digits=(6, 2), aggregator='avg')
    order_count = fields.Integer('Orders', readonly=True)
    customer_count = fields.Integer('Customers', readonly=True)
    avg_order = fields.Monetary('Average Order', readonly=True, currency_field='company_currency_id')

    # Target, from cognify.sales.target.
    target_revenue = fields.Monetary('Revenue Target', readonly=True, currency_field='company_currency_id')
    target_units = fields.Float('Volume Target', readonly=True, digits=(12, 2))
    target_margin = fields.Monetary('Margin Target', readonly=True, currency_field='company_currency_id')

    attainment = fields.Float('Revenue Attainment %', readonly=True, digits=(6, 2),
                              help='Revenue as a share of the target. Reads zero '
                                   'where no target was set, because Odoo reads an '
                                   'empty number as zero and cannot show a blank: '
                                   'read the Target Set column to tell "no goal set" '
                                   'apart from "fell short of the goal".')
    units_attainment = fields.Float('Volume Attainment %', readonly=True, digits=(6, 2))
    has_target = fields.Boolean('Target Set', readonly=True,
                                help='Whether this rep had a target for this month. '
                                     'The figure to read before any attainment '
                                     'percentage: without it, zero means both "no '
                                     'goal was set" and "sold nothing".')
    on_target = fields.Boolean('On Target', readonly=True,
                               help='At or above the revenue target for the month.')
    gap = fields.Monetary('Shortfall', readonly=True, currency_field='company_currency_id',
                          help='Revenue still needed to reach the target. Zero once '
                               'the target is met.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_rep_achievement')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_rep_achievement AS (
                WITH months AS (
                    SELECT (DATE_TRUNC('month', CURRENT_DATE)
                            + make_interval(months => -g.n))::date AS month
                    FROM generate_series(0, 23) AS g(n)
                ),
                -- What was actually sold, taken from the order lines rather
                -- than from sale.report. That is not a shortcut: in Odoo 20
                -- sale.report is no longer a database view, it is computed on
                -- demand from a query Odoo builds at runtime, so there is no
                -- sale_report table here to read. price_subtotal is the net
                -- figure with the discount already off and tax still excluded,
                -- which is what a revenue target is measured against.
                --
                -- Margin is worked out from the variant cost rather than read
                -- from a report, because the cost is the only part of it that
                -- would otherwise have nowhere to come from. Downpayments are
                -- excluded: they are not sales, and counting them would credit
                -- a rep with revenue they have not actually invoiced.
                sold AS (
                    SELECT
                        DATE_TRUNC('month', so.date_order)::date AS month,
                        so.user_id                             AS user_id,
                        so.company_id                          AS company_id,
                        SUM(sol.price_subtotal)                AS revenue,
                        SUM(sol.product_uom_qty)               AS units,
                        SUM(sol.price_subtotal
                            - sol.product_uom_qty
                            * COALESCE((pp.standard_price ->>
                                        (COALESCE(so.company_id, 1))::text)::float, 0)
                        )                                      AS margin,
                        COUNT(DISTINCT so.id)                   AS order_count,
                        COUNT(DISTINCT so.partner_id)           AS customer_count
                    FROM sale_order_line sol
                    JOIN sale_order so ON so.id = sol.order_id
                    JOIN product_product pp ON pp.id = sol.product_id
                    WHERE so.state = 'sale'
                      AND sol.display_type IS NULL
                      AND NOT COALESCE(sol.is_downpayment, FALSE)
                      AND so.date_order >= DATE_TRUNC('month', CURRENT_DATE)
                                        - make_interval(months => 23)
                    GROUP BY 1, 2, 3
                ),
                targets AS (
                    SELECT
                        t.date                AS month,
                        t.user_id             AS user_id,
                        t.company_id          AS company_id,
                        SUM(t.target_revenue) AS target_revenue,
                        SUM(t.target_units)    AS target_units,
                        SUM(t.target_margin)   AS target_margin
                    FROM cognify_sales_target t
                    GROUP BY 1, 2, 3
                ),
                -- Every month/rep pair that has either sales or a target. A rep
                -- with a target and a quiet month still has to appear: that is
                -- exactly the case worth seeing.
                grid AS (
                    SELECT month, user_id, company_id FROM sold
                    UNION
                    SELECT month, user_id, company_id FROM targets
                )
                SELECT
                    -- The pair is the key. Row numbers are used rather than a
                    -- made-up id so the view has no id collisions, which would
                    -- make the ORM read one row's figures for several.
                    ROW_NUMBER() OVER (ORDER BY g.month, g.user_id)  AS id,
                    g.month                                         AS month,
                    g.user_id                                       AS user_id,
                    -- res_users has no name of its own in Odoo 20; it is read
                    -- through the user's partner record.
                    up.name                                         AS user_name,
                    -- A rep's team comes from their membership, not from the
                    -- partner: in Odoo 20 a partner has no team of its own.
                    (SELECT tm.crm_team_id FROM crm_team_member tm
                      WHERE tm.user_id = g.user_id ORDER BY tm.id LIMIT 1)
                                                                    AS team_id,
                    g.company_id                                    AS company_id,
                    gco.currency_id                                 AS company_currency_id,

                    COALESCE(s.revenue, 0)                          AS revenue,
                    COALESCE(s.units, 0)                            AS units,
                    COALESCE(s.margin, 0)                           AS margin,
                    CASE
                        WHEN COALESCE(s.revenue, 0) > 0
                        THEN s.margin / s.revenue * 100 ELSE 0
                    END                                             AS margin_percent,
                    COALESCE(s.order_count, 0)                      AS order_count,
                    COALESCE(s.customer_count, 0)                   AS customer_count,
                    CASE
                        WHEN COALESCE(s.order_count, 0) > 0
                        THEN s.revenue / s.order_count ELSE 0
                    END                                             AS avg_order,

                    COALESCE(t.target_revenue, 0)                  AS target_revenue,
                    COALESCE(t.target_units, 0)                    AS target_units,
                    COALESCE(t.target_margin, 0)                   AS target_margin,
                    -- Zero, not NULL, where there is no target. The obvious
                    -- choice is to leave it out with a CASE and let it read as
                    -- NULL, but Odoo's Float does float(value or 0.0): an
                    -- empty number is indistinguishable from a zero one by the
                    -- time it leaves the ORM, so the view would be claiming a
                    -- blank that nothing downstream could ever see. A sentinel
                    -- such as -1 would survive the read but would then be
                    -- summed into every pivot and every read_group on this
                    -- model, quietly dragging the averages down. has_target is
                    -- the flag that carries the distinction instead.
                    CASE
                        WHEN COALESCE(t.target_revenue, 0) > 0
                        THEN s.revenue / t.target_revenue * 100
                        ELSE 0
                    END                                             AS attainment,
                    CASE
                        WHEN COALESCE(t.target_units, 0) > 0
                        THEN s.units / t.target_units * 100
                        ELSE 0
                    END                                             AS units_attainment,
                    COALESCE(t.target_revenue, 0) > 0              AS has_target,
                    COALESCE(t.target_revenue, 0) > 0
                        AND COALESCE(s.revenue, 0) >= t.target_revenue
                                                                    AS on_target,
                    GREATEST(COALESCE(t.target_revenue, 0)
                             - COALESCE(s.revenue, 0), 0)          AS gap
                FROM grid g
                LEFT JOIN sold    s ON s.month = g.month AND s.user_id = g.user_id
                LEFT JOIN targets t ON t.month = g.month AND t.user_id = g.user_id
                LEFT JOIN res_users u ON u.id = g.user_id
                LEFT JOIN res_company gco ON gco.id = g.company_id
                LEFT JOIN res_partner up ON up.id = u.partner_id
            )
        """)

    def name_get(self):
        return [(row.id, f'{row.user_name} ({row.month:%b %Y})') for row in self]