# Fly.io Deployment Guide for vuma-retail

## Prerequisites (run once)

```bash
# 1. Add flyctl to PATH permanently
echo 'export PATH="$HOME/.fly/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc

# 2. Authenticate with Fly.io (complete in browser)
flyctl auth login
# Visit the URL shown and approve

# 3. Create GitHub repo and push
# Go to https://github.com/new, create private repo "vuma-retail"
cd /home/kob/Documents/vuma-retail
git remote add origin https://github.com/YOUR_USERNAME/vuma-retail.git
git push -u origin main
```

## Deploy to Fly.io

```bash
cd /home/kob/Documents/vuma-retail

# 1. Launch app (creates fly.toml if needed, provisions PostgreSQL)
flyctl launch --name vuma-retail --region iad --no-deploy

# 2. Create PostgreSQL database
flyctl postgres create --name vuma-retail-db --region iad --vm-size shared-cpu-1x --volume-size 3

# 3. Attach database to app (sets DATABASE_URL secret)
flyctl postgres attach vuma-retail-db --app vuma-retail

# 4. Set required secrets (NEVER commit these!)
flyctl secrets set ADMIN_PASSWORD="$(openssl rand -base64 32)"
flyctl secrets set DB_PASSWORD="$(flyctl postgres connect -a vuma-retail-db -c 'SHOW password' | grep -oP 'password=\K\S+')"
# Or manually: flyctl secrets set DB_PASSWORD="your-db-password"

# 5. Deploy
flyctl deploy

# 6. Check status
flyctl status
flyctl logs
```

## Database Migration (import your local data)

```bash
# 1. Dump local database
PGPASSWORD="$(cat .dbpass)" pg_dump -h localhost -U odoo -d vuma_retail --no-owner --no-privileges --format=custom --file=vuma_retail.dump

# 2. Restore to Fly.io database (run from local machine)
flyctl postgres connect -a vuma-retail-db -c "DROP DATABASE IF EXISTS vuma_retail; CREATE DATABASE vuma_retail;"
pg_restore -h <FLY_DB_HOST> -U postgres -d vuma_retail --no-owner --no-privileges vuma_retail.dump

# Get FLY_DB_HOST from: flyctl postgres attach vuma-retail-db --app vuma-retail (shows connection info)
# Or: flyctl secrets list | grep DATABASE_URL
```

## Copy Filestore (attachments)

```bash
# Create a volume for filestore if needed (already in fly.toml as mount)
# Copy filestore to Fly.io machine:
flyctl ssh console -C "mkdir -p /home/odoo/data/filestore/vuma_retail"
rsync -avz --progress /home/kob/Documents/vuma-retail/data/filestore/vuma_retail/ root@<app-name>.fly.dev:/home/odoo/data/filestore/vuma_retail/
# Or use flyctl ssh sftp
```

## Useful Commands

```bash
# Scale (free tier: 1 shared CPU, 256MB RAM)
flyctl scale count 1
flyctl scale memory 256

# SSH into container
flyctl ssh console

# View logs
flyctl logs -f

# Restart
flyctl apps restart vuma-retail

# Check secrets
flyctl secrets list

# Update secrets
flyctl secrets set KEY=value
```

## Free Tier Limits

- 1 shared CPU, 256MB RAM (or 512MB if available)
- 3GB PostgreSQL storage
- 160GB outbound data/month
- Apps sleep after inactivity (configure `auto_stop_machines = false` in fly.toml to prevent)

## Troubleshooting

**Database connection issues:**
- Check `flyctl postgres attach` output for correct host
- Ensure `DB_HOST` in fly.toml matches internal hostname (`vuma-retail-db.internal`)

**Odoo won't start:**
- Check logs: `flyctl logs`
- Verify secrets: `flyctl secrets list`
- Check database migration ran: `RUN_MIGRATION=true`

**Filestore missing:**
- Ensure volume mount in fly.toml: `source = "vuma_retail_data"`
- Copy filestore via SFTP: `flyctl ssh sftp shell` then `put -r local/path remote/path`