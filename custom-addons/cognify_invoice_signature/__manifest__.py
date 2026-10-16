{
    'name': 'Cognify Invoice Signature',
    'version': '1.0.0',
    'category': 'Accounting/Accounting',
    'summary': 'Let a customer sign an invoice online from a tablet; store the '
               'signature, signer name and timestamp, and print it on the PDF.',
    'description': """
Invoice Online Signature
========================

Core Odoo only puts an online signature on ``sale.order``. This module adds the
same capability to ``account.move`` so a customer can sign an invoice directly.

Workflow
--------
1. Office ticks **Customer signature requested** on the invoice.
2. Odoo emails (or you share) the customer their portal link for that invoice.
3. Customer opens it on a tablet, draws with a finger or stylus, types their
   name and taps **Sign & Accept**.
4. Odoo stores the signature image plus **Signed by** and **Signed on**, and
   clears the request flag so nobody can overwrite it by accident.

The signature is rendered on the printed/emailed invoice PDF under an
"Authorised signature" block.

The signing widget is the stock ``portal.signature_form`` component, so the
customer gets Odoo's own signature pad — no extra assets, no third-party JS.

Notes
-----
* The stored image is a PNG drawing. It evidences that someone signed; it is not
  a cryptographically sealed e-signature.
* Signing is deliberately restricted to posted, unpaid invoices. See
  ``action_request_signature`` for the exact rules and the reasons.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['account', 'portal', 'sale'],
    'data': [
        'views/account_move_views.xml',
        'views/portal_templates.xml',
        'views/report_templates.xml',
    ],
    'installable': True,
    'application': False,
}
