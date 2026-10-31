/**
 * Paint the Vuma shell with the colours of the companies currently active.
 *
 * Each company stores an optional `vuma_background_color` (settings > company
 * form). Session info carries that value to the browser, so the shell can be
 * themed on first paint without an extra RPC.
 *
 * One active company  -> solid colour accents.
 * Several companies   -> a hard-stop gradient, so both/all colours stay
 *                        visible and a worker cannot mistake a mixed scope for
 *                        a single company.
 *
 * The actual tints are translucent colour layers over the theme's paper,
 * panel and ink variables, so dark mode automatically uses the dark bases
 * instead of producing a light background.
 */
import { user, userBus } from "@web/core/user";

const HEX_COLOUR = /^#([0-9a-f]{6})$/i;
const COLOUR_PROPERTIES = [
    "--vuma-company-color",
    "--vuma-company-color-2",
    "--vuma-company-on-color",
    "--vuma-company-stripe",
    "--vuma-company-header",
];

function parseColour(value) {
    if (typeof value !== "string") {
        return null;
    }
    const match = HEX_COLOUR.exec(value.trim());
    return match ? `#${match[1].toLowerCase()}` : null;
}

function luminance(hex) {
    const channels = [1, 3, 5].map((offset) => {
        const channel = parseInt(hex.slice(offset, offset + 2), 16) / 255;
        return channel <= 0.03928
            ? channel / 12.92
            : ((channel + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2];
}

function gradient(colours, direction, render) {
    const step = 100 / colours.length;
    const stops = colours.map((colour, index) => {
        const start = (step * index).toFixed(4);
        const end = (step * (index + 1)).toFixed(4);
        return `${render(colour)} ${start}% ${end}%`;
    });
    return `linear-gradient(${direction}, ${stops.join(", ")})`;
}

function applyCompanyTheme() {
    if (!document.body) {
        return;
    }
    try {
        const colours = [];
        for (const company of user.activeCompanies || []) {
            const colour = parseColour(company && company.vuma_background_color);
            if (colour && !colours.includes(colour)) {
                colours.push(colour);
            }
        }

        if (!colours.length) {
            for (const property of COLOUR_PROPERTIES) {
                document.body.style.removeProperty(property);
            }
            return;
        }

        const primary = colours[0];
        const properties = {
            "--vuma-company-color": primary,
            "--vuma-company-color-2": colours[1] || primary,
            "--vuma-company-on-color": luminance(primary) > 0.42 ? "#14161a" : "#ffffff",
            "--vuma-company-stripe": gradient(colours, "90deg", (colour) => colour),
            // Only the logo band at the top of the sidebar wears the company
            // colours. The main canvas stays white and the rest of the sidebar
            // black.
            "--vuma-company-header": gradient(
                colours,
                "90deg",
                (colour) => colour
            ),
        };

        for (const [property, value] of Object.entries(properties)) {
            document.body.style.setProperty(property, value);
        }
    } catch (error) {
        // A theme failure must never take the whole web client down.
        console.warn("[vuma] could not apply company colours", error);
    }
}

if (document.body) {
    applyCompanyTheme();
} else {
    document.addEventListener("DOMContentLoaded", applyCompanyTheme, { once: true });
}

// Company switches normally reload the page, but `reload: false` is used by a
// few Odoo flows. Repaint immediately when that happens.
userBus.addEventListener("ACTIVE_COMPANIES_CHANGED", applyCompanyTheme);
