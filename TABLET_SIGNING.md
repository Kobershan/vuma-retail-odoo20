Tablet Signing of Invoices
==========================

Module: `custom-addons/cognify_invoice_signature` — installed and tested.

**Yes: a customer can write on the screen of the invoice, and it saves.** Core
Odoo 20 only had this on quotations, so this module adds it to `account.move`.


What the customer does
----------------------

1. Office ticks **Customer Signature Requested** on the invoice.
2. Customer opens their invoice link on a tablet (or phone).
3. Taps **Sign Invoice**, then draws with a finger or stylus, types their name,
   taps **Sign & Accept**.
4. Saved: signature image, name, and timestamp.

The pad is Odoo's **own** `portal.signature_form` component — no custom JS, no
third-party assets. The page is responsive, so a phone works identically.

    Signing page:  /my/invoices/<id>/sign?access_token=<token>
    Submit:        POST /my/invoices/<id>/sign/submit  (jsonrpc)
    Signed PDF:    /my/invoices/<id>/signature.pdf?access_token=<token>


What the office does
--------------------

On the invoice form:

* **Request Signature** button — sets the flag and hands you the customer link
* **Customer Signature** tab — shows the captured signature, plus a *Signature
  Note* field (e.g. "Please confirm receipt in good order") shown above the pad
* **Revoke Signature** — clears it so the customer can sign again

Also:

* **Accounting > Customer Invoices > Awaiting Signature** — a list of invoices
  out for signature
* Filters **Awaiting Signature** and **Signed** on the invoice search view
* **Signed by** column on the invoice list
* A **stat button** on the invoice showing when a signature is outstanding


Rules — and why
---------------

Signing is blocked unless the invoice is all of these:

| Rule | Reason |
|---|---|
| a customer invoice (`out_invoice`/`out_refund`) | a supplier does not sign your bill |
| **posted** | signing a draft would let a customer "confirm" something that may still change |
| **unpaid** | once money has moved, a signature adds nothing |
| **not already signed** | the first signature stands; a second attempt is rejected and the original signer is preserved |

These are enforced in `_can_be_signed()` and re-checked server-side on submit,
so they cannot be bypassed by calling the endpoint directly.

Once signed, the request flag clears automatically, so nobody can silently
re-request a signature.


Security
--------

Access uses Odoo's own `_document_check_access` with the invoice's
`access_token`, so a signed invoice is exactly as private as the rest of the
portal.

Verified as a **logged-out public user**:

* wrong token -> rejected, signature untouched
* correct token -> signed as that customer

The token is generated on demand via `_portal_ensure_token()`, because
`access_token` is empty on a freshly created invoice and core only fills it in
when a document is shared — without this the link would be broken.


The signature on the PDF
------------------------

A "Customer Signature" block prints below the totals:

    Customer Signature
    [image]
    Signed by Thandiwe Mokoena on 10/13/2026 04:42:25 PM

Confirmed with `pdftotext` on the generated PDF (the text is font-encoded, so a
naive byte search will not find it).


Verified
--------

    cd /home/kob/Documents/vuma-retail

    # 36 checks: fields, rules, guards, security, templates
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_invoice_signing.py

    # live HTTP: create an invoice and print its URLs (COMMITS)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_invoice_signing_http.py

Then, with the server running:

    curl "http://127.0.0.1:8069/my/invoices/<id>/sign?access_token=<token>"

All test scripts roll back except `ops_test_invoice_signing_http.py`, which
commits on purpose so a running server can serve the page.


Still missing: email
--------------------

You still have **no outgoing mail server** (`SELECT count(*) FROM ir_mail_server`
= 0). Without it you cannot email the signing link, nor the signed invoice.
Everything else is ready — set up SMTP in Settings > Technical > Outgoing Mail
Servers and the whole flow is usable.


Limitations
-----------

* The signature is a PNG drawing. It evidences that someone signed; it is not
  cryptographically sealed, so it is not a legally enforceable e-signature.
  For that you need Odoo Sign (Enterprise) or DocuSign.
* No IP or device audit trail beyond the portal's own request logs.
* The tablet needs network access to your Odoo URL.
