/**
 * Vuma Dashboard Company Switcher (systray widget).
 *
 * One button next to the brand area offering instant scopes:
 *     [All Companies] [Both Companies] [Trade Rite] [Siyaya DC]
 * It drives the standard `user.activateCompanies` service, so it switches the
 * data shown by EVERY window - lists, charts, all dashboards, invoices,
 * payments, reports - because every company-scoped record rule and every
 * dashboard graph reads `allowed_company_ids`.
 */
/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { _t } from "@web/core/l10n/translation";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";

const SCOPES = [
    { id: "all", label: "All Companies" },
    { id: "both", label: "Both Companies" },
    { id: "tr", label: "Trade Rite" },
    { id: "sy", label: "Siyaya DC" },
];

class VumaCompanySwitch extends Component {
    static template = "vuma_multi_tenant.VumaCompanySwitch";
    static components = { Dropdown, DropdownItem };

    //#region scoped look-ups
    _companyByName(name) {
        return user.allowedCompaniesWithAncestors.find((c) => c.name === name);
    }

    get tradeCompany() {
        return this._companyByName("Trade Rite");
    }

    get siyayaCompany() {
        return this._companyByName("Siyaya DC");
    }

    get activeScope() {
        const tr = this.tradeCompany;
        const sy = this.siyayaCompany;
        const active = user.activeCompanies.map((c) => c.id);
        const all = user.allowedCompanies.map((c) => c.id);
        const onlyTradingActive =
            tr &&
            sy &&
            active.includes(tr.id) &&
            active.includes(sy.id) &&
            active.every((id) => id === tr.id || id === sy.id);
        if (onlyTradingActive) {
            return "both";
        }
        if (all.length && all.every((id) => active.includes(id))) {
            return "all";
        }
        if (tr && active.length === 1 && active[0] === tr.id) {
            return "tr";
        }
        if (sy && active.length === 1 && active[0] === sy.id) {
            return "sy";
        }
        return "custom";
    }

    scopeIcon(scopeId) {
        switch (scopeId) {
            case "all":
                return "fa-globe";
            case "both":
                return "fa-th-large";
            case "tr":
                return "fa-building";
            case "sy":
                return "fa-truck";
            default:
                return "fa-random";
        }
    }

    /**
     * Ready-to-use icon class.
     *
     * The template reads this getter instead of calling `scopeIcon(...)`.
     * Inside slot content OWL evaluates expressions against the rendering
     * context, and a bare method call (`scopeIcon(activeScope)`) compiles to
     * `ctx.scopeIcon(ctx.activeScope)`. When that context is not the component
     * itself, `ctx.scopeIcon` is `undefined` and the call throws
     * `TypeError: ctx.scopeIcon is not a function` -- which kills the systray,
     * and with it the whole web client: a blank page after login.
     * A getter is read as `ctx.scopeIconClass`, so it can never throw.
     */
    get scopeIconClass() {
        return `fa-fw ${this.scopeIcon(this.activeScope)}`;
    }

    get hasMultipleCompanies() {
        return user.allowedCompanies.length > 1;
    }

    get hasBothCompanies() {
        return Boolean(this.tradeCompany && this.siyayaCompany);
    }

    get scopeLabel() {
        const found = SCOPES.find((s) => s.id === this.activeScope);
        if (found) {
            return found.label;
        }
        // A custom selection (for example a single company chosen from the
        // standard Odoo switcher) must show the real company instead of an
        // empty "Custom selection" label.
        return user.activeCompany ? user.activeCompany.name : _t("Select a company");
    }
    //#endregion

    updateActiveCompanies(companyIds) {
        Promise.resolve(
            user.activateCompanies(companyIds, {
                includeChildCompanies: false,
                reload: true,
            })
        ).catch(() => {
            // A failed scope change must never take the web client down.
        });
    }

    selectScope(scopeId) {
        const tr = this.tradeCompany;
        const sy = this.siyayaCompany;
        const allCompanyIds = user.allowedCompanies.map((company) => company.id);
        const targetIds = {
            all: allCompanyIds,
            both: tr && sy ? [tr.id, sy.id] : null,
            tr: tr ? [tr.id] : null,
            sy: sy ? [sy.id] : null,
        }[scopeId];
        if (targetIds) {
            this.updateActiveCompanies(targetIds);
        }
    }
}

const vumaCompanySystray = {
    Component: VumaCompanySwitch,
};

/**
 * Register the switcher defensively.
 *
 * A systray widget renders during the web client's boot. If it throws, the
 * exception propagates out of the systray tree and the user gets a BLANK
 * PAGE with no error and no way back -- the whole backend is unreachable.
 * A cosmetic company switcher must never be able to take the UI down, so a
 * failure here only costs the button itself.
 */
try {
    registry
        .category("systray")
        .add("vuma_multi_tenant.CompanySwitch", vumaCompanySystray, { sequence: 20 });
} catch (error) {
    console.error("Vuma company switcher disabled:", error);
}
