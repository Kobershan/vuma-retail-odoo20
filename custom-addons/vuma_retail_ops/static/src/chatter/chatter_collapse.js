import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { onMounted, onPatched } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";

const PANEL_STORAGE_KEY = "vuma.chatter.panelCollapsed";
const LOG_STORAGE_KEY = "vuma.chatter.logCollapsed";

function readFlag(key, fallback = false) {
    try {
        const raw = browser.localStorage?.getItem(key);
        if (raw === null || raw === undefined) {
            return fallback;
        }
        return raw === "1";
    } catch {
        return fallback;
    }
}

function writeFlag(key, value) {
    try {
        browser.localStorage?.setItem(key, value ? "1" : "0");
    } catch {
        /* ignore storage quota / privacy mode */
    }
}

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        Object.assign(this.state, {
            isChatterCollapsed: readFlag(PANEL_STORAGE_KEY, false),
            showLogMessages: !readFlag(LOG_STORAGE_KEY, false),
        });
        onMounted(() => this._syncVumaCollapseClasses());
        onPatched(() => this._syncVumaCollapseClasses());
    },

    _syncVumaCollapseClasses() {
        try {
            const rootEl = this.rootRef?.el;
            if (!rootEl) {
                return;
            }
            const collapsed = Boolean(this.state.isChatterCollapsed);
            const container = rootEl.closest(".o-mail-Form-chatter, .o-mail-ChatterContainer");
            if (container) {
                container.classList.toggle("o-vuma-aside-collapsed", collapsed);
            }
            const formView = rootEl.closest(".o_form_view");
            if (formView) {
                formView.classList.toggle("o-vuma-form-chatter-collapsed", collapsed);
            }
        } catch {
            /* never break rendering */
        }
    },

    get logMessageCount() {
        try {
            return this.state.thread?.messages?.length || 0;
        } catch {
            return 0;
        }
    },

    toggleChatterCollapsed() {
        this.state.isChatterCollapsed = !this.state.isChatterCollapsed;
        writeFlag(PANEL_STORAGE_KEY, this.state.isChatterCollapsed);
        this._syncVumaCollapseClasses();
    },

    toggleLogMessages() {
        this.state.showLogMessages = !this.state.showLogMessages;
        writeFlag(LOG_STORAGE_KEY, !this.state.showLogMessages);
    },
});
