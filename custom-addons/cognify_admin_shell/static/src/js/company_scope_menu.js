import { Component } from "@odoo/owl";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownGroup } from "@web/core/dropdown/dropdown_group";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { useDropdownState } from "@web/core/dropdown/dropdown_hooks";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

// The stock Odoo navbar switcher is a checkbox list of raw companies whose
// button always shows the first active company ("Trade Rite") even when both
// trading companies are on. Vuma works in scopes instead, so the navbar gets
// the same entries as the sidebar -- Trade Rite, Siyaya DC, Both Companies,
// All Companies -- and the button names the scope that is really active.

const TRADE_RITE = "Trade Rite";
const SIYAYA_DC = "Siyaya DC";

export class VumaCompanyScopeMenu extends Component {
    static template = "cognify_admin_shell.CompanyScopeMenu";
    static components = { Dropdown, DropdownGroup, DropdownItem };

    setup() {
        this.dropdown = useDropdownState();
        this.user = user;
    }

    get allowedCompanies() {
        return this.user.allowedCompanies || [];
    }

    get isSingleCompany() {
        return this.allowedCompanies.length <= 1;
    }

    get trade() {
        return this.allowedCompanies.find((c) => c.name === TRADE_RITE);
    }

    get siyaya() {
        return this.allowedCompanies.find((c) => c.name === SIYAYA_DC);
    }

    get scopes() {
        const scopes = [];
        const trade = this.trade;
        const siyaya = this.siyaya;
        if (trade) {
            scopes.push({
                id: "tr",
                label: trade.name,
                icon: "business",
                dotStyle: this.dotStyle([trade]),
                companies: [trade],
            });
        }
        if (siyaya) {
            scopes.push({
                id: "sy",
                label: siyaya.name,
                icon: "local_shipping",
                dotStyle: this.dotStyle([siyaya]),
                companies: [siyaya],
            });
        }
        if (trade && siyaya) {
            scopes.push({
                id: "both",
                label: "Both Companies",
                icon: "apps",
                hint: "Trade Rite + Siyaya DC — cross-company orders and payments",
                dotStyle: this.dotStyle([trade, siyaya]),
                companies: [trade, siyaya],
            });
        }
        if (this.allowedCompanies.length > 1) {
            scopes.push({
                id: "all",
                label: "All Companies",
                icon: "public",
                hint: "Every company this account may work in",
                dotStyle: this.dotStyle(this.allowedCompanies),
                companies: this.allowedCompanies,
            });
        }
        return scopes;
    }

    get activeScopeId() {
        try {
            const active = this.user.activeCompanies.map((c) => c.id);
            const trade = this.trade;
            const siyaya = this.siyaya;
            const bothActive =
                trade && siyaya && active.includes(trade.id) && active.includes(siyaya.id);
            if (bothActive && active.every((id) => id === trade.id || id === siyaya.id)) {
                return "both";
            }
            if (
                this.allowedCompanies.length &&
                this.allowedCompanies.every((c) => active.includes(c.id))
            ) {
                return "all";
            }
            if (trade && active.length === 1 && active[0] === trade.id) {
                return "tr";
            }
            if (siyaya && active.length === 1 && active[0] === siyaya.id) {
                return "sy";
            }
        } catch (e) {
            /* fall through to custom below */
        }
        return "custom";
    }

    get activeScope() {
        return this.scopes.find((s) => s.id === this.activeScopeId) || null;
    }

    get activeScopeLabel() {
        const scope = this.activeScope;
        if (scope) {
            return scope.label;
        }
        try {
            return this.user.activeCompanies.map((c) => c.name).join(" + ") || "";
        } catch (e) {
            return "";
        }
    }

    get activeDotStyle() {
        const scope = this.activeScope;
        return scope ? scope.dotStyle : "";
    }

    dotStyle(companies) {
        const colours = [];
        for (const company of companies || []) {
            const colour = (company && company.vuma_background_color) || "";
            if (/^#[0-9a-f]{6}$/i.test(colour) && !colours.includes(colour.toLowerCase())) {
                colours.push(colour.toLowerCase());
            }
        }
        if (!colours.length) {
            return "";
        }
        if (colours.length === 1) {
            return `background: ${colours[0]}`;
        }
        const step = 100 / colours.length;
        const stops = colours.map((colour, index) => {
            const start = (step * index).toFixed(4);
            const end = (step * (index + 1)).toFixed(4);
            return `${colour} ${start}% ${end}%`;
        });
        return `background: linear-gradient(90deg, ${stops.join(", ")})`;
    }

    selectScope(scope) {
        const ids = (scope.companies || []).map((c) => c.id);
        if (!ids.length) {
            return;
        }
        this.dropdown.close();
        Promise.resolve(
            this.user.activateCompanies(ids, { includeChildCompanies: false, reload: true })
        ).catch(() => {
            // A failed scope change must not take the navbar down.
        });
    }
}

// Guarded like navbar.js: a failure here must not abort every later module
// and blank out /odoo. `web` registers its switcher while web.assets_backend
// is still evaluating, so removing it once at import time is enough.
try {
    const systray = registry.category("systray");
    systray.remove("SwitchCompanyMenu");
    systray.add("VumaCompanyScopeMenu", { Component: VumaCompanyScopeMenu }, { sequence: 1 });
} catch (e) {
    console.warn("[vuma] could not install the company scope switcher", e);
}
