import { _t } from "@web/core/l10n/translation";
import { NotificationPlugin } from "@web/core/notifications/notification_plugin";
import { BusPlugin } from "@bus/services/bus_plugin";
import { Plugin, usePlugin } from "@odoo/owl";
import { services } from "@web/core/services";

/**
 * Listens on the bus for expiry alerts raised by the server and turns each one
 * into a toast in the corner of the screen.
 *
 * The server sends the payload with an `action` already built, so the "Review"
 * button opens the Expiry Alerts list without the client needing to know how the
 * domain is constructed.
 */
export class ExpiryAlertPlugin extends Plugin {
    bus = usePlugin(BusPlugin);
    notification = usePlugin(NotificationPlugin);

    setup() {
        const unsubscribe = this.bus.subscribe(
            "cognify_expiry_manager.expiry_alert",
            ({ title, body, type, action }) => {
                const buttons = [];
                if (action) {
                    buttons.push({
                        name: _t("Review"),
                        primary: true,
                        onClick: () => this.env.services.action.doAction(action),
                    });
                }
                this.notification.add(body, {
                    title,
                    type: type || "warning",
                    sticky: true,
                    buttons,
                });
            }
        );
        this.bus.start();
    }
}

services.add(ExpiryAlertPlugin);