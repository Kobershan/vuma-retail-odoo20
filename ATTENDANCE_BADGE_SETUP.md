Attendance — Badge / Barcode Clock In-Out
=========================================

Status: configured and tested on `vuma_retail`. You can use this today.

No custom module is needed. Odoo 20's `hr_attendance` kiosk mode handles badges
natively. A fingerprint terminal is NOT required.


How it works
------------

    Employee badge  --scan-->  USB barcode scanner  -->  Odoo kiosk page
                                                          |
                                                          +--> hr.attendance record
                                                              check_in / check_out

1. Odoo matches the scanned value against **Badge ID** (`hr.employee.barcode`).
   Badge ID is unique-constrained, so a scan resolves to exactly one employee.
2. First scan of the day = **clock in**. Second scan = **clock out**. It toggles.
3. The record is stamped `in_mode` / `out_mode` = `kiosk`, with device/location
   tracking available if you switch it on.
4. Unknown badge, or a bad kiosk token, is rejected without creating anything.


Kiosk settings (already applied)
--------------------------------

    Location: Settings > Attendance

    Attendance Mode ............ Barcode / RFID and Manual Selection
    Barcode Source ............. Scanner
    Employee PIN Identification . ON
    Kiosk delay ................ 10 seconds

PIN identification is ON **on purpose**: it makes the manual-selection fallback
safe. Without a PIN, anyone could tap a colleague's name and clock in as them.
With it, a lost badge is recoverable (name + PIN) but not exploitable.

Kiosk URL (read-only, treat as a secret):

    http://localhost:8069/hr_attendance/8cb2281d-26ea-490a-bf0e-a893bc5359f1

Anyone opening that URL gets the kiosk. It is not authenticated, so do not post
it publicly. "Generate new URL" rotates it if it leaks.

    Settings > Attendance > Kiosk Mode Address > Generate new URL


Setting up a new employee
-------------------------

1. HR > Employees > New. Fill in the name.
2. Open the **HR Settings** tab on the employee form.
3. Set **Badge ID** to a short unique value. Digits alone are safest:
   `10021`, `10022`, ...
4. Set a **PIN** (4-6 digits) for the manual fallback.
5. Put a photo on the employee — it prints on the badge.
6. Print: the **Print Badge** button on the employee form (top-right > Print Badge).

Badge IDs are unique; Odoo rejects duplicates. Keep them under ~18 characters.

Keep Badge IDs **digits only**. Alphanumerics work, but a pure-numeric ID is
easier to key in manually when a scanner fails.


RFID inside the badge — yes, this works
---------------------------------------

You can embed an RFID tag in the badge and bind it to an employee. Nothing extra
is needed; the kiosk already speaks "Barcode / RFID" because an RFID reader in
HID keyboard-wedge mode just *types* the tag UID as keystrokes, and Odoo treats
that identically to a barcode.

Bind a tag either way:

* **At the kiosk** — open the kiosk URL, tap "Add Employee", hold the badge to
  the reader. Odoo reads the UID and writes it as the Badge ID
  (endpoint `/hr_attendance/set_badge`).
* **In Odoo** — HR > Employees > your employee > **HR Settings** tab > Badge ID,
  and type the UID from the reader.

Verified UID formats, all clock in correctly:

    A1B2C3D4        8 hex chars (EM4100 125kHz)
    a1b2c3d4        lowercase hex
    0000FFFF        leading zeros preserved as text
    04A1B2C3D4      10 hex (Mifare Classic 1K)
    12345678        all digits

Rules Odoo enforces on Badge ID:

    max 18 characters
    printable ASCII only, no spaces, no tabs
    unique across all employees
    empty clears the field

Three field realities that cause most "the badge doesn't work" reports:

1. **Case matters.** Matching is exact and case sensitive. If your reader emits
   `a1b2c3d4` but the Badge ID was saved as `A1B2C3D4`, the scan is silently
   rejected and nothing is created. Bind the tag *from the reader* rather than
   retyping it, so the stored value matches the reader's output exactly.
2. **No stray characters.** A reader appending CR, CRLF or a tab produces a
   value that will not match. Keyboard-wedge readers normally append CR, which
   the kiosk JS trims. Verify on a real unit before rolling out.
3. **Tag UID is fixed at manufacture.** You cannot change a card's UID; the
   Badge ID simply stores whatever the card already has.

Lost badge / re-issue: bind the replacement tag to the same employee. The old
UID is then dead — it matches nobody and creates no record. Verified.

Buyer terms to search for: **"RFID reader USB HID keyboard wedge 125kHz EM4100"**
(roughly USD 20-45). Avoid readers described only as "USB" or "SDK" — those
usually need vendor software on the PC and will not type into a browser. Verify
before buying that it works in **HID keyboard-emulation mode** and that it types
the raw UID.

RFID vs barcode, briefly: RFID is better for a badge you wear (no orientation,
survives a wet lanyard, no printing) but costs more per card and needs a reader
instead of a scanner. Barcode on a printed PVC card is cheaper and prints
straight from Odoo's Print Badge report. Either works identically with this
setup — you are not locked in.


Hardware — what to buy
----------------------

A plain **USB barcode scanner** is all you need. There is no need for a
biometric terminal, and no need for a network device.

Key phrase when buying: **"HID keyboard-wedge USB barcode scanner"**. These
plug into any USB port, need no driver on Linux, and behave like a keyboard.
Expect roughly USD 15-40.

Suitable symbologies: Code 128 (default and best), Code 39, EAN-13, QR.
The badge prints Code 128, so a scanner supporting Code 128 covers it.

The scanner can sit at the office entrance on any PC or a cheap tablet running
a browser pointed at the kiosk URL.


Scanning on a phone instead
---------------------------

Set **Barcode Source** to *Front Camera* or *Back Camera* and employees can scan
with a phone camera at the kiosk URL. Slower than a fixed USB scanner and phones
may block the screen, but it costs nothing.

Note that QR codes are better for phone cameras. The built-in badge report prints
Code 128, which most phone cameras will still read, but if you want guaranteed
phone scanning you would print QR codes instead — that needs a small custom
report change, not yet done.


Tests
-----

    cd /home/kob/Documents/vuma-retail

    # simulate a device punching in and out (15 checks)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_attendance_kiosk.py

    # badge print pipeline (10 checks)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_badge_print.py

    # RFID tag UID formats and matching rules (17 checks)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_test_rfid_tag.py

    # rehearse binding tags to employees, incl. lost-badge re-issue (24 checks)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_bind_rfid_badge.py

    # ...and actually apply it
    RFID_BIND_LIVE=1 ./venv/bin/python odoo/odoo-bin shell -c odoo.conf \
        -d vuma_retail --http-port=8199 --log-level=error < ops_bind_rfid_badge.py

    # re-apply kiosk settings (idempotent)
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --http-port=8199 --log-level=error < ops_setup_kiosk.py

All test scripts roll back and leave no data behind.

Use `--http-port=8199` when a server is already running on 8069, otherwise the
command fails with "Address already in use".


PDF engine — required for badge printing
----------------------------------------

Badges, invoices, quotes and delivery notes all need a PDF engine. This system
had **none**, so every PDF failed with
"Unable to find the pdf engine on this system". Fixed:

  * `wkhtmltopdf` 0.12.6.1 (patched qt) installed to `~/.local/bin`
  * `bin_path = /home/kob/.local/bin` added to `odoo.conf` so Odoo finds it —
    the systemd unit runs with a minimal PATH that excludes `~/.local/bin`
  * `rlPyCairo` installed into the venv (reportlab needs it to render the
    barcode image; without it the badge report dies on the barcode div)

Verify with `ops_test_pdf_engine.py` (10 checks) — it renders a real invoice,
a sale order and a badge.

**Restart Odoo after any of this**, otherwise the running workers keep the old
registry:

    sudo systemctl restart odoo


Known limitations
-----------------

* The kiosk records **whoever presents the badge**, not who was physically
  scanned. A lost badge lets someone else clock in until you notice the
  attendance record. Mitigations: use the photo on the badge, put the kiosk in
  line of sight of a supervisor, review `hr.attendance` daily.
* Badge IDs are visible and copyable. A photo of a badge is as good as the
  badge itself.
* There is no payroll-grade overtime or shift engine in core `hr_attendance`.
  If you later need that, `hr_timesheet` (already installed) covers project
  hours; true shift scheduling would be a further module.
