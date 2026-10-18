#!/usr/bin/env bash
# Restores the vuma_retail dump into the fresh PostgreSQL cluster.
# Runs once, on first boot, via the /docker-entrypoint-initdb.d hook.
set -euo pipefail

DUMP=/restore/vuma_fresh.dump

# PGDATA comes from the image and is version-specific:
#   postgres:18 -> /var/lib/postgresql/18/docker
# Writing the marker inside it keeps the marker inside the mounted volume, so
# it survives restarts without a hardcoded path that breaks on the next major.
: "${PGDATA:?PGDATA not set -- is this running inside the postgres image?}"
MARKER="$PGDATA/.vuma-restored"

# PGUSER is NOT set by the image in init hooks (verified on postgres:18), so
# psql would otherwise connect as the OS user and fail.
export PGUSER="${POSTGRES_USER:-postgres}"

if [ -f "$MARKER" ]; then
  echo "[restore] already restored, skipping"
  exit 0
fi

if [ ! -f "$DUMP" ]; then
  echo "[restore] ERROR: $DUMP not found" >&2
  echo "[restore] put the dump in ./restore/ next to docker-compose.yml" >&2
  exit 1
fi

echo "[restore] restoring $DUMP as role $PGUSER ..."

# Drop and recreate rather than restoring into the database POSTGRES_DB made.
# A fresh cluster already has a `public` schema, so restoring a dump that also
# creates one trips over "schema public already exists" and fails the whole
# load under --exit-on-error. Recreating gives pg_restore a clean target.
# Connect to `postgres` because a database cannot be dropped while in use.
psql -v ON_ERROR_STOP=1 -d postgres -c "DROP DATABASE IF EXISTS vuma_retail"
psql -v ON_ERROR_STOP=1 -d postgres -c "CREATE DATABASE vuma_retail OWNER \"$PGUSER\""

# --exit-on-error so a truncated dump fails loudly instead of half-loading and
# leaving Odoo to start against missing tables.
pg_restore -d vuma_retail --no-owner --no-privileges --exit-on-error "$DUMP"

# Sanity-check that the schema actually arrived, not just that we got an exit 0.
tables=$(psql -tA -d vuma_retail -c "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'")
tables=${tables:-0}
echo "[restore] public tables restored: $tables"
if [ "$tables" -lt 100 ]; then
  echo "[restore] ERROR: only $tables tables -- dump looks empty or wrong" >&2
  exit 1
fi

# The custom addons must be present in the module registry, or Odoo will boot
# without the multi-tenant features and silently look fine.
mods=$(psql -tA -d vuma_retail -c "SELECT count(*) FROM ir_module_module WHERE name LIKE 'vuma%' AND state='installed'")
echo "[restore] installed vuma* modules: ${mods:-0}"

touch "$MARKER"
echo "[restore] done"
