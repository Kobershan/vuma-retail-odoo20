import { NavBar } from "@web/webclient/navbar/navbar";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useBus } from "@web/core/utils/hooks";

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
