# Deploying vuma-retail to Afrihost

Two things travel separately, and it matters which is which:

| What | Where it goes | Why |
|---|---|---|
| **Code** (`custom-addons/`, `odoo/`, `themes/`, docs) | GitHub → cloned on the server | small, diffable, and you want history |
| **Data** (database + filestore) | copied **straight** to the server | live customer records; git would keep every version forever |

The database is never committed. `*.dump`, `*.sql`, `data/` and the credential
files are all in `.gitignore` for that reason.

## 1. Export the data (on this machine)

```bash
./deploy/export-vuma.sh
```

Writes `deploy/export/` containing the dump, the filestore archive, and a
`MANIFEST.txt` with sizes and checksums.

## 2. Copy it to the server

```bash
rsync -avz --progress deploy/export/ kob@<afrihost-host>:/srv/vuma/import/
```

`rsync` rather than `scp` so an interrupted transfer resumes instead of
starting again — 450 MB over a home connection will drop at least once.

## 3. Get the code onto the server

```bash
ssh kob@<afrihost-host>
sudo mkdir -p /srv/vuma && sudo chown "$USER" /srv/vuma
git clone <your-repo-url> /srv/vuma/app
```

## 4. Restore the data (on the server)

```bash
cd /srv/vuma/app
PGPASSWORD='<the db password>' ./deploy/restore-vuma.sh /srv/vuma/import
```

It refuses to touch an existing database unless you pass `--force`, and it
verifies the checksums from the manifest before it starts.

## 5. Configure and start Odoo

`odoo.conf.template` is the starting point — copy it to `odoo.conf` on the
server and fill in `ADMIN_PASSWORD` and `DB_PASSWORD`. Both are gitignored, so
they only ever exist on the machine that needs them.

Point `addons_path` at the checkout:

```
addons_path = /srv/vuma/app/odoo/addons,/srv/vuma/app/custom-addons,/srv/vuma/app/themes
data_dir    = /srv/vuma/data
```

Then install and start the systemd unit (`odoo.service` in this repo is a
working starting point).

## Updating later

```bash
ssh kob@<afrihost-host>
cd /srv/vuma/app
git pull
sudo systemctl restart odoo
```

**Views reload on their own; Python does not.** Odoo imports an addon's Python
when a worker starts, so any change to a `models/*.py` file needs the restart
above before it takes effect. This is the single most common cause of "the fix
is on disk but the browser still shows the old behaviour".

After a restart, confirm it actually came up:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8069/web/login
```

`200` or `303` means healthy. Anything else, read
`data/logs/odoo.log` before assuming the code is wrong.

## Working from the office

Point your browser at the server. Over the internet, put it behind TLS — Odoo
serves plain HTTP, so either terminate TLS in a reverse proxy (nginx) or reach
it over Tailscale, which is already set up on this machine. See the notes in
`FLY_DEPLOY.md` for the `workers` / websocket caveat before adding a proxy.
