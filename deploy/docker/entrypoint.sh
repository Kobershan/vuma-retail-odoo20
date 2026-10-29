#!/bin/bash
# Renders the config, then hands off to the official image's entrypoint so that
# Postgres-wait and process supervision behave exactly as Odoo ships them.
set -euo pipefail

TEMPLATE=/etc/odoo/odoo.conf.template
RENDERED=/var/lib/odoo/odoo.conf

# Fail loudly instead of booting an Odoo with a blank master password.
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD is not set}"
: "${DB_HOST:?DB_HOST is not set}"
: "${DB_USER:?DB_USER is not set}"
: "${DB_PASSWORD:?DB_PASSWORD is not set}"
export DB_PORT="${DB_PORT:-5432}"
export DB_NAME="${DB_NAME:-vuma_retail}"
# workers = 0 keeps the threaded server the app was tested on. See the comment
# in odoo.conf.template before raising this.
export WORKERS="${WORKERS:-0}"

mkdir -p /var/lib/odoo/logs

# Substitute with Python rather than envsubst or sed: envsubst is not installed
# in this image (gettext-base is absent), and sed treats & and \ specially in
# the replacement, which would corrupt a generated password.
python3 - "$TEMPLATE" "$RENDERED" <<'PY'
import os, re, sys

template, rendered = sys.argv[1], sys.argv[2]
text = open(template).read()
for key in ('ADMIN_PASSWORD', 'DB_HOST', 'DB_PORT', 'DB_USER',
            'DB_PASSWORD', 'DB_NAME', 'WORKERS'):
    text = text.replace('${%s}' % key, os.environ[key])

# Fail loudly on any remaining placeholder. Odoo would otherwise receive a
# literal "${SOMETHING}" and die deep inside config parsing, which is a
# confusing way to discover a missing substitution above.
leftover = sorted(set(re.findall(r'\$\{[A-Z_]+\}', text)))
if leftover:
    sys.exit('unsubstituted placeholders in %s: %s' % (template, ', '.join(leftover)))

open(rendered, 'w').write(text)
PY

chmod 600 "$RENDERED"

# The stock entrypoint decides whether to append --db_host/--db_user/... by
# grepping the file named in $ODOO_RC. Point it at the rendered config so it
# finds them already present and leaves our values alone.
export ODOO_RC="$RENDERED"

# Anything the caller passes after the service command (e.g.
# `docker compose run odoo odoo -d vuma_retail -u mymodule --stop-after-init`)
# is forwarded to Odoo, so module upgrades work without editing this file.
# "odoo" itself is dropped: the stock entrypoint already inserts it.
if [ "${1:-}" = "odoo" ]; then
    shift
fi
exec /entrypoint.sh odoo -c "$RENDERED" "$@"
