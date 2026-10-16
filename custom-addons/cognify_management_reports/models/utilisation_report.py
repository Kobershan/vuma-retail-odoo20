# -*- coding: utf-8 -*-
"""Time spent, and how much of it was worth billing.

Timesheets record where the hours went; this turns them into something a
manager can act on:

* **Billable share** - hours charged to billable work against hours spent in
  total. A team that is busy but not billing is losing money quietly.
* **Cost against revenue** - what the hours cost against what was invoiced for
  them. The number that says whether a project is worth finishing.
* **Timesheet coverage** - days with time recorded against days the person was
  expected to work. Missing timesheets are not the same as zero work, and this
  is the measure that catches them.

``account_analytic_line`` is where the hours live. Billable is read two ways
because a business can mark it either way: from the time type on the line
itself, or from a "billable" analytic plan, which is how Odoo tells chargeable
projects from internal ones by default.
"""
from odoo import fields, models, tools


class UtilisationReport(models.Model):
    _name = 'cognify.utilisation.report'
    _description = 'Timesheet Utilisation and Project Cost'
    _auto = False
    _log_access = False
    _order = 'month desc, cost desc'

    month = fields.Date('Month', readonly=True)
    employee_id = fields.Many2one('hr.employee', string='Employee', readonly=True)
    employee_name = fields.Char('Employee', readonly=True)
    department_id = fields.Many2one('hr.department', string='Department', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Currency',
                                          readonly=True)


    hours = fields.Float('Hours', readonly=True, digits=(12, 2),
                         help='Hours recorded on timesheets this month.')
    billable_hours = fields.Float('Billable Hours', readonly=True, digits=(12, 2))
    non_billable_hours = fields.Float('Non-billable Hours', readonly=True, digits=(12, 2))
    billable_percent = fields.Float('Billable %', readonly=True, digits=(6, 2),
                                     aggregator='avg',
                                    help='Share of recorded hours that went to billable '
                                         'work.')
    days_recorded = fields.Integer('Days Recorded', readonly=True)
    expected_days = fields.Float('Working Days', readonly=True, digits=(6, 1),
                                 help='Weekdays in the month, less public holidays. This '
                                      'is a full-time figure: someone on a part week '
                                      'is measured against it and will read low.')
    timesheet_coverage = fields.Float('Timesheet Coverage %', readonly=True, digits=(6, 2),
                                     help='Days with a timesheet against working days. '
                                          'Well below 100% means timesheets are being '
                                          'filled in late or not at all.')

    cost = fields.Monetary('Cost', readonly=True, currency_field='company_currency_id',
                           help='Hours worked at the person\'s own hourly cost.')
    revenue = fields.Monetary('Revenue', readonly=True, currency_field='company_currency_id',
                              help='Invoiced against the time that was booked.')
    margin = fields.Monetary('Margin', readonly=True, currency_field='company_currency_id')
    margin_percent = fields.Float('Margin %', readonly=True, digits=(6, 2), aggregator='avg')
    effective_rate = fields.Monetary('Effective Rate', readonly=True,
                                     currency_field='company_currency_id',
                                     help='Revenue per billable hour. Compare it with '
                                          'the rate on the price list to see whether the '
                                          'work is being billed at the right price.')
    revenue_per_hour = fields.Monetary('Revenue per Hour', readonly=True,
                                       currency_field='company_currency_id',
                                       help='Revenue against every hour recorded, '
                                            'billable or not.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_utilisation_report')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_utilisation_report AS (
                WITH months AS (
                    SELECT (DATE_TRUNC('month', CURRENT_DATE)
                            + make_interval(months => -g.n))::date AS month
                    FROM generate_series(0, 23) AS g(n)
                ),
                staff AS (
                    -- hourly_cost sits on hr.employee in Odoo 20, not on the
                    -- job, so the rate comes from here.
                    SELECT e.id AS id, e.name AS name, e.company_id AS company_id,
                           -- hr.employee has no currency column of its own, so
                           -- it comes from the company the person works for.
                           COALESCE(co.currency_id, 1) AS currency_id,
                           v.department_id AS department_id,
                           COALESCE(e.hourly_cost, 0) AS hourly_cost
                    FROM hr_employee e
                    LEFT JOIN hr_version v ON v.id = e.current_version_id
                    LEFT JOIN res_company co ON co.id = e.company_id
                ),
                -- The plan name is translated and stored as jsonb, so it has to
                -- be pulled out by its language key before it can be matched on.
                billable_plans AS (
                    SELECT ap.id
                    FROM account_analytic_plan ap
                    WHERE lower(COALESCE(ap.name ->> 'en_US', '')) LIKE '%bill%'
                ),
                booked AS (
                    SELECT
                        DATE_TRUNC('month', aal.date)::date  AS month,
                        aal.employee_id                     AS employee_id,
                        SUM(aal.unit_amount)                AS hours,
                        SUM(aal.unit_amount) FILTER (
                            WHERE (aal.billable_type IS NOT NULL
                                   AND aal.billable_type NOT LIKE '%non_billable%')
                               OR aal.account_id IN (SELECT id FROM billable_plans)
                        )                                   AS billable_hours,
                        COUNT(DISTINCT aal.date)            AS days_recorded,
                        -- Cost is worked out from the hours and the person's
                        -- own rate, not read off the line: the amount on a
                        -- timesheet line is only filled in once it has been
                        -- invoiced, and sits at zero until then.
                        SUM(aal.unit_amount * st.hourly_cost) AS cost
                    FROM account_analytic_line aal
                    JOIN staff st ON st.id = aal.employee_id
                    WHERE aal.employee_id IS NOT NULL
                      AND aal.date >= DATE_TRUNC('month', CURRENT_DATE)
                                        - make_interval(months => 23)
                    GROUP BY 1, 2
                ),
                -- What was invoiced for the work, found through the analytic
                -- line the invoice line produced. Grouped by the month it was
                -- billed rather than the month the hours were spent, because
                -- that is when the money arrived.
                billed AS (
                    SELECT
                        DATE_TRUNC('month', am.invoice_date)::date AS month,
                        aal.employee_id                       AS employee_id,
                        SUM(ail.price_subtotal)              AS revenue
                    FROM account_analytic_line aal
                    JOIN account_move_line ail ON ail.id = aal.move_line_id
                    JOIN account_move am ON am.id = ail.move_id
                    WHERE am.move_type = 'out_invoice'
                      AND am.state = 'posted'
                      AND aal.employee_id IS NOT NULL
                      AND am.invoice_date >= DATE_TRUNC('month', CURRENT_DATE)
                                        - make_interval(months => 23)
                    GROUP BY 1, 2
                ),
                -- Weekdays in the month, less public holidays. Walking the
                -- calendar is what makes this right for every month length,
                -- which arithmetic on the month boundaries does not.
                workdays AS (
                    SELECT
                        m.month                                AS month,
                        st.id                                  AS employee_id,
                        COUNT(*) FILTER (
                            WHERE EXTRACT(ISODOW FROM d.day) < 6
                        )                                       AS weekdays,
                        COUNT(*) FILTER (
                            WHERE EXTRACT(ISODOW FROM d.day) < 6
                              AND hh.id IS NOT NULL
                        )                                       AS holidays
                    FROM months m
                    JOIN staff st ON st.id IS NOT NULL
                    CROSS JOIN LATERAL generate_series(
                        DATE_TRUNC('month', m.month)::date,
                        (DATE_TRUNC('month', m.month)
                         + make_interval(months => 1, days => -1))::date,
                        '1 day') AS d(day)
                    -- Public holidays in Odoo 20 are resource.calendar.leaves
                    -- entries, not a table of their own. A row with no calendar
                    -- applies to everybody in the company; one tied to the
                    -- company calendar applies to everyone working that calendar,
                    -- which for a single-company setup is the same set. The
                    -- company is joined first because its calendar is what the
                    -- holiday join tests against.
                    LEFT JOIN res_company co ON co.id = st.company_id
                    LEFT JOIN resource_calendar_leaves hh
                           ON hh.date_from = d.day
                          AND hh.company_id IS NOT DISTINCT FROM st.company_id
                          AND (hh.calendar_id IS NULL
                               OR hh.calendar_id = co.resource_calendar_id)
                    GROUP BY m.month, st.id
                )
                SELECT
                    m.month                                     AS month,
                    -- The month and the employee together are what make a row,
                    -- so the pair is the id. Keying on the employee alone would
                    -- leave every month pointing at the same record and the ORM
                    -- would read one month's figures for all of them.
                    (EXTRACT(YEAR FROM m.month) * 100
                     + EXTRACT(MONTH FROM m.month)::int) * 10000 + st.id AS id,
                    st.id                                       AS employee_id,
                    st.name                                     AS employee_name,
                    st.department_id                            AS department_id,
                    st.company_id                               AS company_id,
                    st.currency_id                              AS company_currency_id,

                    COALESCE(b.hours, 0)                        AS hours,
                    COALESCE(b.billable_hours, 0)               AS billable_hours,
                    COALESCE(b.hours, 0)
                        - COALESCE(b.billable_hours, 0)         AS non_billable_hours,
                    CASE
                        WHEN COALESCE(b.hours, 0) > 0
                        THEN b.billable_hours / b.hours * 100 ELSE 0
                    END                                         AS billable_percent,
                    COALESCE(b.days_recorded, 0)                 AS days_recorded,
                    GREATEST(0, w.weekdays - w.holidays)         AS expected_days,
                    CASE
                        WHEN GREATEST(0, w.weekdays - w.holidays) > 0
                        THEN b.days_recorded
                             / GREATEST(0, w.weekdays - w.holidays) * 100
                        ELSE 0
                    END                                         AS timesheet_coverage,

                    COALESCE(b.cost, 0)                         AS cost,
                    COALESCE(bl.revenue, 0)                     AS revenue,
                    COALESCE(bl.revenue, 0) - COALESCE(b.cost, 0)
                                                                AS margin,
                    CASE
                        WHEN COALESCE(bl.revenue, 0) > 0
                        THEN (bl.revenue - b.cost) / bl.revenue * 100
                        ELSE 0
                    END                                         AS margin_percent,
                    CASE
                        WHEN COALESCE(b.billable_hours, 0) > 0
                        THEN bl.revenue / b.billable_hours
                        ELSE 0
                    END                                         AS effective_rate,
                    CASE
                        WHEN COALESCE(b.hours, 0) > 0
                        THEN bl.revenue / b.hours
                        ELSE 0
                    END                                         AS revenue_per_hour
                FROM months m
                JOIN staff st ON st.id IS NOT NULL
                LEFT JOIN booked   b ON b.employee_id = st.id AND b.month = m.month
                LEFT JOIN billed   bl ON bl.employee_id = st.id AND bl.month = m.month
                LEFT JOIN workdays w ON w.employee_id = st.id AND w.month = m.month
                WHERE b.employee_id IS NOT NULL
            )
        """)

    def name_get(self):
        return [(row.id, f'{row.employee_name} ({row.month:%b %Y})') for row in self]