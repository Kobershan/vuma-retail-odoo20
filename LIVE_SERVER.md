# Live Server

**When I say "LIVE SERVER", this is the address:**

```
https://102-68-97-93.sslip.io/vuma/
```

- Public IP: `102.68.97.93` (the `102-68-97-93.sslip.io` name just resolves to it)
- Odoo 20 is served behind Caddy under the **`/vuma/` path prefix**
- Deploy/runbook details live in `deploy/README.md` (Afrihost layout:
  code at `/srv/vuma/app`, systemd unit `odoo.service`).

Everything tested against "the live server" means **this host**, not the
local machine served through a Cloudflare tunnel.

## Access

### HTTP / Odoo login

The Odoo web client opens at `/vuma/`, but the JSON-RPC endpoints live at the
**root** path (`/web/...`). Calls to `/vuma/web/...` fail with
"Session expired (invalid CSRF token)" — use `/web/...` instead.

The deployment admin login is the one printed by `deploy/bundle/start.sh`
(local, gitignored). It works against this server.

### SSH

```bash
ssh kob@102.68.97.93      # password auth is enabled; the local key is not authorised
```

As of 2026-11-01 the local `~/.ssh/id_ed25519` is **rejected** for
`kob@` (server offers `publickey,password`). Code changes cannot be deployed
without working SSH credentials (or authorising the key on the host).

**Code deploy** (once SSH works):

```bash
cd /srv/vuma/app
git pull
# upgrade changed addons, e.g.
sudo -u odoo /srv/vuma/venv/bin/python odoo/odoo-bin -c /etc/vuma/odoo.conf \
    -d vuma_retail -u cognify_admin_shell --stop-after-init
sudo systemctl restart odoo
```

Current live code: `cognify_admin_shell` **1.3.6** (the 1.3.7 company-scope
navbar switcher is pushed to git but not yet deployed to this host).

## Database fixes over HTTP (no SSH required)

Admin JSON-RPC works from any machine:

```
POST https://102-68-97-93.sslip.io/web/dataset/call_kw
Content-Type: application/json
```

with the admin session cookie. Log in first at `/web/login` (root URL), then
use `call_kw` calls (`model`, `method`, `args`, `kwargs`).

## Company record rules (fixed 2026-11-01)

**Symptom:** opening *Costing & Pricing Settings* (search `/costing`) showed

> Access Error — Sorry, Administrator (id=2) doesn't have 'read' access to:
> - Inventory Route (stock.route)

**Cause:** `ir.access` restriction rules had lost their shared-record branch.
They read `[('company_id', 'in', company_ids)]` instead of the standard
`[('company_id', 'in', company_ids + [False])]`, so every record with
`company_id = NULL` (like the shared MTO route read by
`stock.models.res_config_settings._compute_replenish_on_order`) became
unreadable — even for admin. 68 rules were affected (stock, purchase, sales,
CRM, projects, HR, MRP, fleet).

**Fix:** `ops_fix_company_rules.py` widens each broken rule to the
both-companies pattern already used elsewhere in this database:

```
['|', '|', (field, '=', False),
      (field, 'parent_of', company_ids),
      (field, 'in', [466, 467])]
```

Run locally with:

```bash
./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --no-http < ops_fix_company_rules.py
VR_APPLY=1 ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
    --no-http < ops_fix_company_rules.py
```

The live database was repaired over JSON-RPC on 2026-11-01 (0 broken rules
remaining). Verify with:

```sql
SELECT count(*) FROM ir_access
WHERE group_id IS NULL AND active
  AND domain ILIKE '%company_ids%'
  AND domain NOT ILIKE '%False%'
  AND domain NOT ILIKE '%466%';
```
