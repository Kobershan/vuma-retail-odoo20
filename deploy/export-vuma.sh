#!/usr/bin/env bash
#
# Package the live vuma_retail database and its filestore for Afrihost.
#
# The database holds customer records and the filestore holds every attachment
# they have ever uploaded, so neither belongs in git: git keeps every version of
# every file forever, and a 156 MB dump committed once is 156 MB in the history
# for good, on every clone. This writes both to a staging directory instead,
# ready to copy straight to the server.
#
#   ./deploy/export-vuma.sh [output-dir]        # default: deploy/export
#
# Then copy it over:
#
#   rsync -avz --progress deploy/export/ kob@<afrihost-host>:/srv/vuma/import/
#
# The password is read from .dbpass, which is gitignored.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
DB="${DB:-vuma_retail}"
OUT="${1:-$HERE/export}"

PWFILE="$ROOT/.dbpass"
if [ ! -f "$PWFILE" ]; then
    echo "no $PWFILE, so the database cannot be reached" >&2
    exit 1
fi
export PGPASSWORD="$(cat "$PWFILE")"

mkdir -p "$OUT"

echo "==> dumping $DB"
pg_dump -h localhost -U odoo -d "$DB" \
    --no-owner --no-privileges --format=custom \
    --file="$OUT/$DB.dump"
ls -lh "$OUT/$DB.dump" | awk '{print "    " $5, $9}'

echo "==> archiving the filestore"
FILESTORE="$ROOT/data/filestore/$DB"
if [ ! -d "$FILESTORE" ]; then
    echo "no filestore at $FILESTORE" >&2
    exit 1
fi
tar -czf "$OUT/$DB-filestore.tar.gz" -C "$ROOT/data/filestore" "$DB"
ls -lh "$OUT/$DB-filestore.tar.gz" | awk '{print "    " $5, $9}'

echo "==> recording what was exported"
VERSION="$(cd "$ROOT" && ./venv/bin/python -c \
    'import odoo; print(odoo.release.version)' 2>/dev/null || echo unknown)"
{
    echo "exported : $(date -Iseconds)"
    echo "host     : $(hostname)"
    echo "database : $DB"
    echo "odoo     : $VERSION"
    echo
    echo "sha256:"
    (cd "$OUT" && sha256sum "$DB.dump" "$DB-filestore.tar.gz")
} > "$OUT/MANIFEST.txt"
cat "$OUT/MANIFEST.txt"

echo
echo "staged in $OUT"
echo "copy it over with:"
echo "  rsync -avz --progress $OUT/ kob@<afrihost-host>:/srv/vuma/import/"