# -*- coding: utf-8 -*-
{
    'name': 'Vuma Credit Hold',
    'version': '1.0.0',
    'category': 'Sales/Accounting',
    'summary': 'Overdue customers go on hold: one admin quotation, then no sales '
               'activity until the account is paid and up to date.',
    'description': """
Vuma Credit Hold
================

Turns an overdue customer account into a controlled one.

What it does
------------
* **The account goes on hold automatically.** As soon as a posted customer
  invoice or credit note leaves a positive overdue balance (due date before
  today), the customer is on credit hold. No manual switch to remember and no
  daily list to maintain.
* **Only an administrator may quote once.** While the account is on hold, the
  normal sales user cannot create, edit, send or confirm a quotation, add an
  order line, post a customer invoice/credit note or validate an outgoing
  delivery for that customer.
* **One quotation, not one per attempt.** An administrator may create exactly
  one quotation for the held account. That quotation is recorded on the
  customer as the credit-hold quotation. The allowance stays used even if the
  quotation is later deleted or cancelled, so it cannot be handed out again
  until the customer pays.
* **Paying releases the hold.** When the overdue balance reaches zero (payment
  reconciliation or a posted credit note), the hold disappears automatically
  and the one-quotation allowance is cleared. If the account falls behind
  again later, a new one-quotation allowance starts.
* **Payments stay open.** Any user who can register a payment may still do so;
  the hold blocks new sales activity, not the money that clears it.

The hold is computed across both trading companies, and is applied to the
customer's commercial entity, so contacts and delivery addresses share the
same hold.
""",
    'author': 'Vuma Retail',
    'license': 'LGPL-3',
    'depends': [
        'vuma_multi_tenant',
        'sale_management',
        'account',
        'stock',
    ],
    'data': [
        'views/res_partner_views.xml',
        'views/sale_order_views.xml',
        'views/account_move_views.xml',
        'data/ir_cron_data.xml',
    ],
    'installable': True,
    'application': False,
}
