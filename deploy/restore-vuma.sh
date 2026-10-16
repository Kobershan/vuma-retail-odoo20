#!/usr/bin/env bash
#
# Restore the exported vuma_retail database and filestore on the Afrihost box.
# Run this ON THE SERVER, as the user that runs Odoo.
#
#   ./restore-vuma.sh /srv/vuma/import
#
# It will not touch an existing database unless you say so, because the usual
# way to lose a day's work is to restore over a database that was already in
# use. Pass --force to overwrite.
set -euo pipefail

SRC="${1:-/srv/vuma/import}"
shift || true
FORCE=no
[ "${1:-}" = "--force" ] && FORCE=yes

DB="${DB:-vuma_retail}"
DATA_DIR="${DATA_DIR:-/srv/vuma/data}"
PGUSER="${PGUSER:-odoo}"
PGHOST="${PGHOST:-localhost}"
PGPASSWORD="${PGPASSWORD:-}"

if [ -z "$PGPASSWORD" ]; then
    echo "set PGPASSWORD (or DB_PASSWORD) before running this" >&2
    exit 1
fi
export PGPASSWORD

DUMP="$SRC/$DB.dump"
TARBALL="$SRC/$DB-filestore.tar.gz"
for f in "$DUMP" "$TARBALL"; do
    [ -f "$f" ] || { echo "missing $f" >&2; exit 1; }
done

echo "==> verifying the transfer"
if [ -f "$SRC/MANIFEST.txt" ]; then
    cat "$SRC/MANIFEST.txt"
    echo
    (cd "$SRC" && sha256sum -c --ignore-missing <(grep -E "^[0-9a-f]{64}" MANIFEST.txt) \
        || echo "!! checksums differ -- the files may be truncated, stopping" >&2)
fi

EXISTS="$(psql -h "$PGHOST" -U "$PGUSER" -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname = '$DB'")"
if [ -n "$EXISTS" ] && [ "$FORCE" != yes ]; then
    echo "database $DB already exists; re-run with --force to replace it" >&2
    exit 1
fi

if [ -n "$EXISTS" ]; then
    echo "==> dropping the existing $DB"
    psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "DROP DATABASE $DB"
fi

echo "==> creating $DB"
psql -h "$PGHOST" -U "$PGUSER" -d postgres -c "CREATE DATABASE $DB OWNER $PGUSER"

echo "==> restoring"
# --no-owner because the server's role names need not match the source's.
pg_restore -h "$PGHOST" -U "$PGUSER" -d "$DB" \
    --no-owner --no-privileges --exit-on-error "$DUMP"

echo "==> unpacking the filestore"
mkdir -p "$DATA_DIR/filestore"
tar -xzf "$TARBALL" -C "$DATA_DIR/filestore"
chmod -R u+rwX "$DATA_DIR/filestore/$DB"

echo "==> done"
echo "database : $DB"
echo "filestore: $DATA_DIR/filestore/$DB"
echo
echo "restart Odoo, then check http://<host>:8069/web/login"