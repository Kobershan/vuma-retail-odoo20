# -*- coding: utf-8 -*-
"""Customer balances: what they owe, what is late, and how much room is left.

Odoo keeps ``res.partner.credit`` as a computed, non-stored field, so it cannot
be grouped or summed in a pivot view. This view materialises the same numbers
(and adds the overdue split and the credit headroom) so the "who owes us the
most" question is a single table.

Only posted customer invoices count. Refunds are included with their own
negative amounts, so a credit note reduces what a customer owes rather than
being ignored.
"""
from odoo import fields, models, tools


class CustomerCredit(models.Model):
    _name = 'cognify.customer.credit'
    _description = 'Customer Balances and Credit'
    _auto = False
    _log_access = False
    _order = 'outstanding desc, partner_name'

    partner_id = fields.Many2one('res.partner', string='Customer', readonly=True)
    partner_name = fields.Char('Customer', readonly=True)
    parent_id = fields.Many2one('res.partner', string='Parent', readonly=True)
    country_id = fields.Many2one('res.country', string='Country', readonly=True)
    user_id = fields.Many2one('res.users', string='Salesperson', readonly=True)
    team_id = fields.Many2one('crm.team', string='Sales Team', readonly=True)
    company_id = fields.Many2one('res.company', string='Company', readonly=True)
    company_currency_id = fields.Many2one('res.currency', string='Currency',
                                          readonly=True)


    invoice_count = fields.Integer('Invoices', readonly=True)
    billed = fields.Monetary('Invoiced', readonly=True, currency_field='company_currency_id',
                             help='Total invoiced to this customer, refunds deducted.')
    paid = fields.Monetary('Paid', readonly=True, currency_field='company_currency_id')
    outstanding = fields.Monetary('Outstanding', readonly=True, currency_field='company_currency_id',
                                  help='Posted and not yet paid.')
    overdue = fields.Monetary('Overdue', readonly=True, currency_field='company_currency_id',
                              help='Outstanding on invoices whose due date has passed.')
    overdue_days = fields.Integer('Days Overdue', readonly=True,
                                  help='Days past due for the oldest unpaid invoice.')
    oldest_due = fields.Date('Oldest Due', readonly=True)

    use_credit_limit = fields.Boolean('Credit Managed', readonly=True)
    credit_limit = fields.Monetary('Credit Limit', readonly=True, currency_field='company_currency_id')
    credit_available = fields.Monetary('Credit Available', readonly=True,
                                       currency_field='company_currency_id',
                                       help='Credit limit less what is outstanding. '
                                            'Blank when the customer is not on a credit limit.')
    over_limit = fields.Boolean('Over Limit', readonly=True,
                                help='Owes more than their credit limit allows.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_customer_credit')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_customer_credit AS (
                WITH invoice_totals AS (
                    SELECT
                        am.partner_id                                AS partner_id,
                        -- The lowest company the customer trades in; the row is
                        -- a consolidated position, not a per-company one.
                        MIN(am.company_id)                              AS company_id,
                        COUNT(*)                                     AS invoice_count,
                        COALESCE(SUM(am.amount_total), 0)            AS billed,
                        COALESCE(SUM(am.amount_total - am.amount_residual), 0) AS paid,
                        COALESCE(SUM(am.amount_residual), 0)         AS outstanding,
                        COALESCE(SUM(am.amount_residual) FILTER (
                            WHERE am.invoice_date_due < CURRENT_DATE), 0) AS overdue,
                        MIN(am.invoice_date_due) FILTER (
                            WHERE am.amount_residual > 0)           AS oldest_due
                    FROM account_move am
                    WHERE am.move_type IN ('out_invoice', 'out_refund')
                      AND am.state = 'posted'
                    -- Grouped by the customer alone, not by customer *and*
                    -- company. The row below is keyed on the partner, so a
                    -- customer who trades in two companies would otherwise
                    -- produce two rows sharing one id, and the ORM would keep
                    -- only the first -- showing one company's slice of their
                    -- balance as though it were the whole thing.
                    GROUP BY am.partner_id
                ),
                order_totals AS (
                    SELECT
                        so.partner_id   AS partner_id,
                        MIN(so.company_id) AS company_id,
                        COUNT(*)        AS order_count
                    FROM sale_order so
                    WHERE so.state NOT IN ('draft', 'cancel')
                    GROUP BY so.partner_id
                ),
                balances AS (
                    SELECT
                        p.id       AS id,
                        p.id       AS partner_id,
                        p.name     AS partner_name,
                        p.parent_id, p.country_id, p.user_id,
                        -- A partner has no team of its own in Odoo 20; it comes
                        -- from the salesperson's team membership.
                        (SELECT tm.crm_team_id FROM crm_team_member tm
                          WHERE tm.user_id = p.user_id ORDER BY tm.id LIMIT 1) AS team_id,
                        COALESCE(i.company_id, p.company_id) AS company_id,
                        COALESCE(co.currency_id, 1)       AS company_currency_id,
                        -- Credit limit and the "use a credit limit" switch are
                        -- company-dependent in Odoo 20: stored as jsonb keyed by
                        -- company id, so they cannot be read as plain columns.
                        -- The limit is shared by both companies. Fall back to
                        -- any stored value when the partner has no invoices and
                        -- no company of their own (the jsonb key would
                        -- otherwise be NULL and the limit would read as zero).
                        COALESCE(
                            (p.credit_limit ->>
                             (COALESCE(i.company_id, p.company_id, %s))::text)::float,
                            (SELECT MAX(value::numeric)
                               FROM jsonb_each_text(p.credit_limit)
                              WHERE value ~ '^-?[0-9.]+$'),
                            0)                                     AS credit_limit,
                        COALESCE(co.account_use_credit_limit, FALSE) AS credit_managed,
                        COALESCE(i.invoice_count, 0)        AS invoice_count,
                        COALESCE(i.billed, 0)               AS billed,
                        COALESCE(i.paid, 0)                 AS paid,
                        COALESCE(i.outstanding, 0)          AS outstanding,
                        COALESCE(i.overdue, 0)              AS overdue,
                        CASE
                            WHEN COALESCE(i.overdue, 0) > 0 AND i.oldest_due IS NOT NULL
                            THEN GREATEST(0, CURRENT_DATE - i.oldest_due)
                            ELSE 0
                        END                                   AS overdue_days,
                        i.oldest_due                          AS oldest_due
                    FROM res_partner p
                    LEFT JOIN invoice_totals i ON i.partner_id = p.id
                    LEFT JOIN order_totals   o ON o.partner_id = p.id
                    LEFT JOIN res_company    co ON co.id = COALESCE(i.company_id, p.company_id)
                    WHERE p.customer_rank > 0 OR i.partner_id IS NOT NULL
                       OR o.partner_id IS NOT NULL
                )
                SELECT
                    b.id                                                AS id,
                    b.partner_id, b.partner_name, b.parent_id,
                    b.country_id, b.user_id, b.team_id, b.company_id,
                    b.company_currency_id,
                    b.invoice_count, b.billed, b.paid, b.outstanding,
                    b.overdue, b.overdue_days, b.oldest_due,
                    -- "On a credit limit" is what Odoo enforces: the company
                    -- has credit limits switched on and the customer has one.
                    (b.credit_managed AND b.credit_limit > 0)        AS use_credit_limit,
                    b.credit_limit,
                    -- Headroom is shown whenever the customer has a limit,
                    -- whether or not the company currently enforces it:
                    -- a manager asking "how much room is left" wants the
                    -- number, not a blank because a setting is off.
                    CASE
                        WHEN b.credit_limit > 0
                        THEN b.credit_limit - b.outstanding
                        ELSE NULL
                    END                                               AS credit_available,
                    CASE
                        WHEN b.credit_limit > 0
                             AND b.outstanding > b.credit_limit
                        THEN TRUE ELSE FALSE
                    END                                               AS over_limit
                FROM balances b
            )
        """, (self.env.company.id,))

    def name_get(self):
        return [(credit.id, credit.partner_name) for credit in self]