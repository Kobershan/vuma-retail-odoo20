import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useBus } from "@web/core/utils/hooks";
import { Component, onMounted, onWillStart, proxy, signal, useEffect, useScope } from "@odoo/owl";
import { user } from "@web/core/user";
import { imageUrl } from "@web/core/utils/urls";
import { Dropdown } from "@web/core/dropdown/dropdown";
import { DropdownItem } from "@web/core/dropdown/dropdown_item";
import { CheckBox } from "@web/core/checkbox/checkbox";

// Apps deliberately grouped so the sidebar can be scanned instead of read.
const GROUPS = [
    { key: "sell", label: "Marketing", icons: "sale_management,sale,crm,contacts,website,mass_mailing,utm", order: 10 },
    { key: "stock", label: "Stock", icons: "stock,purchase,mrp,repair,customer_goods_pickup,cognify_expiry_manager", order: 20 },
    { key: "money", label: "Money", icons: "account,spreadsheet_dashboard,cognify_management_reports,cognify_remittance", order: 30 },
    { key: "work", label: "Work", icons: "project,project_todo,calendar", order: 40 },
    { key: "people", label: "People", icons: "hr,hr_attendance,hr_holidays,hr_timesheet,hr_expense,fleet,hr_skills", order: 50 },
];
const PINNED = [
    { xmlid: "base.menu_administration", label: "Settings" },
];

// Apps that must never surface on the dashboards.
const HIDDEN_APPS = new Set(["mail.menu_root_discuss"]);

const APP_GROUP = {
    // Marketing (the "sell" key is historical; the label reads Marketing)
    "sale_management.sale_menu_root": "sell", "crm.crm_menu_root": "sell",
    "contacts.menu_contacts": "sell", "website.menu_website_configuration": "sell",
    "mass_mailing.mass_mailing_menu_root": "sell", "calendar.mail_menu_calendar": "sell",
    // Work
    "project.menu_main_pm": "work", "project_todo.menu_todo_todos": "work",
    // Stock
    "stock.menu_stock_root": "stock", "purchase.menu_purchase_root": "stock",
    "mrp.menu_mrp_root": "stock", "repair.menu_repair_order": "stock",
    "customer_goods_pickup.menu_customer_goods_pickup_root": "stock",
    "cognify_expiry_manager": "stock", "cognify_product_images": "stock",
    // Money
    "account.menu_finance": "money", "spreadsheet_dashboard.spreadsheet_dashboard_menu_root": "money",
    "cognify_remittance.menu_cognify_remittance_root": "money",
    "cognify_management_reports.menu_cognify_management": "money",
    "utm.menu_link_tracker_root": "money",
    // People
    "hr.menu_hr_root": "people", "hr_attendance.menu_hr_attendance_root": "people",
    "hr_holidays.menu_hr_holidays_root": "people", "hr_timesheet.timesheet_menu_root": "people",
    "hr_expense.menu_hr_expense_root": "people", "fleet.menu_root": "people",
};

function groupKeyFor(app) {
    if (APP_GROUP[app.xmlid]) {
        return APP_GROUP[app.xmlid];
    }
    return "more";
}

export class VumaSidebar extends Component {
    static template = "vuma.Sidebar";
    static components = { Dropdown, DropdownItem, CheckBox };

    setup() {
        // Captured here (like web.UserMenu does) so the user_menuitems
        // factories can run inside this component's hook scope.
        this.scope = useScope();
        this.menuService = useService("menu");
        this.actionService = useService("action");
        this.ui = useService("ui");
        this.user = user;

        this.state = proxy({
            collapsed: this.loadCollapsed(),
            search: "",
            expandedFolders: {},
            // Per-app visibility of the inline section list. Without this the
            // sections were shown purely because the app was current, so the
            // current app could never be collapsed again.
            openApps: {},
            openFlyout: null,
            focusSearch: 0,
            isFullscreen: false,
            mobileOpen: false,
        });

        // Optional website editor menus. Read from service registry without hooks.
        this.websiteCustomMenus = (this.env.services && this.env.services.website_custom_menus) || null;

        useBus(this.env.bus, "VUMA_SIDEBAR_TOGGLE", () => this.toggleCollapse());

        useBus(this.env.bus, "MENUS:APP-CHANGED", () => {
            // Force rerender via signals by setting a blank field.
            this.state.search = this.state.search;
        });

        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", ({ detail: mode }) => {
            this.state.isFullscreen = mode === "fullscreen";
            if (this.state.isFullscreen) {
                document.body.classList.add("o_vuma_fullscreen");
            } else {
                document.body.classList.remove("o_vuma_fullscreen");
            }
            // Bump the render marker so active-menu highlighting is re-evaluated.
            this.state.search = this.state.search;
        });

        useEffect(() => {
            document.body.classList.add("o_vuma_has_sidebar");
            if (this.state.collapsed) {
                document.body.classList.add("o_vuma_sidebar_collapsed");
            } else {
                document.body.classList.remove("o_vuma_sidebar_collapsed");
            }
            if (this.state.mobileOpen) {
                document.body.classList.add("o_vuma_sidebar_open");
            } else {
                document.body.classList.remove("o_vuma_sidebar_open");
            }
            return () => {
                document.body.classList.remove("o_vuma_has_sidebar");
                document.body.classList.remove("o_vuma_sidebar_collapsed");
                document.body.classList.remove("o_vuma_sidebar_open");
            };
        }, () => [this.state.collapsed, this.state.mobileOpen]);

        // mobile-specific state lives in state.
        this.state.mobileOpen = false;
    }

    get isSmall() {
        return this.ui.isSmall;
    }

    loadCollapsed() {
        try {
            return localStorage.getItem("vuma.sidebar.collapsed") === "1";
        } catch (e) {
            return false;
        }
    }

    saveCollapsed(v) {
        try {
            localStorage.setItem("vuma.sidebar.collapsed", v ? "1" : "0");
        } catch (e) {
            /* ignore */
        }
    }

    toggleCollapse() {
        if (this.isSmall) {
            this.state.mobileOpen = !this.state.mobileOpen;
            return;
        }
        this.state.collapsed = !this.state.collapsed;
        this.saveCollapsed(this.state.collapsed);
    }

    openMobile() {
        this.state.mobileOpen = true;
    }

    closeMobile() {
        this.state.mobileOpen = false;
    }

    onSearchInput(ev) {
        this.state.search = ev.target.value || "";
    }

    onSearchKeydown(ev) {
        if (ev.key === "Escape") {
            this.state.search = "";
            ev.target.value = "";
            ev.target.blur();
            return;
        }
        if (ev.key === "Enter") {
            const hits = this.searchResults();
            if (hits.length) {
                const first = hits[0];
                this.closeMobile();
                if (first.menu) {
                    this.menuService.selectMenu(first.menu);
                } else {
                    this.menuService.selectMenu(first.app.id);
                }
            }
        }
    }

    // ---------- Data getters ----------

    get currentApp() {
        return this.menuService.getCurrentApp();
    }

    get currentAppTree() {
        const app = this.currentApp;
        if (!app) {
            return null;
        }
        try {
            const tree = this.menuService.getMenuAsTree(app.id);
            if (app.xmlid === "website.menu_website_configuration" && this.websiteCustomMenus) {
                return {
                    ...tree,
                    childrenTree: this.websiteCustomMenus
                        .addCustomMenus(tree.childrenTree)
                        .filter((s) => s.childrenTree && s.childrenTree.length),
                };
            }
            return tree;
        } catch (e) {
            return null;
        }
    }

    appIconSrc(app) {
        return app.webIconData || null;
    }

    iconLetter(app) {
        return (app.name || "?").trim().charAt(0);
    }

    get allApps() {
        try {
            return this.menuService.getApps().filter((app) => !HIDDEN_APPS.has(app.xmlid));
        } catch (e) {
            return [];
        }
    }

    groupedApps() {
        const apps = this.allApps;
        const pinnedXml = new Set(PINNED.map((p) => p.xmlid));
        const grouped = {};
        const ordered = [];
        for (const group of GROUPS) {
            grouped[group.key] = { group, items: [] };
            ordered.push(group.key);
        }
        grouped.more = { group: { key: "more", label: "More", order: 60 }, items: [] };
        ordered.push("more");
        grouped.system = { group: { key: "system", label: "System", order: 90 }, items: [] };

        for (const app of apps) {
            if (pinnedXml.has(app.xmlid)) {
                continue;
            }
            let key = groupKeyFor(app);
            if (!Object.prototype.hasOwnProperty.call(grouped, key)) {
                key = "more";
            }
            grouped[key].items.push(app);
        }
        // Only keep non-empty groups.
        return ordered
            .map((k) => grouped[k])
            .filter((g) => g.items.length > 0);
    }

    // A method, not a getter: sidebar.xml calls it as `this.pinnedApps()`.
    // Declaring it `get` made that call throw `TypeError: ... is not a function`
    // on the first render, which aborted the WebClient mount and left /vuma blank.
    pinnedApps() {
        const apps = this.allApps;
        const list = [];
        for (const p of PINNED) {
            const app = apps.find((a) => a.xmlid === p.xmlid);
            if (app) {
                list.push({ app, label: p.label });
            }
        }
        return list;
    }

    // ---------- Behavior ----------

    isCurrentApp(app) {
        const ca = this.currentApp;
        return ca && ca.id === app.id;
    }

    selectApp(app) {
        this.closeMobile();
        // Clicking the app you are already on toggles its sections instead of
        // pointlessly re-running the same action.
        if (this.isCurrentApp(app)) {
            this.toggleAppOpen(app);
            return;
        }
        // Entering a different app opens its sections.
        this.state.openApps[app.id] = true;
        this.menuService.selectMenu(app.id);
    }

    // Sections default to open for the current app, so its navigation is
    // visible the moment you land on it.
    isAppOpen(app) {
        const v = this.state.openApps[app.id];
        return v === undefined ? true : v;
    }

    toggleAppOpen(app) {
        this.state.openApps[app.id] = !this.isAppOpen(app);
    }

    toggleFolder(id) {
        const curr = this.state.expandedFolders[id];
        this.state.expandedFolders[id] = !curr;
    }

    isFolderOpen(id) {
        if (this.state.expandedFolders[id] !== undefined) {
            return this.state.expandedFolders[id];
        }
        return this.isFolderInModel(id);
    }

    isFolderInModel(id) {
        // Auto-open if current app section contains the active menu.
        const tree = this.currentAppTree;
        if (!tree || !tree.childrenTree) {
            return false;
        }
        const find = (node) => {
            if (node.id === id) {
                return node;
            }
            for (const c of node.childrenTree || []) {
                const r = find(c);
                if (r) {
                    return r;
                }
            }
            return null;
        };
        const node = find(tree);
        if (node && node.childrenTree && node.childrenTree.length) {
            // Expand folders that own the currently active menu.
            const activeId = this.activeMenuId();
            if (!activeId) {
                // Open first-level folders by default for navigability.
                return tree.childrenTree.some((c) => c.id === id);
            }
            const owns = ownsActive(node, activeId);
            if (owns) {
                return true;
            }
        }
        return false;
    }

    activeMenuId() {
        try {
            const ctrl = this.actionService.currentController;
            const actionId = ctrl && ctrl.action && ctrl.action.id;
            if (!actionId) {
                return null;
            }
            // Search the whole menu tree.
            const menus = this.menuService.getAll();
            for (const m of menus) {
                if (m.actionID === actionId && (m.id === m.appID || true)) {
                    return m.id;
                }
            }
        } catch (e) {
            /* ignore */
        }
        return null;
    }

    isMenuActive(menu) {
        const activeId = this.activeMenuId();
        if (activeId && menu && menu.id === activeId) {
            return true;
        }
        // also compare via action IDs
        try {
            const ctrl = this.actionService.currentController;
            const actionId = ctrl && ctrl.action && ctrl.action.id;
            if (actionId && menu && menu.actionID === actionId) {
                return true;
            }
        } catch (e) {
            /* ignore */
        }
        return false;
    }

    selectMenu(menu) {
        this.closeMobile();
        this.menuService.selectMenu(menu.id || menu);
        // Collapse the folder highlight state is unchanged — watchers rely on active detection.
    }

    // Handlers for the two places the template used a block-bodied arrow
    // function. OWL compiles `t-on-click` values as expressions, so
    // `() => { ...statements... }` produced `Unexpected token '{'` and the whole
    // `vuma.Sidebar` template failed to compile. Expression-only arrows work,
    // so the logic lives here instead.
    onResultClick(r) {
        this.closeMobile();
        if (r.menu) {
            this.menuService.selectMenu(r.menu.id);
        } else {
            this.menuService.selectMenu(r.app.id);
        }
    }

    onFolderClick(item) {
        this.toggleFolder(item.id);
        if (item.actionID) {
            this.selectMenu(item);
        }
    }

    onAppKeyDown(ev, app) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.selectApp(app);
        }
    }

    onItemKeyDown(ev, item) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.selectMenu(item);
        }
    }

    closeFlyout() {
        this.state.openFlyout = null;
    }

    openFlyoutFor(app) {
        if (this.state.collapsed && !this.isSmall) {
            this.state.openFlyout = app.id;
        }
    }

    // ---------- Search ----------

    searchResults() {
        const q = (this.state.search || "").trim().toLowerCase();
        if (!q) {
            return [];
        }
        const out = [];
        for (const app of this.allApps) {
            if (app.name.toLowerCase().includes(q)) {
                out.push({ type: "app", app, menu: null, path: "" });
            }
            // traverse submenus
            let tree = null;
            try {
                tree = this.menuService.getMenuAsTree(app.id);
            } catch (e) {
                tree = null;
            }
            if (tree) {
                const heap = (node, parents) => {
                    for (const child of node.childrenTree || []) {
                        if (!child.id || !child.actionID) {
                            heap(child, parents);
                            continue;
                        }
                        if ((child.name || "").toLowerCase().includes(q)) {
                            out.push({
                                type: "menu",
                                app,
                                menu: child,
                                path: parents.join(" / "),
                            });
                        }
                        heap(child, parents.concat([child.name]));
                    }
                };
                heap(tree, [app.name]);
            }
        }
        // de-duplicate app hits identical labels
        return out.slice(0, 40);
    }

    imageUrlForUser() {
        return imageUrl("res.partner", this.user.partnerId, "avatar_128", {
            unique: this.user.writeDate,
        });
    }

    companyName() {
        try {
            return this.user.activeCompany ? this.user.activeCompany.name : "";
        } catch (e) {
            return "";
        }
    }

    // ---------- Account menu (footer) ----------

    // Same contract as the stock web.UserMenu: the "user_menuitems" registry
    // factories run inside this component's scope so their hooks
    // (useService/usePlugin) bind here, then they render as menu entries.
    // Anything that throws is dropped instead of breaking the sidebar, and
    // component-type entries (the chat presence widget) are skipped because
    // the Vuma shell removed chat on purpose.
    profileMenuItems() {
        let items = [];
        try {
            items = registry
                .category("user_menuitems")
                .getAll()
                .map((element) => {
                    try {
                        return this.scope.run(() => element(this.env));
                    } catch (e) {
                        console.warn("[vuma] user menu item failed", e);
                        return null;
                    }
                })
                .filter(Boolean);
        } catch (e) {
            return [];
        }
        return items
            .filter((el) => el.type !== "component")
            .sort((x, y) => (x.sequence || 100) - (y.sequence || 100));
    }

    onProfileMenuSelect(element) {
        // Close the phone drawer first so dialogs land on the content
        // underneath instead of on the drawer.
        this.closeMobile();
        if (element.callback) {
            element.callback();
        }
    }

    focusSearchInput() {
        if (this.state.collapsed && !this.isSmall) {
            this.state.collapsed = false;
            this.saveCollapsed(false);
        }
        setTimeout(() => {
            const el = document.querySelector(".vuma-sidebar .vuma-search input");
            if (el) el.focus();
        }, 200);
    }
}

function ownsActive(node, activeId) {
    if (node.id === activeId) {
        return true;
    }
    for (const c of node.childrenTree || []) {
        if (ownsActive(c, activeId)) {
            return true;
        }
    }
    return false;
}

// Register.
try {
    registry.category("main_components").add("vuma.Sidebar", {
        Component: VumaSidebar,
    });

} catch (e) {
    console.warn("VumaSidebar registration failed", e);
}