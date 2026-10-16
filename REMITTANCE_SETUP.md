Field Remittance - Odoo 20 setup & verification
=============================================

Module: custom-addons/cognify_remittance

Everything below was already done on this database. Re-running any of it is
safe (the setup script is idempotent).

Commands
--------

  cd /home/kob/Documents/vuma-retail

  # 1. Create the travel/cash accounts, map the categories, store the settings
  ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
      --log-level=error < ops_setup_remittance_accounts.py

  # 2. Business-logic tests (51 checks) - rolls back, leaves no data
  ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
      --log-level=error < ops_test_remittance.py

  # 3. View / config tests (23 checks) - rolls back
  ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
      --log-level=error < ops_test_remittance_ui.py

  # 4. Upgrade the module
  ./venv/bin/python odoo/odoo-bin -c odoo.conf -d vuma_retail \
      -u cognify_remittance --stop-after-init

  # 5. Uninstall / reinstall from scratch
  ./venv/bin/python odoo/odoo-bin module uninstall -c odoo.conf -d vuma_retail cognify_remittance
  ./venv/bin/python odoo/odoo-bin module install    -c odoo.conf -d vuma_retail cognify_remittance

  # Run the server
  ./venv/bin/python odoo/odoo-bin -c odoo.conf        # http://127.0.0.1:8069

Note: the odoo-bin subcommand (`module uninstall`) must come BEFORE -c/-d.

Accounts created by the setup script
------------------------------------
  101500  Cash on Hand (Field Reps)     asset_cash   <- Cash Returned Account
  601000  Travel - Food and Catering     expense      <- Food
  601100  Travel - Lodging               expense      <- Lodging
  601200  Travel - Fuel                  expense      <- Fuel
  601300  Travel - Airtime               expense      <- Airtime
  601400  Travel - Toll                  expense      <- Toll
  601500  Travel - Other Expenses        expense      <- Other

Remittance journal: MISC (Miscellaneous Operations)
Config params:     cognify_remittance.remittance_journal_id      = 3
                   cognify_remittance.remittance_cash_account_id = 92

How the workflow behaves
------------------------
Rep      Remittances > My Remittances. One remittance = one trip. Add a line
         per customer under "Cash Collected", plus any expenses under
         "Expenses". Nothing is written to the general ledger at this stage.
Office   Remittances > Remittances to Verify. Verify, then "Credit to Accounts".

Journal entry on credit (example: 700 + 400 + 200 collected, 200 expenses):

    Dr 101500 Cash on Hand (Field Reps)          1100
    Dr 601000 Travel - Food and Catering           50
    Dr 601100 Travel - Lodging                     80
    ...one debit per expense category used...
        Cr 121000 Accounts Receivable (customer A) 700
        Cr 121000 Accounts Receivable (customer B) 400
        Cr 121000 Accounts Receivable (customer C) 200
                                                  ----
                                                  1300  = 1300

Two collections from the same customer are merged into one credit line.
