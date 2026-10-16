# -*- coding: utf-8 -*-
"""Cash: what came in, what went out, and what is left.

Sales figures say what was invoiced. This says what actually moved through the
bank and the tills, which is the number a business runs on day to day and the
one that catches a problem invoicing will never show: invoices going out with
nobody paying them.

One row per month per account, straight off the general ledger. Balances are
cumulative within the view, so each month shows where the account stood at the
end of it.

Every posting to a bank account carries a counterpart: the customer's
receivable it settles, or the supplier's payable it pays. Counting the
counterpart as well cancels the two out, so the report shows money in exactly
equal to money out, every month, forever. What is left after removing the
receivable and payable sides is the bank's own money, which is what this is
about.
"""
from odoo import fields, models, tools


class CashPosition(models.Model):
    _name = 'cognify.cash.position'
    _description = 'Cash and Bank Position'
    _auto = False
    _log_access = False
    _order = 'month desc, journal_name'

    month = fields.Date('Month', readonly=True)
    journal_id = fields.Many2one('account.journal', string='Account', readonly=True)
    journal_name = fields.Char('Account', readonly=True)
    journal_code = fields.Char('Code', readonly=True)
    journal_type = fields.Selection([
        ('bank', 'Bank'),
        ('cash', 'Cash'),
    ], string='Type', readonly=True)
    currency_id = fields.Many2one('res.currency', string='Currency', readonly=True,
                                   help='The currency the money in this account is held in. '
                                        'A journal without its own currency uses the '
                                        'company currency.')
    company_id = fields.Many2one('res.company', string='Company', readonly=True)

    receipts = fields.Monetary('Money In', readonly=True, currency_field='currency_id',
                               help='Paid in during the month: customer receipts, cash '
                                    'sales and refunds received back.')
    payments = fields.Monetary('Money Out', readonly=True, currency_field='currency_id',
                               help='Paid out during the month: supplier bills, wages '
                                    'and running costs.')
    net = fields.Monetary('Net Movement', readonly=True, currency_field='currency_id',
                          help='Money in less money out for the month.')
    opening = fields.Monetary('Opening Balance', readonly=True, currency_field='currency_id')
    closing = fields.Monetary('Closing Balance', readonly=True, currency_field='currency_id',
                              help='What was in the account at the end of the month.')
    unreconciled = fields.Monetary('Unreconciled', readonly=True, currency_field='currency_id',
                                   help='Money received or paid on this account that '
                                        'has not been matched up to a bill or invoice. '
                                        'A standing balance here means the books are '
                                        'behind the bank.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_cash_position')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_cash_position AS (
                WITH months AS (
                    SELECT (DATE_TRUNC('month', CURRENT_DATE)
                            + make_interval(months => -g.n))::date AS month
                    FROM generate_series(0, 23) AS g(n)
                ),
                -- The bank's own money: everything on the account except the
                -- receivable and payable it is settling. Those two are the
                -- counterparties and would cancel the figure out to nothing.
                -- Note that money pending on its way in sits in Outstanding
                -- Receipts rather than in the bank account itself, which is why
                -- this reads by journal and not by one account id.
                cash_lines AS (
                    SELECT
                        aml.journal_id                                  AS journal_id,
                        DATE_TRUNC('month', aml.date)::date             AS month,
                        aml.balance                                    AS balance,
                        aml.full_reconcile_id                          AS full_reconcile_id
                    FROM account_move_line aml
                    JOIN account_move am ON am.id = aml.move_id
                    JOIN account_journal j ON j.id = aml.journal_id
                    JOIN account_account acc ON acc.id = aml.account_id
                    WHERE j.type IN ('bank', 'cash')
                      AND am.state = 'posted'
                      AND acc.account_type NOT IN (
                            'asset_receivable', 'asset_receivable_treasury',
                            'liability_payable', 'liability_payable_treasury',
                            'income', 'income_other',
                            'expense', 'expense_direct_cost')
                ),
                movement AS (
                    -- Grouped by account and month only. A journal belongs to
                    -- one company, so grouping by company as well would just
                    -- risk splitting a month into two rows that the running
                    -- total below would then double count.
                    SELECT
                        journal_id, month,
                        COALESCE(SUM(balance) FILTER (WHERE balance > 0), 0) AS receipts,
                        -COALESCE(SUM(balance) FILTER (WHERE balance < 0), 0) AS payments,
                        COALESCE(SUM(balance), 0)                             AS net
                    FROM cash_lines
                    GROUP BY 1, 2
                ),
                running AS (
                    SELECT
                        journal_id, month,
                        SUM(net) OVER w AS net_to_date
                    FROM movement
                    WINDOW w AS (PARTITION BY journal_id ORDER BY month
                                 ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
                ),
                -- Everything before the window the running total covers, so the
                -- first month shown opens on a real balance rather than on zero.
                opening AS (
                    SELECT
                        cl.journal_id                                   AS journal_id,
                        COALESCE(SUM(cl.balance), 0)                    AS opening_balance
                    FROM cash_lines cl
                    WHERE cl.month < DATE_TRUNC('month', CURRENT_DATE)
                                        - make_interval(months => 23)
                    GROUP BY 1
                ),
                unreconciled AS (
                    SELECT
                        journal_id, month,
                        COALESCE(SUM(balance) FILTER
                            (WHERE full_reconcile_id IS NULL), 0)   AS unreconciled
                    FROM cash_lines
                    GROUP BY 1, 2
                )
                SELECT
                    -- The month and the account together are what make a row,
                    -- so the pair is the id. A journal id on its own would leave
                    -- every month pointing at the same record and the ORM would
                    -- read one month's figures for all of them.
                    (EXTRACT(YEAR FROM m.month) * 100
                     + EXTRACT(MONTH FROM m.month)::int) * 10000 + j.id AS id,
                    m.month                                           AS month,
                    j.id                                               AS journal_id,
                    -- account_journal.name is a translated column, so it is
                    -- jsonb keyed by language rather than text. Handing the
                    -- jsonb over as it is would show the user a raw
                    -- {"en_US": "Bank"} in the Account column. to_jsonb() is
                    -- what makes this read either way: on a jsonb column it is
                    -- a no-op, and on a plain text one it wraps the text in
                    -- quotes, so the ->> returns NULL and the fallback picks
                    -- the text up. That matters because Odoo only stores a
                    -- field as jsonb once translations have been written for
                    -- it, so the very same view has to cope with both.
                    COALESCE(to_jsonb(j.name) ->> 'en_US', j.name::text) AS journal_name,
                    j.code                                             AS journal_code,
                    j.type                                             AS journal_type,
                    -- A journal with no currency of its own is held in the
                    -- company's currency. Falling back to NULL here is what
                    -- left every Monetary column on this report formatting as
                    -- a bare number, because a Monetary field with no currency
                    -- has nothing to put a symbol on.
                    COALESCE(j.currency_id, co.currency_id)           AS currency_id,
                    j.company_id                                       AS company_id,

                    COALESCE(mv.receipts, 0)                           AS receipts,
                    COALESCE(mv.payments, 0)                           AS payments,
                    COALESCE(mv.net, 0)                                AS net,
                    COALESCE(op.opening_balance, 0)
                        + COALESCE(r.net_to_date, 0)
                        - COALESCE(mv.net, 0)                          AS opening,
                    COALESCE(op.opening_balance, 0)
                        + COALESCE(r.net_to_date, 0)                  AS closing,
                    COALESCE(ur.unreconciled, 0)                       AS unreconciled
                FROM months m
                CROSS JOIN account_journal j
                LEFT JOIN res_company    co ON co.id = j.company_id
                LEFT JOIN movement     mv ON mv.journal_id = j.id AND mv.month = m.month
                LEFT JOIN running      r ON r.journal_id = j.id AND r.month = m.month
                LEFT JOIN opening      op ON op.journal_id = j.id
                LEFT JOIN unreconciled ur ON ur.journal_id = j.id AND ur.month = m.month
                WHERE j.type IN ('bank', 'cash')
            )
        """)

    def name_get(self):
        return [(row.id, f'{row.journal_name} ({row.month:%b %Y})') for row in self]