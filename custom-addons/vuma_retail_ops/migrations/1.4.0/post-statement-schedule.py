# -*- coding: utf-8 -*-
"""Give existing customers a first automatic statement date.

Before 1.4.0 one monthly cron emailed every customer in debit.  Statements are
now scheduled per customer.  Existing customers are opted in and given the
first day of next month as their first statement date, so the monthly statement
they already received is not silently lost.  Dates that are already set are
left alone.
"""


def migrate(cr, version):
    if not version:
        return

    cr.execute("""
        UPDATE res_partner
           SET vuma_statement_auto = TRUE
         WHERE customer_rank > 0
           AND vuma_statement_auto IS NULL
    """)
    cr.execute("""
        UPDATE res_partner
           SET vuma_statement_next_date =
               (date_trunc('month', CURRENT_DATE) + interval '1 month')::date
         WHERE customer_rank > 0
           AND vuma_statement_next_date IS NULL
    """)

    # data/ir_sequence.xml is noupdate, so the existing cron is not changed by
    # the XML data file. Move it from the old monthly global run to the new
    # daily per-customer run in place, keeping the same cron record and its
    # history.
    cr.execute("""
        UPDATE ir_act_server AS action
           SET name = action.name || jsonb_build_object(
                   'en_US', 'Vuma: email due debtor statements'),
               code = 'model._cron_send_due_statements()'
          FROM ir_cron AS cron,
               ir_model_data AS data
         WHERE data.model = 'ir.cron'
           AND data.module = 'vuma_retail_ops'
           AND data.name = 'cron_monthly_statements'
           AND cron.id = data.res_id
           AND action.id = cron.ir_actions_server_id
    """)
    cr.execute("""
        UPDATE ir_cron AS cron
           SET cron_name = 'Vuma: email due debtor statements',
               interval_number = 1,
               interval_type = 'days',
               nextcall = CURRENT_TIMESTAMP + interval '1 day'
          FROM ir_model_data AS data
         WHERE data.model = 'ir.cron'
           AND data.module = 'vuma_retail_ops'
           AND data.name = 'cron_monthly_statements'
           AND cron.id = data.res_id
    """)
