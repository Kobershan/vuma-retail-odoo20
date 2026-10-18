#!/usr/bin/env bash
# Generates deploy/branding/ -- the Vuma rebrand, and nothing else.
#
# Background
# ----------
# The Vuma source checkout is an OLDER Odoo 20 snapshot than the official
# odoo:20 image. Diffing the two naively gives ~783 changed files, but ~670 of
# those are upstream commits the image has and the checkout does not. Copying
# them into the image would REGRESS real fixes, e.g.:
#
#   web/static/src/core/barcode/barcode_video_scanner.js
#       image has an iOS 27 WebKit workaround the checkout predates
#   odoo/tools/pdf/__init__.py
#       checkout calls page.rotate_clockwise(), which does not exist in the
#       pypdf 4.0.2 the image ships (it was removed in pypdf 4.0)
#   pos_adyen/.../payment_adyen.js
#       image handles 403 as well as 401
#
# So this script keeps ONLY files that are:
#   1. rebranded (their copy mentions "Vuma"), and
#   2. cosmetically different -- every differing line is a Vuma/Odoo naming
#      change, with no functional drift underneath.
#
# 126 of 131 rebranded files pass that test. The five that do not (release.py
# plus four files carrying upstream drift) are listed below: release.py is kept
# explicitly because product_name is the most visible part of the rebrand, and
# its only extra difference is an upstream version suffix.
#
# Usage:
#   docker create --name vuma-stock odoo:20
#   docker run --rm --user root odoo:20 tar -C /usr/lib/python3/dist-packages -cf - odoo \
#     | tar -C /tmp/opencode/stock -xf -
#   docker rm vuma-stock
#   ./deploy/make_branding_overlay.sh
set -euo pipefail

cd "$(dirname "$0")/.."

STOCK="${1:-/tmp/opencode/stock}"
OVERLAY=deploy/branding

# Rebranelled on purpose: product_name/description live here.
ALLOW_DRIFT="odoo/release.py"

if [ ! -d "$STOCK/odoo" ]; then
    echo "ERROR: no stock Odoo at $STOCK/odoo" >&2
    echo "Extract it from the official image first:" >&2
    echo "  docker run --rm --user root odoo:20 tar -C /usr/lib/python3/dist-packages -cf - odoo \\" >&2
    echo "    | tar -C $STOCK -xf -" >&2
    exit 1
fi

rm -rf "$OVERLAY"
mkdir -p "$OVERLAY/odoo"

kept=0
skipped_branding=0   # rebranded but also carrying functional drift
skipped_plain=0      # not rebranded at all -- plain upstream drift

# The image merges the repo's two addons trees into one directory:
#   odoo/odoo/*   -> <pkg>/
#   odoo/addons/* -> <pkg>/addons/
compare_tree() {
    local src_dir="$1" dest_dir="$2" file rel drift
    while IFS= read -r -d '' file; do
        rel="${file#"$src_dir"/}"
        case "$rel" in
            __pycache__/*|*.pyc|*.pyo|*.so) continue ;;
        esac

        local stock_file="$STOCK/$dest_dir/$rel"
        [ -e "$stock_file" ] || continue          # new file: not our concern here
        cmp -s "$src_dir/$rel" "$stock_file" && continue   # identical

        # Not rebranded -> plain upstream drift, leave the image's copy alone.
        if ! grep -q "Vuma" "$src_dir/$rel" 2>/dev/null; then
            skipped_plain=$((skipped_plain + 1))
            continue
        fi

        # Rebranded. Is anything other than naming different?
        # `diff` exits 1 when files differ, and `grep -v` exits 1 when it
        # filters everything out -- both of which pipefail would turn into a
        # fatal error under set -e. awk in a single pass cannot fail that way.
        local raw_diff
        raw_diff=$(diff "$stock_file" "$src_dir/$rel" 2>/dev/null || true)
        drift=$(printf '%s\n' "$raw_diff" \
                | awk '/^[<>]/ && tolower($0) !~ /vuma|odoo/ {n++} END {print n+0}')

        if [ "$drift" -ne 0 ] && [ "$dest_dir/$rel" != "$ALLOW_DRIFT" ]; then
            echo "  skipping $dest_dir/$rel ($drift non-branding lines -- keeping image version)"
            skipped_branding=$((skipped_branding + 1))
            continue
        fi

        install -D "$src_dir/$rel" "$OVERLAY/$dest_dir/$rel"
        kept=$((kept + 1))
    done < <(find "$src_dir" -type f -print0)
}

echo "==> scanning odoo/odoo"
compare_tree odoo/odoo odoo
echo "==> scanning odoo/addons"
compare_tree odoo/addons odoo/addons

# Binary brand assets (icons/logos). They cannot be text-diffed for "Vuma"
# mentions, so they are kept explicitly: otherwise the stock Odoo iconography
# survives in the image (backend browser-tab favicon, PWA icons, logo
# fallbacks). Keep this list in sync with odoo/addons/web/static/img/.
for rel in \
    web/static/img/favicon.ico \
    web/static/img/logo.png \
    web/static/img/logo2.png \
    web/static/img/logo_inverse_white_206px.png \
    web/static/img/odoo-icon-192x192.png \
    web/static/img/odoo-icon-512x512.png \
    web/static/img/odoo-icon-ios.png \
    web/static/img/odoo_logo.svg \
    web/static/img/odoo_logo_dark.svg \
; do
    src="odoo/addons/$rel"
    if [ -f "$src" ]; then
        install -D "$src" "$OVERLAY/odoo/addons/$rel"
        kept=$((kept + 1))
    fi
done

# /vuma backend-path patches: the whole webclient was moved from /odoo to
# /vuma. These files change URL paths rather than Vuma/Odoo naming, so the
# drift test above would drop them; they are kept explicitly instead. Every
# one of them was byte-identical to the image before the /vuma patch, so no
# upstream fix is lost.
install -D odoo/odoo/http/router.py "$OVERLAY/odoo/http/router.py" 2>/dev/null || true
if [ -f "$OVERLAY/odoo/http/router.py" ]; then
    kept=$((kept + 1))
fi
for rel in \
    web/controllers/home.py \
    web/controllers/utils.py \
    web/controllers/webmanifest.py \
    web/static/src/core/browser/router.js \
    web/static/src/service_worker.js \
    web/static/src/webclient/webclient.js \
    web/static/src/core/pwa/pwa_service.js \
    web/static/src/webclient/navbar/navbar.js \
    web/static/src/webclient/navbar/navbar.xml \
    web/static/src/webclient/menus/menu_helpers.js \
    web/static/src/views/fields/many2one/many2one.js \
    mail/static/src/service_worker.js \
    mail/static/src/core/common/store_service.js \
    mail/static/src/core/common/notification_message.js \
    mail/static/src/core/web/follower_subtype_dialog.js \
    mail/controllers/mail.py \
    mail/controllers/discuss/public_page.py \
    point_of_sale/static/src/app/services/pos_store.js \
    project/static/src/project_sharing/router.js \
    calendar/static/src/views/calendar_form/calendar_event_form_controller.js \
    hr_expense/models/hr_expense.py \
    website/static/src/client_actions/website_preview/website_builder_action.js \
    website/static/src/client_actions/website_preview/new_content_systray_item.js \
    website/static/src/js/editor/html_editor.js \
    website/static/src/js/tours/tour_utils.js \
    website/static/src/services/google_maps_service.js \
    website/static/src/client_actions/configurator/configurator.js \
    website/static/src/builder/plugins/form/form_option_plugin.js \
    website_mass_mailing/static/src/website_builder/mailing_list_subscribe_option_plugin.js \
    website_payment/static/src/snippets/s_supported_payment_methods/supported_payment_methods.edit.js \
    html_builder/static/src/snippets/snippet_service.js \
    api_doc/static/src/components/doc_modal_api_key.js \
    mysubscription/static/src/components/navbar.xml \
    web/controllers/session.py \
    web/controllers/database.py \
    html_editor/controllers/main.py \
    website/models/website.py \
    website/controllers/main.py \
; do
    src="odoo/addons/$rel"
    if [ -f "$src" ] && [ ! -e "$OVERLAY/odoo/addons/$rel" ]; then
        install -D "$src" "$OVERLAY/odoo/addons/$rel"
        kept=$((kept + 1))
    fi
done

# Action-service race fix: an action that was superseded while still loading
# (e.g. the slow home dashboard) must not mount over a newer menu selection,
# and the blank "clear breadcrumbs" transition must not hang forever when a
# concurrent update replaces it. Functional patch -- the drift test above
# would drop it, so it is kept explicitly. (See the "Vuma:" comments in the
# file for the two guarded spots.)
install -D odoo/addons/web/static/src/webclient/actions/action_plugin.js \
    "$OVERLAY/odoo/addons/web/static/src/webclient/actions/action_plugin.js"
kept=$((kept + 1))

echo
echo "branding files kept    : $kept"
echo "rebranded but skipped  : $skipped_branding  (functional drift; image copy kept)"
echo "plain upstream drift   : $skipped_plain  (ignored by design)"
echo "overlay written to $OVERLAY"
du -sh "$OVERLAY"
