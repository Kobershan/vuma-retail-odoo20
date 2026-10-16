# -*- coding: utf-8 -*-
"""Employee expenses and how much they cost the business.

An expense report answers "how much did they claim". What a manager wants is
the ratio: what did they claim against what they brought in. Spend per person,
spend as a share of revenue, and the expense categories that actually cost the
money.

Only approved expenses count. A submitted claim that has not been approved is
a work in progress, not a cost, and counting it would make the month look worse
before anyone has decided to pay it.
"""
from odoo import fields, models, tools


class ExpenseReport(models.Model):
    _name = 'cognify.expense.report'
    _description = 'Employee Expense Spend'
    _auto = False
    _log_access = False
    _order = 'month desc, expense_total desc'

    month = fields.Date('Month', readonly=True)
    employee_id = fields.Many2one('hr.employee', string='Employee', readonly=True)
    employee_name = fields.Char('Employee', readonly=True)
    department_id = fields.Many2one('hr.department', string='Department', readonly=True)
    manager_id = fields.Many2one('hr.employee', string='Manager', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Currency',
                                          readonly=True)


    expense_total = fields.Monetary('Expenses', readonly=True, currency_field='company_currency_id')
    expense_count = fields.Integer('Claims', readonly=True)
    avg_claim = fields.Monetary('Average Claim', readonly=True, currency_field='company_currency_id')
    largest_claim = fields.Monetary('Largest Claim', readonly=True, currency_field='company_currency_id')

    # The expense products this business actually uses, which are the standard
    # travel, meals, accommodation and communication lines.
    travel = fields.Monetary('Travel', readonly=True, currency_field='company_currency_id')
    accommodation = fields.Monetary('Accommodation', readonly=True, currency_field='company_currency_id')
    meals = fields.Monetary('Meals and Entertainment', readonly=True, currency_field='company_currency_id')
    communication = fields.Monetary('Communication', readonly=True, currency_field='company_currency_id')
    gifts = fields.Monetary('Gifts', readonly=True, currency_field='company_currency_id')
    other = fields.Monetary('Other', readonly=True, currency_field='company_currency_id')

    # Share of the month's sales the expenses represent. This is the number that
    # turns a list of claims into a judgement about whether the spending is
    # earning its keep.
    revenue = fields.Monetary('Revenue', readonly=True, currency_field='company_currency_id',
                              help='Sales invoiced in the month, so the expense total '
                                   'can be read as a share of what came in.')
    expense_ratio = fields.Float('% of Revenue', readonly=True, digits=(6, 2),
                                 help='Expenses as a share of the revenue for the month.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_expense_report')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_expense_report AS (
                WITH months AS (
                    SELECT (DATE_TRUNC('month', CURRENT_DATE)
                            + make_interval(months => -g.n))::date AS month
                    FROM generate_series(0, 23) AS g(n)
                ),
                -- hr.employee.department_id and job_id are related to the
                -- employee's current hr_version row in Odoo 20, not stored on
                -- hr_employee, so they come from there.
                staff AS (
                    SELECT e.id AS id, e.name AS name, e.company_id AS company_id,
                           -- hr.employee has no currency column of its own, so
                           -- it comes from the company the person works for.
                           COALESCE(co.currency_id, 1) AS currency_id,
                           e.parent_id AS manager_id, v.department_id AS department_id
                    FROM hr_employee e
                    LEFT JOIN hr_version v ON v.id = e.current_version_id
                    LEFT JOIN res_company co ON co.id = e.company_id
                ),
                -- Names on hr.expense and hr_work_entry_type are translated
                -- jsonb, so the category split matches on the English text and
                -- on the names Odoo ships with. Anything unrecognised lands in
                -- "Other" rather than being dropped.
                claims AS (
                    SELECT
                        DATE_TRUNC('month', h.date)::date      AS month,
                        h.employee_id                         AS employee_id,
                        h.company_id                          AS company_id,
                        COUNT(*)                              AS expense_count,
                        SUM(h.total_amount)                   AS expense_total,
                        AVG(h.total_amount)                   AS avg_claim,
                        MAX(h.total_amount)                   AS largest_claim,
                        SUM(h.total_amount) FILTER (
                            WHERE lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%travel%'
                               OR lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%mileage%'
                               OR lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%fuel%'
                        )                                   AS travel,
                        SUM(h.total_amount) FILTER (
                            WHERE lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%accommodation%'
                               OR lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%hotel%'
                        )                                   AS accommodation,
                        SUM(h.total_amount) FILTER (
                            WHERE lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%meal%'
                               OR lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%entertain%'
                        )                                   AS meals,
                        SUM(h.total_amount) FILTER (
                            WHERE lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%communicat%'
                               OR lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%phone%'
                               OR lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%data%'
                        )                                   AS communication,
                        SUM(h.total_amount) FILTER (
                            WHERE lower(COALESCE(p.name ->> 'en_US', '')) LIKE '%gift%'
                        )                                   AS gifts
                    FROM hr_expense h
                    LEFT JOIN product_product pp ON pp.id = h.product_id
                    LEFT JOIN product_template p ON p.id = pp.product_tmpl_id
                    -- Approved only: a submitted claim is not yet a cost, and
                    -- counting it would make the month look worse before anyone
                    -- has decided to pay it.
                    WHERE h.state = 'approved'
                      AND h.date >= DATE_TRUNC('month', CURRENT_DATE)
                                        - make_interval(months => 23)
                    GROUP BY 1, 2, 3
                ),
                revenue AS (
                    SELECT
                        DATE_TRUNC('month', am.invoice_date)::date AS month,
                        am.company_id                           AS company_id,
                        SUM(am.amount_total)                    AS revenue
                    FROM account_move am
                    WHERE am.move_type = 'out_invoice'
                      AND am.state = 'posted'
                      AND am.invoice_date >= DATE_TRUNC('month', CURRENT_DATE)
                                        - make_interval(months => 23)
                    GROUP BY 1, 2
                )
                SELECT
                    m.month                                     AS month,
                    st.id                                       AS employee_id,
                    -- The month and the employee together are what make a row,
                    -- so the pair has to form the id. Keying on the employee
                    -- alone gives every month the same record, and the ORM then
                    -- reads one month's figures for all of them.
                    (EXTRACT(YEAR FROM m.month) * 100
                     + EXTRACT(MONTH FROM m.month)::int) * 10000 + st.id AS id,
                    st.name                                     AS employee_name,
                    st.department_id                            AS department_id,
                    st.manager_id                               AS manager_id,
                    COALESCE(c.company_id, st.company_id)        AS company_id,
                    st.currency_id                           AS company_currency_id,

                    COALESCE(c.expense_total, 0)                AS expense_total,
                    COALESCE(c.expense_count, 0)                AS expense_count,
                    COALESCE(c.avg_claim, 0)                    AS avg_claim,
                    COALESCE(c.largest_claim, 0)                AS largest_claim,
                    COALESCE(c.travel, 0)                       AS travel,
                    COALESCE(c.accommodation, 0)                AS accommodation,
                    COALESCE(c.meals, 0)                        AS meals,
                    COALESCE(c.communication, 0)                AS communication,
                    COALESCE(c.gifts, 0)                        AS gifts,
                    COALESCE(c.expense_total, 0)
                        - COALESCE(c.travel, 0)
                        - COALESCE(c.accommodation, 0)
                        - COALESCE(c.meals, 0)
                        - COALESCE(c.communication, 0)
                        - COALESCE(c.gifts, 0)                   AS other,

                    COALESCE(rv.revenue, 0)                     AS revenue,
                    CASE
                        WHEN COALESCE(rv.revenue, 0) > 0
                        THEN c.expense_total / rv.revenue * 100
                        ELSE 0
                    END                                         AS expense_ratio
                FROM months m
                JOIN staff st ON st.id IS NOT NULL
                LEFT JOIN claims  c  ON c.employee_id = st.id AND c.month = m.month
                -- Revenue is grouped per company, so it has to be joined per
                -- company too. Matching on the month alone multiplies every
                -- expense row by the number of companies that invoiced that
                -- month, and the repeated rows carry the same id -- which the
                -- ORM reads as one record, so a month of expenses would show
                -- one company's figures instead of the whole month's.
                LEFT JOIN revenue rv ON rv.month = m.month
                    AND rv.company_id = COALESCE(c.company_id, st.company_id)
                WHERE c.employee_id IS NOT NULL
            )
        """)

    def name_get(self):
        return [(row.id, f'{row.employee_name} ({row.month:%b %Y})') for row in self]