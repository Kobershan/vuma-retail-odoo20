import { NavBar } from "@web/webclient/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useBus } from "@web/core/utils/hooks";

// Guarded: this runs while `web.assets_web` is still evaluating, before
// `main.js` starts the webclient. Odoo's module loader aborts every later
// module -- including `main.js` itself -- when one module throws, so a failure
// here would blank out /odoo entirely rather than just losing this patch.
try {
    patch(NavBar.prototype, {
        setup() {
            super.setup();
            this.vumaBus = this.env.bus;
            this.uiV = useService("ui");
            useBus(this.vumaBus, "VUMA_SIDEBAR_TOGGLE", () => {
                // noop here; sidebar listens too; keep hook for symmetry
            });
        },

        get currentAppName() {
            return this.currentApp ? this.currentApp.name : "";
        },

        onVumaToggleSidebar() {
            this.env.bus.trigger("VUMA_SIDEBAR_TOGGLE", {});
        },

        onVumaSearch() {
            try {
                const command = this.env.services.command;
                if (command && typeof command.openMainPalette === "function") {
                    command.openMainPalette();
                }
            } catch (e) {
                // stay silent if the service isn't available
            }
        },
    });
} catch (e) {
    console.warn("[vuma] could not patch the navbar", e);
}
