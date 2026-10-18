import { registry } from "@web/core/registry";

/**
 * Remove the messaging systray items and the chat bubble from the backend.
 *
 * Two rules, both learned the hard way:
 *
 * 1. Use the real Registry API. Odoo 20's Registry exposes `add`, `get`,
 *    `contains`, `getAll`, `getEntries` and `remove` -- there is no `getKeys()`.
 *    Iterating `getKeys()` threw `TypeError: reg.getKeys is not a function`.
 *
 * 2. Nothing here may be allowed to throw. This file is evaluated while
 *    `web.assets_web` loads, *before* `web/static/src/main.js` calls
 *    `startWebClient()`. Odoo's module loader aborts the remaining modules when
 *    one fails, so an exception here does not just disable the chat -- it takes
 *    the entire webclient down, leaving `/odoo` (and `/vuma`, which iframes it)
 *    as a blank page with no server-side trace at all. Every operation below is
 *    therefore wrapped, and the CSS at the bottom works even if all of it fails.
 */

// Systray entries contributed by `mail`, and the component that renders the
// floating chat bubble / chat windows. "mail.activity_menu" is the little
// clock in the navbar: Vuma reads activities from the dashboards, never from
// the systray, and on phones it opened an old-style dropdown that clashed
// with the Vuma burger.
const SYSTRAY_KEYS = [
    "mail.activity_menu",
    "mail.messaging_menu",
    "mail.discuss",
    "discuss.CallMenu",
    "discuss.calls",
];
const MAIN_COMPONENT_KEYS = ["mail.ChatHub"];

function stripChatSystray() {
    try {
        const systray = registry.category("systray");
        for (const [key] of systray.getEntries()) {
            if (SYSTRAY_KEYS.includes(key) && systray.contains(key)) {
                systray.remove(key);
            }
        }
    } catch (e) {
        console.warn("[vuma] could not clear chat systray items", e);
    }
    for (const key of MAIN_COMPONENT_KEYS) {
        try {
            const main = registry.category("main_components");
            if (main.contains(key)) {
                main.remove(key);
            }
        } catch (e) {
            console.warn("[vuma] could not remove chat component", e);
        }
    }
}

// mail registers late, so re-run whenever the systray updates. `remove()` is
// only called for keys still present, so this settles instead of looping.
stripChatSystray();
try {
    registry.category("systray").addEventListener("UPDATE", stripChatSystray);
} catch (e) {
    /* the event bus API differs between versions; the CSS below is the fallback */
}

// Backstop: even if a chat component survives, it is invisible.
try {
    const style = document.createElement("style");
    style.textContent = `
        .o-mail-ChatHub, .o-mail-ChatBubble, .o-mail-ChatWindow,
        .o-livechat-root, .o-livechat-LivechatButton { display: none !important; }
    `;
    document.head.appendChild(style);
} catch (e) {
    /* ignore */
}
