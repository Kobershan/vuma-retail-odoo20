Testing Email Without a Real SMTP Account
==========================================

Yes. Odoo has no "capture mail" config option in v20, but you do not need a
real mailbox to prove the flow works: point Odoo at a **local SMTP sink** and
it exercises the genuine `smtp` code path — EHLO, AUTH, MAIL, RCPT, DATA —
while the message lands on disk for you to read.

This is how the signed-invoice email flow was verified on `vuma_retail`.


What was proven
---------------

Full chain, no external mail server:

1. Invoice created and posted.
2. Office ticked *Customer Signature Requested*.
3. Customer signed through the portal endpoint, as a **logged-out public
   user**, using the real access token.
4. Office used *Send & Print* to email the invoice.
5. Odoo connected over real SMTP to 127.0.0.1:1025 and delivered.
6. The captured message contained a PDF attachment, and `pdftotext` on it
   showed:

       Customer Signature
       Signed by Sibusiso Ndlovu on 10/13/2026 05:18:01 PM

So: **signed on tablet -> emailed -> the emailed PDF carries the signature.**


How to run it
-------------

    cd /home/kob/Documents/vuma-retail

    # 1. start the sink (background)
    python3 ops_smtp_sink.py 1025 /tmp/opencode/maildrop &

    # 2. point Odoo at it (COMMITS - creates an ir.mail_server)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_setup_smtp_sink.py

    # 3. create an invoice, sign it, email it (COMMITS)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_mail_flow.py

    # 4. read what arrived
    ls -la /tmp/opencode/maildrop/
    cat /tmp/opencode/maildrop/index.txt

    # 5. clean up
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_cleanup_mail_test.py

Everything is a `.eml` file — open it in any mail client, or:

    python3 -c "
    import email,sys
    m = email.message_from_string(open(sys.argv[1]).read())
    for p in m.walk():
        if p.get_filename(): open(p.get_filename(),'wb').write(p.get_payload(decode=True))
    " /tmp/opencode/maildrop/<file>.eml

    pdftotext attachment.pdf - | grep -i "customer signature\|signed by"


Two things the test rig had to get right
----------------------------------------

* **The sink must advertise AUTH.** Odoo 20's `ir.mail_server` only offers
  `login`, `certificate` and `cli` — there is no "no auth" option. A sink that
  does not advertise `250-AUTH` fails with
  *"SMTP AUTH extension not supported by server"*.
* **Sending happens in the mail queue cron**, not in `Send & Print`. The wizard
  only queues. The SMTP connection is made by `mail.mail` queue processing, so
  the mail stays `outgoing` until the cron runs (every 60s in a live server).


WARNING — do not forget this sink exists
----------------------------------------

`ops_setup_smtp_sink.py` created a real `ir.mail_server` record named
**"Local Test Sink"** pointing at `127.0.0.1:1025` with `sequence = 5`.

While the sink is running, **every** email Odoo sends goes to
`/tmp/opencode/maildrop` and **nowhere else**. Nothing reaches real people.

Before pointing Odoo at your real SMTP server, **delete this record**, or your
mail will keep going to the sink. Settings > Technical > Outgoing Mail Servers.


The 242 stuck emails
--------------------

Before the sink existed, 242 emails had failed and sat in `exception` with
*"Connection refused"*. All of them are internal project chatter
(204 `project.task`, 24 `project.project`, 14 without a model) and **none has a
recipient address** — they were never going anywhere.

Once a real SMTP server is configured they will be retried by the cron. Because
they have no recipient they are harmless, but they are noise. Clearing them:

    # review first
    SELECT count(*) FROM mail_mail WHERE state = 'exception';

    # then, from Technical > Email, or:
    UPDATE mail_mail SET state = 'exception', failure_reason = 'cleared: stale internal notifications';

They are internal-only notifications that Odoo also shows in-app, so deleting
them loses nothing.
