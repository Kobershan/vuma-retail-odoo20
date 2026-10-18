# Hosting vuma-retail somewhere other than Oracle

Oracle turned the card down. That is almost always the **bank** refusing the
international verification charge, not Oracle — so try this first, it is free:

> Open your banking app → Cards → your card → turn **Online / International /
> e-commerce payments** ON (FNB: *Card Settings → International*; Capitec:
> *Manage Card → Internet payments*; Absa/Nedbank: *Online purchases*). Then
> retry the Oracle signup, or retry the same card. Oracle Always Free needs no
> payment at all once the account verifies, so it stays R0 forever.

If the bank still refuses, use one of the routes below. **Every route here
avoids the card entirely** — that is the point.

---

## What the server must be

| Requirement | Why |
|---|---|
| **Ubuntu 24.04, x86_64, KVM virtualisation, full SSH/root** | the deploy is Docker + docker compose, nothing more |
| **2 GB RAM minimum, 4 GB comfortable** | Postgres 18 + Odoo 20 running with `workers = 0` |
| **40 GB disk or more** | `odoo:20` image is ~3.5 GB, plus Docker layers and the volumes |
| **Public IPv4** | so the client can reach it |

> **Do not buy "LXC" / "container" / "budget" plans.** Docker cannot run inside
> most of them. It must be **KVM** (a real VM). This is the single most common
> wasted purchase.
>
> **Do not buy shared hosting / cPanel plans either** — including cheap
> "Gold 3 GB" style packages (Afrihost's R95 shared plan, and equivalents).
> Those run PHP websites, not application servers: no root, no Docker, no
> long-running daemons, ports served only by their Apache. The "3 GB" is
> **disk**, not RAM. Odoo needs a real VM.

---

## The five that work without a company card

| Provider | Plan that fits | Price | Pay with | Location |
|---|---|---|---|---|
| **Hetzner** ⭐ cheapest reliable | CX22 — 2 vCPU / 4 GB / 40 GB | ~€4.4/mo (~R90) | **PayPal** (Africa: PayPal is the accepted method) | Germany / Finland |
| **HostAfrica** (South African) ⭐ best local value | KVM Linux VPS — **C3**: 2 vCPU / 2 GB / 80 GB NVMe; **C4**: 2 vCPU / 4 GB / 100 GB | **C3 R300/mo · C4 R430/mo** (first month half price; free `.cloud` domain promo to end of 2026) | **SA debit card, credit card, EFT**, PayPal | Johannesburg |
| **Afrihost** (South African) | Self-managed **Cloud Hosting** — Bronze 1 vCPU / 2 GB / 100 GB works; Silver 2 vCPU / 4 GB is comfortable | **R460 / R730 per month** | SA **bank debit order** or local card — no international card at all | Johannesburg / Cape Town |
| **Zappie Host** (South African) | KVM 4 GB / 6 cores / 150 GB — **take KVM, not LXC** | ~$19/mo | **PayPal** + **Bitcoin** | Johannesburg |
| **Contabo** | VPS with 8 GB RAM | ~€5/mo + small setup fee | **PayPal** | EU / US |

**Which to pick**

* Cheapest that is genuinely reliable and Docker-friendly → **Hetzner CX22 + PayPal**.
* Lowest latency for a client sitting in South Africa, cheapest local option →
  **HostAfrica** in Johannesburg (C3 = R300/mo, first month half price, paid by
  SA debit card / EFT / PayPal). **Zappie KVM** is the fallback if you need
  Bitcoin (no bank at all) — but it costs more.
* Want to pay with a **South African bank debit order or a local card**, with a
  local invoice and local support, and the client sitting in SA? → **Afrihost**
  self-managed Cloud, Bronze or Silver. The priciest per GB, but the easiest
  option to pay for from South Africa with no international card involved.
* Do not bother with AWS/Google: no PayPal, at least as fussy about debit cards
  as Oracle, and nothing stays free — since July 2025 AWS's "free" offer is a
  6-month credit trial ($100-200) that closes itself, and Lightsail is only
  free for 3 months ($5-12/mo after that). Google's free tier is 1 GB RAM —
  too small.

**Linking a South African debit card to PayPal works** (card → PayPal → host).
Hetzner rejects virtual cards, so use a real card if you go that way.

**Afrihost specifics:** order *Cloud Hosting → Self-managed* (not Managed —
that starts at R1 600/mo, you do not need it), choose the **Johannesburg**
data centre, and pick an **Ubuntu** image (22.04 or 24.04). Self-managed cloud
gives you full `root` (Afrihost emails the password, and the ClientZone console
works as a fallback), so `ssh root@<IP>` is all you need — there is no `ubuntu`
user like on Oracle. Their two payment methods are a **South African bank
debit order or a local card**, so nothing needs to clear an international
verification. Month-to-month, no lock-in.

**HostAfrica specifics:** choose the **South Africa** location and an **Ubuntu**
image (24.04, or 22.04 is fine). Every plan includes **SSH + root**, KVM
virtualisation, a static IPv4, NVMe storage and unlimited traffic, with a web
console as a fallback — so `ssh root@<IP>` is all you need. **C2** (1 vCPU /
2 GB / 50 GB, R215/mo) also runs this stack; **C3** adds a second CPU core and
30 GB for R85 more, which makes report generation snappier. Pay by **debit or
credit card for instant provisioning**; EFT works too, but EFT payments are
only allocated Mon–Fri 08:00–16:00, so the server waits for the next
allocation window. Prices shown are usually ex-VAT (the cart adds 15%).

---

## On a 2 GB plan, add 2 GB of swap

2 GB of RAM runs this stack fine (it runs with `workers = 0`), but rendering a
batch of PDFs can spike memory for a moment. Swap turns that spike into a short
pause instead of an OOM kill:

```bash
sudo fallocate -l 2G /swapfile && sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
free -h
```

---

## Running the deploy — only three things change

Everything in `deploy/ORACLE.md` still applies. Steps 2, 3, 5, 6 and 7 are
unchanged. These are the only differences:

**1. The SSH user.** Oracle gave you `ubuntu`; these providers give you `root`
(or `debian`). Drop `ubuntu@` if it is `root`:

```bash
ssh root@<IP>
```

**2. The firewall.** There is no Oracle "security list" to fight, and no
pre-installed iptables REJECT. On a normal VPS, UFW is the whole story:

```bash
sudo apt-get update && sudo apt-get install -y ufw
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw --force enable
sudo ufw status
```

The stack only publishes port 80/443 (Caddy). Odoo itself binds to
`127.0.0.1:8069`, so it is never exposed.

**3. Nothing else.** HTTPS is identical — Caddy + sslip.io:

```bash
IP=$(curl -s ifconfig.me); HOST="${IP//./-}.sslip.io"; echo "your URL: https://$HOST"
```

Took HostAfrica's free `.cloud` domain? Even better: create an **A record**
(e.g. `vumaretail.cloud`) pointing at the server's static IPv4, put that name in
the Caddyfile instead of the sslip.io one, and Caddy fetches a real Let's
Encrypt certificate for it automatically.

Docker install, the tarball upload, `./deploy/bundle/start.sh`, the reboot test
and the verification list are all exactly as written in `ORACLE.md`.

---

## Checklist

```bash
# 0. on the desktop (already done -- the tarball is packed)
scp vuma-deploy.tar.gz root@<IP>:/root/

# 1. on the server
curl -fsSL https://get.docker.com | sudo sh
exit; ssh root@<IP>                      # re-login so the docker group applies
docker run --rm odoo:20 sh -c 'wkhtmltopdf --version | head -1'

# 2. firewall
sudo apt-get update && sudo apt-get install -y ufw
sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo ufw allow 443/tcp && sudo ufw --force enable

# 3. deploy
tar xzf vuma-deploy.tar.gz && cd vuma-deploy && ./deploy/bundle/start.sh

# 4. HTTPS (see ORACLE.md Step 4, Option A)
IP=$(curl -s ifconfig.me); HOST="${IP//./-}.sslip.io"
sudo tee /etc/caddy/Caddyfile >/dev/null <<EOF
$HOST {
    reverse_proxy 127.0.0.1:8069
}
EOF
sudo systemctl reload caddy

# 5. prove it: reboot, then the Step 6 verification list from ORACLE.md
```

`deploy/bundle/.env` holds the generated passwords — keep it.
And change the ~19 demo user passwords before the client logs in.
