import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";

const PANEL_STORAGE_KEY = "vuma.chatter.panelCollapsed";
const LOG_STORAGE_KEY = "vuma.chatter.logCollapsed";

function readFlag(key, fallback = false) {
    try {
        return browser.localStorage?.getItem(key) === "1";
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
    },

    toggleLogMessages() {
        this.state.showLogMessages = !this.state.showLogMessages;
        writeFlag(LOG_STORAGE_KEY, !this.state.showLogMessages);
    },
});
