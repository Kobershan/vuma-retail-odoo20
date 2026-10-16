# -*- coding: utf-8 -*-
"""Debtor ageing: who owes us money, and for how long.

A total of what is overdue says there is a problem. What a credit controller
actually needs is how old the debt is, because that is what decides who gets
chased, how hard, and whether it is written off. This view splits each
customer's unpaid balance into the usual buckets.

The split is done on the invoice due date rather than on the date the money
was missed, which is the same thing for an overdue invoice but keeps a
not-yet-due balance in its own bucket instead of counting it as a week late.
"""
from odoo import fields, models, tools


class CustomerAgeing(models.Model):
    _name = 'cognify.customer.ageing'
    _description = 'Customer Debt Ageing'
    _auto = False
    _log_access = False
    _order = 'bucket_90_plus desc, outstanding desc'

    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    partner_name = fields.Char('Customer', readonly=True)
    parent_id = fields.Many2one('res.partner', string='Parent', readonly=True)
    user_id = fields.Many2one('res.users', string='Salesperson', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Currency',
                                          readonly=True)


    # Unpaid money by how overdue it is. "Current" is not yet due, so it is not
    # a problem yet and is kept separate from the buckets a manager chases.
    bucket_current = fields.Monetary('Not Due Yet', readonly=True, currency_field='company_currency_id')
    bucket_30 = fields.Monetary('1 to 30 Days', readonly=True, currency_field='company_currency_id')
    bucket_60 = fields.Monetary('31 to 60 Days', readonly=True, currency_field='company_currency_id')
    bucket_90 = fields.Monetary('61 to 90 Days', readonly=True, currency_field='company_currency_id')
    bucket_90_plus = fields.Monetary('Over 90 Days', readonly=True, currency_field='company_currency_id')

    outstanding = fields.Monetary('Total Outstanding', readonly=True, currency_field='company_currency_id')
    overdue = fields.Monetary('Total Overdue', readonly=True, currency_field='company_currency_id',
                              help='Everything past its due date, across all the buckets.')
    overdue_percent = fields.Float('% Overdue', readonly=True, digits=(6, 2),
                                    aggregator='avg',
                                    help='Share of what they owe that is already late.')
    oldest_due = fields.Date('Oldest Due', readonly=True)
    overdue_days = fields.Integer('Days Overdue', readonly=True,
                                  help='Days past due for the oldest unpaid invoice.')
    invoice_count = fields.Integer('Unpaid Invoices', readonly=True)

    # Ageing bands, so the report can be filtered to just the worst debt rather
    # than only what is past 90 days.
    age_band = fields.Selection([
        ('current', 'Not due'),
        ('1_30', '1 to 30 days'),
        ('31_60', '31 to 60 days'),
        ('61_90', '61 to 90 days'),
        ('90_plus', 'Over 90 days'),
    ], string='Worst Band', readonly=True,
        help='The oldest unpaid invoice decides the band. One late invoice puts '
             'the customer in the band even if most of what they owe is current.')
    at_risk = fields.Boolean('At Risk', readonly=True,
                             help='Over 60 days overdue, or over half of what they owe '
                                  'is late. Either way this is debt worth escalating.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_customer_ageing')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_customer_ageing AS (
                WITH unpaid AS (
                    SELECT
                        am.partner_id                                    AS partner_id,
                        -- The lowest company the customer owes in. The row is a
                        -- consolidated position rather than a per-company one,
                        -- so this names the company the row is filed under; it
                        -- is what the currency lookup below keys on.
                        MIN(am.company_id)                               AS company_id,
                        COUNT(*)                                         AS invoice_count,
                        SUM(am.amount_residual)                         AS outstanding,
                        MIN(am.invoice_date_due)                         AS oldest_due,
                        SUM(am.amount_residual) FILTER (
                            WHERE am.invoice_date_due >= CURRENT_DATE
                        )                                               AS bucket_current,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due
                                  BETWEEN 1 AND 30
                        )                                               AS bucket_30,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due
                                  BETWEEN 31 AND 60
                        )                                               AS bucket_60,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due
                                  BETWEEN 61 AND 90
                        )                                               AS bucket_90,
                        SUM(am.amount_residual) FILTER (
                            WHERE CURRENT_DATE - am.invoice_date_due > 90
                        )                                               AS bucket_90_plus
                    FROM account_move am
                    WHERE am.move_type IN ('out_invoice', 'out_refund')
                      AND am.state = 'posted'
                      AND am.amount_residual != 0
                    -- Grouped by the customer alone, not by customer *and*
                    -- company. The row below is keyed on the partner, so a
                    -- customer who owes money in two companies would otherwise
                    -- produce two rows sharing one id -- and the ORM would
                    -- silently keep only the first, so the report would show
                    -- one company's slice of what they owe as though it were
                    -- the lot. A credit controller wants the customer's whole
                    -- position, which is what summing gives.
                    GROUP BY am.partner_id
                )
                SELECT
                    p.id                                               AS id,
                    p.id                                               AS partner_id,
                    p.name                                             AS partner_name,
                    p.parent_id, p.user_id,
                    COALESCE(u.company_id, p.company_id)               AS company_id,
                    -- The currency a monetary figure on this row is shown in.
                    -- Looked up rather than joined so a partner with no company
                    -- at all still returns a row instead of falling out.
                    (SELECT c.currency_id FROM res_company c
                      WHERE c.id = COALESCE(u.company_id, p.company_id, 1))
                                                                    AS company_currency_id,
                    COALESCE(u.invoice_count, 0)                       AS invoice_count,
                    COALESCE(u.bucket_current, 0)                      AS bucket_current,
                    COALESCE(u.bucket_30, 0)                           AS bucket_30,
                    COALESCE(u.bucket_60, 0)                           AS bucket_60,
                    COALESCE(u.bucket_90, 0)                           AS bucket_90,
                    COALESCE(u.bucket_90_plus, 0)                      AS bucket_90_plus,
                    COALESCE(u.outstanding, 0)                         AS outstanding,
                    COALESCE(u.bucket_30, 0) + COALESCE(u.bucket_60, 0)
                        + COALESCE(u.bucket_90, 0)
                        + COALESCE(u.bucket_90_plus, 0)               AS overdue,
                    CASE
                        WHEN COALESCE(u.outstanding, 0) > 0
                        THEN (COALESCE(u.bucket_30, 0) + COALESCE(u.bucket_60, 0)
                              + COALESCE(u.bucket_90, 0)
                              + COALESCE(u.bucket_90_plus, 0))
                             / u.outstanding * 100
                        ELSE 0
                    END                                               AS overdue_percent,
                    u.oldest_due                                      AS oldest_due,
                    CASE
                        WHEN COALESCE(u.bucket_30, 0) + COALESCE(u.bucket_60, 0)
                             + COALESCE(u.bucket_90, 0)
                             + COALESCE(u.bucket_90_plus, 0) > 0
                             AND u.oldest_due IS NOT NULL
                        THEN GREATEST(0, CURRENT_DATE - u.oldest_due)
                        ELSE 0
                    END                                               AS overdue_days,
                    CASE
                        WHEN COALESCE(u.bucket_90_plus, 0) > 0 THEN '90_plus'
                        WHEN COALESCE(u.bucket_90, 0) > 0 THEN '61_90'
                        WHEN COALESCE(u.bucket_60, 0) > 0 THEN '31_60'
                        WHEN COALESCE(u.bucket_30, 0) > 0 THEN '1_30'
                        ELSE 'current'
                    END                                               AS age_band,
                    -- Either it has been late long enough that it is worth
                    -- escalating, or so much of the balance is late that the
                    -- account is in trouble even if the oldest bill is recent.
                    COALESCE(u.bucket_90, 0) > 0
                        OR COALESCE(u.bucket_90_plus, 0) > 0
                        OR (COALESCE(u.outstanding, 0) > 0
                            AND (COALESCE(u.bucket_30, 0) + COALESCE(u.bucket_60, 0)
                                 + COALESCE(u.bucket_90, 0)
                                 + COALESCE(u.bucket_90_plus, 0))
                                > u.outstanding / 2)                   AS at_risk
                FROM res_partner p
                LEFT JOIN unpaid u ON u.partner_id = p.id
                WHERE u.partner_id IS NOT NULL
            )
        """)

    def name_get(self):
        return [(row.id, row.partner_name or '') for row in self]