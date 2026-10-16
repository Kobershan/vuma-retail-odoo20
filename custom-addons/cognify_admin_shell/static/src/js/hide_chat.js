import { registry } from "@web/core/registry";

function stripChatSystray() {
    const reg = registry.category("systray");
    let changed = false;
    for (const key of reg.getKeys()) {
        if (
            key === "mail.messaging_menu" ||
            key === "mail.discuss" ||
            key === "discuss.CallMenu" ||
            key === "discuss.calls"
        ) {
            try {
                reg.remove(key);
                changed = true;
            } catch (e) {
                /* ignore */
            }
        }
    }
    try {
        registry.category("main_components").remove("mail.ChatHub");
    } catch (e) {
        /* ignore */
    }
    if (changed) {
        try {
            globalThis.odoo?.bus?.trigger?.("SYSTRAY:UPDATE");
        } catch (e) {
            /* ignore */
        }
    }
}

// Run now and again in case mail registers late.
stripChatSystray();
try {
    if (registry.category("systray") instanceof EventTarget) {
        registry.category("systray").addEventListener("UPDATE", stripChatSystray);
    }
} catch (e) {
    /* ignore */
}

// de-register discussion chat windows.
const services = registry.category("services");
for (const key of services.getKeys()) {
    if (key === "mail.chat_hub") {
        try {
            services.remove(key);
        } catch (e) {
            /* ignore */
        }
    }
}

// Hide live chat embed if present.
const style = document.createElement("style");
style.textContent = `
    .o-mail-ChatHub, .o-mail-ChatBubble, .o-mail-ChatWindow,
    .o-livechat-root, .o-livechat-LivechatButton { display: none !important; }
`;
document.head.appendChild(style);
