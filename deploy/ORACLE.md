# Deploying vuma-retail to Oracle Cloud Always Free — step by step

Goal: the dual-company Odoo 20 system (Trade Rite + Siyaya DC) running 24/7 on
Oracle's Always Free tier, reachable over HTTPS, so the client can test it with
your desktop switched off.

Read it top to bottom and run the commands in order. **Step 0 runs on your
desktop; everything after that runs on the server.**

You do only two things: send a file, and open ports. Both are done in the
first ten minutes.

> **Not on Oracle?** If the signup card was declined, or you are using Hetzner,
> HostAfrica, Zappie or any other VPS, read **deploy/HOSTING.md** instead for
> those three differences (SSH user, firewall, nothing else). The rest of this
> runbook applies unchanged.

---

## What gets deployed

| Container   | Image          | Purpose                                         |
|-------------|----------------|-------------------------------------------------|
| `vuma-db`   | `postgres:18`  | Database, restored from `vuma_fresh.dump` once  |
| `vuma-odoo` | built locally  | Odoo 20 + Vuma branding + the custom addons     |

Odoo is built `FROM odoo:20`, the official multi-arch image. It supplies
wkhtmltopdf (patched Qt — needed for invoices and picking slips) and a compiled
libsass, which is why the build takes seconds rather than the nineteen minutes a
hand-built image took, and why it works on Oracle's ARM hardware.

Odoo listens on `127.0.0.1:8069` only. Nothing reaches it directly: Caddy or a
Cloudflare tunnel terminates HTTPS in front. That is deliberate — Odoo's own HTTP
server has no TLS.

---

## Step 0 — On your desktop: build the file to upload

Only needed if the system changed since the last upload. This snapshots the
**live** database, so stop entering data while it runs.

```bash
cd /home/kob/Documents/vuma-retail
export PGPASSWORD='<the db password>'

./deploy/bundle.sh     # fresh dump + filestore + .env      (~10 s)
./deploy/pack.sh       # rebuilds vuma-deploy.tar.gz        (~1 min)
```

Expected result: `vuma-deploy.tar.gz` around 54 MB. It must contain:

```bash
tar tzf vuma-deploy.tar.gz | grep -E 'docker-compose.yml|\.dump$|bundle/\.env$'
```

If any of those three lines is missing, do not continue — the upload would fail
confusingly later.

---

## Step 1 — Create the instance

In the Oracle console: **Compute → Instances → Create instance**.

| Field        | Value                                                          |
|--------------|----------------------------------------------------------------|
| Image        | Canonical Ubuntu **24.04**, aarch64                           |
| Shape        | **VM.Standard.A1.Flex** — 4 OCPU, 24 GB memory                |
| Boot volume  | 100 GB (Always Free allows up to 200 GB of block storage)      |
| Public IPv4  | **Assign a public IPv4 address** (tick it)                     |
| SSH keys     | Generate a key pair and download the private key, or paste your own public key |

> **"Out of host capacity" is normal.** Free ARM capacity is heavily
> oversubscribed and it is not a mistake on your side. Try, in this order:
> another availability domain in the same region (AD-1 / AD-2 / AD-3); the same
> shape with 2 OCPU / 12 GB instead of 4/24; a different region; or simply the
> same request again a few times over an hour. Scripts that retry every few
> minutes until it succeeds are a common workaround.

Note the **public IP address**. The SSH user is `ubuntu` on these images, not
`root`:

```bash
ssh -i /path/to/your-private-key ubuntu@<PUBLIC_IP>
```

---

## Step 2 — Install Docker on the server

```bash
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER
```

Then **log out and back in** — the group change only applies to new sessions.
Confirm both Docker and the ARM base image, which is the one
architecture-specific risk in this deploy (it is Odoo's image, not ours):

```bash
docker run --rm hello-world
docker run --rm odoo:20 sh -c 'echo "python $(python3 -V)"; wkhtmltopdf --version | head -1; python3 -c "import sass; print(\"libsass\", sass.libsass_version)"'
```

Expect Python 3.12, `wkhtmltopdf 0.12.6.1 (with patched qt)`, and a libsass
version. If all three print, the rest of the deploy is mechanical.

---

## Step 3 — Upload and start the stack

On your desktop:

```bash
scp vuma-deploy.tar.gz ubuntu@<PUBLIC_IP>:/home/ubuntu/
```

Back on the server:

```bash
tar xzf vuma-deploy.tar.gz
cd vuma-deploy
./deploy/bundle/start.sh
```

The first run pulls `odoo:20` (~3.5 GB) and restores the database. It is safe to
re-run and it waits on a marker file, so Odoo can never start against a
half-restored database. The filestore is seeded *before* Odoo starts, which is
what prevents missing-attachment errors. At the end it prints the login details.

Confirm it is alive:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8069/web/login   # 200
curl -s http://127.0.0.1:8069/web/login | grep -o '<title>[^<]*</title>'   # Login | Vuma Retail
```

The second command proves the branding layer loaded and not just plain Odoo.

So far it is reachable only from the server itself. Now put HTTPS in front.

---

## Step 4 — Put HTTPS in front

**Pick one option.** A gives a permanent URL (recommended for a client); B needs
no firewall changes but the link changes on every restart.

### Option A — Caddy + sslip.io, a URL that never changes **(recommended)**

This produces a real Let's Encrypt certificate without buying a domain.
[sslip.io](https://sslip.io) resolves any `a-b-c-d.sslip.io` name to the IP
`a.b.c.d`, so your server's own IP *is* the hostname.

It needs inbound ports 80 and 443. That means opening **two** firewalls, and
missing either one is the single most common reason this appears to fail.

**4A.1 — Oracle's cloud firewall.** Console: **Networking → Virtual Cloud
Networks → your VCN → Security Lists → Default Security List → Add Ingress
Rules**. Add two rules, both Source CIDR `0.0.0.0/0`, IP Protocol `TCP`:

| Destination Port | Purpose                              |
|------------------|--------------------------------------|
| `80`             | Let's Encrypt validation + HTTP→HTTPS |
| `443`            | HTTPS                                |

**4A.2 — The instance's own firewall.** Oracle's Ubuntu images ship an iptables
ruleset that rejects everything except SSH. It is independent of the cloud
firewall. Check it:

```bash
sudo iptables -L INPUT -n --line-numbers | tail -5
```

If the last line is `REJECT` or `DROP`, insert the accepts above it:

```bash
sudo iptables -I INPUT 1 -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT 1 -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save || sudo apt-get install -y iptables-persistent
```

**4A.3 — Install Caddy.**

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

**4A.4 — Point it at Odoo.** The hostname is your server's IP with dots turned
into dashes:

```bash
IP=$(curl -s ifconfig.me); HOST="${IP//./-}.sslip.io"; echo "your URL: https://$HOST"

sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$HOST {
    reverse_proxy 127.0.0.1:8069
}
EOF

sudo systemctl reload caddy
```

Caddy obtains the certificate within a minute. Verify **from your desktop**, not
from the server:

```bash
curl -sI https://<THAT-URL>/web/login | head -1     # expect HTTP/2 200
```

Odoo's config sets `proxy_mode = True`, so it generates `https://` links and
reads the real client IP behind the proxy.

> Let's Encrypt rate-limits repeated certificate requests for one hostname. If
> you rebuild the server and keep the same IP, Caddy reuses the certificate in
> `/var/lib/caddy`, which lives on the boot volume and survives a restart.

### Option B — Cloudflare quick tunnel (no firewall changes)

Fastest route: `cloudflared` connects *outward*, so both firewalls are
irrelevant. The trade-off is a random hostname that changes on every restart.

```bash
curl -fsSL -o /tmp/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64
sudo install -m755 /tmp/cloudflared /usr/local/bin/cloudflared

sudo tee /etc/systemd/system/vuma-tunnel.service >/dev/null <<'EOF'
[Unit]
Description=Cloudflare quick tunnel for vuma-retail
After=network-online.target docker.service
Wants=network-online.target

[Service]
User=ubuntu
ExecStart=/usr/local/bin/cloudflared tunnel --no-autoupdate --url http://127.0.0.1:8069
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now vuma-tunnel
sleep 15
sudo journalctl -u vuma-tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
```

> **The caveat that bites.** Because the service restarts itself, any crash or
> reboot silently produces a *new* URL — and you would have handed the client a
> dead link. After any restart, re-read it:
>
> ```bash
> sudo journalctl -u vuma-tunnel | grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' | tail -1
> ```

---

## Step 5 — Survive a reboot

Nothing to configure: both containers are `restart: unless-stopped`, so Docker
brings the stack back by itself. Prove it once rather than hoping:

```bash
sudo reboot
```

Wait a minute, reconnect, then:

```bash
cd ~/vuma-deploy
docker compose --env-file deploy/bundle/.env ps
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8069/web/login
```

With Option A the URL is unchanged. With Option B, re-read it as above.

---

## Step 6 — Verify before you send the link

Open the public URL and check what the client will actually touch:

1. **Login page is branded** — title `Login | Vuma Retail`.
2. **Company switcher** — Trade Rite, Siyaya DC and My Company must all be
   selectable from the top-bar selector.
3. **Mixed basket** — a quote holding products from both Trade Rite and Siyaya
   DC splits on confirm into **two company orders and two picking slips**, one
   per warehouse. Invoice each company order separately from Sales (Odoo 20
   never auto-invoices on confirm), and each yields its own invoice with its own
   company's totals.
4. **A PDF** — open an invoice and choose Print → Invoice. This exercises
   wkhtmltopdf, the component that differs most between a desktop and a
   container.
5. **Cash allocation** — a payment against a customer with several open invoices
   must settle the oldest first.
6. **The shop** — `/shop` should list products with prices and stock badges, and
   `/contactus` must load (not 403).

---

## Step 7 — Housekeeping before the client sees it

1. **Change the demo passwords.** About nineteen test accounts share a weak demo
   password, and the system was briefly exposed through a public tunnel — anyone
   who saw that URL knows it. *Settings → Users → (user) → change password.*
2. **Give the client their own user.** Create one account with exactly the
   companies they need under *Settings → Users → Companies*, rather than handing
   over `admin`.
3. **Company name.** The public website is owned by the company still called
   *My Company*; that name appears in the site footer and header. Rename it
   under *Settings → Companies* if the client should not see it.
4. **Keep `deploy/bundle/.env` private.** `ADMIN_PASSWORD` in it is the database
   manager master password — not a login. The database manager itself is already
   closed (`list_db = False`).
5. **Never open port 8069.** Do not add a security-list rule for it. Only 80/443.

---

## Day-2 operations

All commands run from `~/vuma-deploy`.

**Logs** — Odoo logs to stdout on purpose, so this is the whole story:

```bash
docker compose --env-file deploy/bundle/.env logs -f odoo
docker compose --env-file deploy/bundle/.env logs --tail 100 odoo
```

**Restart Odoo**

```bash
docker compose --env-file deploy/bundle/.env restart odoo
```

**Backup** — these two artifacts plus `.env` are everything:

```bash
docker compose --env-file deploy/bundle/.env exec -T db \
  pg_dump -U odoo -Fc vuma_retail > backup-$(date +%F).dump
docker compose --env-file deploy/bundle/.env cp \
  odoo:/var/lib/odoo/filestore ./filestore-backup
```

**Push newer work from your desktop.** The server holds a *snapshot*; local
changes are not there until you repeat this:

```bash
# desktop
cd /home/kob/Documents/vuma-retail
export PGPASSWORD='<the db password>'
./deploy/bundle.sh && ./deploy/pack.sh
scp vuma-deploy.tar.gz ubuntu@<PUBLIC_IP>:/home/ubuntu/

# server
cd ~/vuma-deploy && docker compose --env-file deploy/bundle/.env down -v
cd ~ && tar xzf vuma-deploy.tar.gz && cd vuma-deploy && ./deploy/bundle/start.sh
```

`down -v` throws the server's database away and restores the new one, so the
server becomes an exact copy of what you tested locally.

**Factory reset** — the safe undo for almost anything:

```bash
cd ~/vuma-deploy
docker compose --env-file deploy/bundle/.env down -v
./deploy/bundle/start.sh
```

---

## Troubleshooting

**`start.sh` cannot find the dump**
`deploy/bundle/restore/vuma_fresh.dump` is missing: the tarball was packed before
`./deploy/bundle.sh` ran. Re-pack on the desktop.

**The build fails pulling `odoo:20`**
A network blip. Re-run `./deploy/bundle/start.sh`; Docker resumes from the layers
it already has.

**Works on `127.0.0.1:8069` but not on the public URL**
It is the firewall — nearly always one of the two layers in 4A.1/4A.2 rather
than Caddy. Check the ingress rule is on the security list of the instance's own
subnet, and re-run `sudo iptables -L INPUT -n`.

**Caddy cannot get a certificate**
Port 80 must be reachable from the internet for the HTTP challenge. Confirm the
ingress rule and that `http://<HOST>` answers from outside. Double-check the
hostname uses the *dashed* IP.

**`Access to unauthorized or invalid companies`**
The logged-in user may not act as that company. *Settings → Users → (user) →
Companies*.

**Login page renders but images are missing**
The filestore did not seed. Compare what the database references with what is
on disk — disk must hold at least as many files:

```bash
docker compose --env-file deploy/bundle/.env exec -T db psql -U odoo -d vuma_retail -tAc \
  "select count(distinct store_fname) from ir_attachment where store_fname <> '';"
docker compose --env-file deploy/bundle/.env exec odoo \
  sh -c 'find /var/lib/odoo/filestore/vuma_retail -type f | wc -l'
```

Disk is normally a little higher: Odoo prunes orphaned files on first boot, so
a small surplus is expected and healthy. A shortfall is not — the seed mount or
`deploy/bundle/filestore` was empty.
