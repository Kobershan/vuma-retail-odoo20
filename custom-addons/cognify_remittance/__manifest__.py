{
    'name': 'Cognify Field Remittance',
    'version': '2.0.0',
    'category': 'Sales/Field Sales',
    'summary': 'Record cash collected from customers by field reps, log travel expenses, '
               'and credit each customer account once verified at the office.',
    'description': """
Field Sales Remittance
======================

Lets a sales rep record the money collected from customers in the field during a
trip. A rep usually sees several customers in a day, so each collection is its
own line — the remittance covers the whole trip.

The remittance deliberately does **not** touch the books until it has been
checked at the office, so nothing reaches ``account.move`` until an approver
credits it.

Workflow
--------
1. ``draft``     — the rep records one line per customer paid (customer +
                   amount) and any trip expenses (food, toll, lodging, fuel,
                   airtime, other).
2. ``submitted`` — locked for the rep, waiting for the office.
3. ``verified``  — the office has checked the paperwork.
4. ``credited``  — a balanced journal entry credits each customer account.

On crediting, the entry debits the net cash handed back to the office, debits
each expense category, and credits every customer's receivable by what they
paid. Customer balances are held out of the ledger entirely until step 4, so
the general ledger reflects reality only after office verification.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['base', 'mail', 'account', 'sale_management'],
    'data': [
        'security/cognify_remittance_security.xml',
        'security/ir.access.csv',
        'data/ir_sequence.xml',
        'data/cognify_expense_category.xml',
        'views/cognify_expense_category_views.xml',
        'views/res_config_settings_views.xml',
        'views/cognify_remittance_views.xml',
        'views/cognify_remittance_menus.xml',
    ],
    'installable': True,
    'application': True,
}