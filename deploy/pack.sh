#!/usr/bin/env bash
# Bundles everything a server needs into ONE tarball, so deployment is a single
# scp instead of copying a 1.6GB source checkout that the server does not need.
#
# What goes in (~54MB compressed; the filestore dominates and compresses poorly):
#
#   docker-compose.yml, Dockerfile, .dockerignore
#   custom-addons/      the cognify_*/vuma_*/customer_goods_* modules
#   deploy/docker/      config template + container entrypoint
#   deploy/branding/    the rebrand overlay (see make_branding_overlay.sh)
#   deploy/restore.sh   one-shot database restore
#   deploy/bundle/      the database dump, filestore, .env, start.sh
#   deploy/ORACLE.md    the Oracle runbook
#   deploy/HOSTING.md   the same deploy on any other cheap VPS
#
# What deliberately does NOT go in:
#
#   odoo/    1.3GB. It is an older Odoo 20 snapshot; the official odoo:20 image
#            supplies the core, and deploy/branding/ supplies the rebrand.
#   themes/  250MB of pristine stock website themes the image already has.
#   data/    the local working database and filestore.
#
# Usage:  ./deploy/pack.sh [output-name]
set -euo pipefail

cd "$(dirname "$0")/.."

OUT="${1:-vuma-deploy}"
TARBALL="${OUT}.tar.gz"

[ -f docker-compose.yml ] || { echo "ERROR: run from the repo root" >&2; exit 1; }

# Refuse to ship a bundle with no data in it -- that fails confusingly later.
dump=$(ls deploy/bundle/restore/*.dump 2>/dev/null | head -1) || true
if [ -z "$dump" ]; then
    echo "ERROR: no dump in deploy/bundle/restore/. Run ./deploy/bundle.sh first." >&2
    exit 1
fi

if [ ! -s deploy/bundle/.env ]; then
    echo "ERROR: deploy/bundle/.env is missing. Run ./deploy/bundle.sh first." >&2
    exit 1
fi

echo "==> packing (this reads ~240MB; about a minute)"
rm -f "$TARBALL"

tar czf "$TARBALL" \
    --transform 's,^,vuma-deploy/,' \
    --exclude='__pycache__' \
    --exclude='*.pyc' \
    --exclude='*.pyo' \
    --exclude='.DS_Store' \
    docker-compose.yml \
    Dockerfile \
    .dockerignore \
    custom-addons \
    deploy/docker \
    deploy/branding \
    deploy/restore.sh \
    deploy/bundle \
    deploy/ORACLE.md \
    deploy/HOSTING.md

echo
echo "  $TARBALL  $(du -h "$TARBALL" | cut -f1)"
echo
echo "Contains $(tar tzf "$TARBALL" | wc -l) entries, including:"
tar tzf "$TARBALL" | grep -E "vuma-deploy/(docker-compose.yml|Dockerfile)$|restore/.*\.dump$|bundle/.env$" \
    | sed 's/^/    /'
echo
echo "Next:"
echo "    scp $TARBALL ubuntu@YOUR_SERVER_IP:/home/ubuntu/"
echo
echo "Then on the server, after installing docker, follow deploy/ORACLE.md"
echo "(or deploy/HOSTING.md if you are not on Oracle):"
echo "    tar xzf $TARBALL && cd vuma-deploy && ./deploy/bundle/start.sh"
