# -*- coding: utf-8 -*-
"""Give every company the same debtor credit limit.

Before 1.0.1 the credit limit was Odoo's company-dependent field, so a limit
captured while Trade Rite was active was missing (or different) in Siyaya DC.
Vuma now manages one limit for both companies; this migration consolidates any
existing values so the shared limit starts from a sensible number.

Where companies disagree, the highest non-zero limit wins: the customer was
already allowed that much in at least one company, and lowering the limit as
part of a technical upgrade would silently block orders.
"""
import json


def migrate(cr, version):
    if not version:
        return

    cr.execute("SELECT id FROM res_company")
    company_ids = [row[0] for row in cr.fetchall()]
    if len(company_ids) < 2:
        return

    cr.execute("""
        SELECT id, credit_limit
          FROM res_partner
         WHERE credit_limit IS NOT NULL
           AND credit_limit <> '{}'::jsonb
    """)
    for partner_id, values in cr.fetchall():
        if not isinstance(values, dict):
            continue
        limits = [
            float(value)
            for value in values.values()
            if value not in (None, False)
        ]
        if not limits:
            continue
        shared_limit = max(limits)
        cr.execute("""
            UPDATE res_partner
               SET credit_limit = %s::jsonb
             WHERE id = %s
        """, (
            json.dumps({str(company_id): shared_limit
                        for company_id in company_ids}),
            partner_id,
        ))
