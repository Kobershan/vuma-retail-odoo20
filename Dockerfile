# Odoo 20 for vuma-retail, built on the official multi-arch Odoo image.
#
# Why not build from python:3.12-slim (what this used to do):
#
#   1. wkhtmltopdf is GONE from Debian 13 "trixie", and python:3.12-slim now
#      points at trixie. `apt-get install wkhtmltopdf` fails with "no
#      installation candidate", so the image could not build at all -- on any
#      architecture. Reports (invoices, picking slips) need it.
#
#   2. libsass has no Linux aarch64 wheel on any published version -- 0.22.0
#      and 0.23.0 ship macOS arm64 wheels only. On Oracle's Ampere (ARM) free
#      tier it therefore compiles from source, which needs g++ and ~10 minutes
#      of emulated CPU. 72 .scss files in custom-addons/themes mean it cannot
#      simply be dropped.
#
# The official odoo:20 image is multi-arch (linux/amd64 + linux/arm64) and
# already ships wkhtmltopdf 0.12.6.1 (patched Qt) and a compiled libsass, so
# this Dockerfile only has to add the Vuma layer on top.
#
# What goes in, and why nothing else does:
#
#   deploy/branding/   the rebrand (release.py product_name, the web app
#                      manifest, ~127 files). Regenerate with
#                      deploy/make_branding_overlay.sh -- see that script for
#                      why the rest of the 1.3GB checkout is deliberately NOT
#                      shipped. It is an older Odoo 20 snapshot, and copying it
#                      in would revert upstream fixes (an iOS 27 barcode fix, a
#                      pypdf 4 API change, ...).
#   custom-addons/     the nine cognify_*/vuma_* modules, all installed in the
#                      database, so they must be present or Odoo boots without
#                      the multi-tenant features.
#
# The stock image already carries all 695 core addons and 36 website themes,
# including theme_graphene, which is the only theme the database has installed.
FROM odoo:20

USER root

# The overlay mirrors the image layout (odoo/... -> /usr/lib/python3/dist-packages/odoo/...)
# so it lands on top of the packaged Odoo. No pip install, no symlink.
COPY --chown=odoo:odoo deploy/branding/ /usr/lib/python3/dist-packages/
COPY --chown=odoo:odoo custom-addons/ /mnt/extra-addons/
COPY --chown=odoo:odoo deploy/docker/odoo.conf.template /etc/odoo/odoo.conf.template
COPY --chown=odoo:odoo deploy/docker/entrypoint.sh /vuma-entrypoint.sh

RUN chmod +x /vuma-entrypoint.sh

USER odoo

EXPOSE 8069

ENTRYPOINT ["/vuma-entrypoint.sh"]
CMD ["odoo"]
