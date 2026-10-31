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

// The five business tabs. Labels and hierarchy are Vuma's; every leaf points
// at a real Odoo menu through its xmlid, so the tab is a curated view over the
// same actions the rest of Odoo runs. A leaf whose menu is missing (module not
// installed, or hidden for this user) is dropped at render time instead of
// showing a dead link. Folders (entries with `children`) mirror the business
// task, e.g. "Goods Receiving" holds receipts, store receiving and returns.
// `appXmlid` picks the application whose brand icon heads the tab; the `icon`
// glyph is only the fallback when that app is not visible to the user.
const BUSINESS_GROUPS = [
    {
        key: "products",
        label: "Products",
        icon: "inventory_2",
        appXmlid: "stock.menu_stock_root",
        items: [
            {
                label: "Products Dashboard",
                children: [
                    { label: "Stock Health and Value", xmlid: "cognify_management_reports.menu_cognify_stock_health" },
                    { label: "Low and Out of Stock", xmlid: "cognify_management_reports.menu_cognify_stock_low" },
                    { label: "Dead and Slow Moving Stock", xmlid: "cognify_management_reports.menu_cognify_stock_dead" },
                    { label: "Product Turnover", xmlid: "cognify_management_reports.menu_cognify_turnover" },
                    { label: "Slowest Moving", xmlid: "cognify_management_reports.menu_cognify_turnover_lowest" },
                ],
            },
            { label: "Product Profiles", xmlid: "stock.menu_product_variant_config_stock" },
            {
                label: "Stock Take",
                children: [
                    { label: "Physical Inventory", xmlid: "stock.menu_action_inventory_tree" },
                    { label: "Replenishment", xmlid: "stock.menu_reordering_rules_replenish" },
                    { label: "Scrap", xmlid: "stock.menu_stock_scrap" },
                    { label: "Lots / Serial Numbers", xmlid: "stock.menu_action_production_lot_form" },
                ],
            },
            {
                label: "Purchase Orders",
                children: [
                    { label: "Requests for Quotation", xmlid: "purchase.menu_purchase_rfq" },
                    { label: "Purchase Orders", xmlid: "purchase.menu_purchase_form_action" },
                    { label: "Vendors", xmlid: "purchase.menu_procurement_management_supplier_name" },
                ],
            },
            {
                label: "Goods Receiving",
                children: [
                    { label: "Receipts", xmlid: "stock.in_picking" },
                    { label: "Store Receiving", xmlid: "customer_goods_pickup.menu_customer_goods_pickup_store_receiving" },
                    { label: "Returns", xmlid: "customer_goods_pickup.menu_customer_goods_pickup_returns" },
                    { label: "Quarantine / Claims", xmlid: "customer_goods_pickup.menu_customer_goods_quarantine" },
                ],
            },
            {
                label: "Product Reports",
                children: [
                    { label: "Stock on Hand", xmlid: "stock.menu_product_stock" },
                    { label: "Stock Moves Analysis", xmlid: "stock.stock_move_menu" },
                    { label: "Purchases", xmlid: "purchase.purchase_report" },
                    { label: "Sales by Product", xmlid: "sale.menu_reporting_product" },
                    { label: "Cost of Sales", xmlid: "cognify_management_reports.menu_cognify_cost_of_sales" },
                    { label: "Expiry Report", xmlid: "cognify_expiry_manager.menu_expiry_report" },
                ],
            },
        ],
    },
    {
        key: "sales",
        label: "Sales",
        icon: "point_of_sale",
        appXmlid: "sale.sale_menu_root",
        items: [
            {
                label: "Sales Dashboard",
                children: [
                    { label: "Sales Overview", xmlid: "cognify_management_reports.menu_cognify_sales_overview" },
                    { label: "Pipeline (Quotations)", xmlid: "cognify_management_reports.menu_cognify_sales_pipeline" },
                    { label: "Sales by Day", xmlid: "cognify_management_reports.menu_cognify_sales_day" },
                    { label: "Sales by Month", xmlid: "cognify_management_reports.menu_cognify_sales_month" },
                    { label: "Sales by Channel", xmlid: "cognify_management_reports.menu_cognify_sales_channel" },
                    { label: "Sales by Category", xmlid: "cognify_management_reports.menu_cognify_sales_category" },
                ],
            },
            { label: "Quotations", xmlid: "vuma_multi_tenant.menu_vuma_link_quote" },
            { label: "Sales Orders", xmlid: "vuma_multi_tenant.menu_vuma_link_orders" },
            {
                label: "Order Picking",
                children: [
                    { label: "Batch Transfers", xmlid: "stock.stock_picking_batch_menu" },
                ],
            },
            {
                label: "Invoicing",
                children: [
                    { label: "Customer Invoices", xmlid: "vuma_multi_tenant.menu_vuma_link_invoices" },
                    { label: "Customer Payments", xmlid: "account.menu_action_account_payments_receivable" },
                    { label: "Awaiting Signature", xmlid: "cognify_invoice_signature.menu_invoices_awaiting_signature" },
                ],
            },
            {
                label: "Deliveries",
                children: [
                    { label: "Delivery Orders", xmlid: "stock.out_picking" },
                    { label: "Returns", xmlid: "sale_stock.menu_stock_picking_return_report" },
                    { label: "Operation Types", xmlid: "stock.stock_picking_type_menu" },
                    { label: "Delivery Methods", xmlid: "stock_delivery.menu_action_delivery_carrier_form" },
                ],
            },
        ],
    },
    {
        key: "debtors",
        label: "Debtors",
        icon: "person",
        appXmlid: "contacts.menu_contacts",
        items: [
            {
                label: "Debtors Dashboard",
                children: [
                    { label: "Outstanding Balances", xmlid: "cognify_management_reports.menu_cognify_customer_balances" },
                    { label: "Credit Available", xmlid: "cognify_management_reports.menu_cognify_credit_available" },
                    { label: "Debt Ageing", xmlid: "cognify_management_reports.menu_cognify_ageing" },
                    { label: "Sales by Customer", xmlid: "cognify_management_reports.menu_cognify_sales_by_customer" },
                    { label: "Invoicing Dashboard", xmlid: "account.menu_board_journal_1" },
                ],
            },
            { label: "Customer Profiles", xmlid: "account.menu_account_customer" },
            { label: "Credit Limits", xmlid: "vuma_retail_ops.menu_vuma_credit_limits" },
            {
                label: "Age Analysis",
                children: [
                    { label: "Debt Ageing", xmlid: "cognify_management_reports.menu_cognify_ageing" },
                    { label: "Outstanding Balances", xmlid: "cognify_management_reports.menu_cognify_customer_balances" },
                    { label: "Credit Available", xmlid: "cognify_management_reports.menu_cognify_credit_available" },
                ],
            },
            { label: "Receive Payments Manually", xmlid: "account.menu_action_account_payments_receivable" },
            {
                label: "Allocate Payments per Company",
                children: [
                    { label: "Payments", xmlid: "vuma_multi_tenant.menu_vuma_link_payments" },
                    { label: "Cash Collections", xmlid: "vuma_multi_tenant.menu_vuma_collect" },
                    { label: "Credit Customer Invoices", xmlid: "vuma_multi_tenant.menu_vuma_credit" },
                ],
            },
            {
                label: "Allocate Discounts Post Invoice",
                children: [
                    { label: "Credit Notes", xmlid: "account.menu_action_move_out_refund_type" },
                    { label: "Discount & Loyalty", xmlid: "sale_loyalty.menu_discount_loyalty_type_config" },
                ],
            },
            {
                label: "Returns and Credits",
                children: [
                    { label: "Returns by Customer and Invoice", xmlid: "customer_goods_pickup.menu_customer_goods_pickup_returns" },
                    { label: "Credit Notes", xmlid: "account.menu_action_move_out_refund_type" },
                    { label: "Goods in Returns Area", xmlid: "customer_goods_pickup.menu_customer_goods_quarantine" },
                ],
            },
            {
                label: "Debtor Documents",
                children: [
                    { label: "Awaiting Signature", xmlid: "cognify_invoice_signature.menu_invoices_awaiting_signature" },
                    { label: "Customer Contacts", xmlid: "vuma_retail_ops.menu_vuma_contacts_customers" },
                ],
            },
        ],
    },
    {
        key: "creditors",
        label: "Creditors",
        icon: "receipt_long",
        appXmlid: "purchase.menu_purchase_root",
        items: [
            {
                label: "Creditors Dashboard",
                children: [
                    { label: "Invoice Analysis", xmlid: "account.menu_action_account_invoice_report_all" },
                    { label: "Purchase Analysis", xmlid: "purchase.purchase_report" },
                    { label: "Cash and Bank Position", xmlid: "cognify_management_reports.menu_cognify_cash" },
                    { label: "Invoicing Dashboard", xmlid: "account.menu_board_journal_1" },
                ],
            },
            { label: "Creditor Profiles", xmlid: "account.menu_account_supplier" },
            {
                label: "Creditors Age Analysis",
                children: [
                    { label: "Invoice Analysis", xmlid: "account.menu_action_account_invoice_report_all" },
                    { label: "Purchase Analysis", xmlid: "purchase.purchase_report" },
                    { label: "Payment Terms", xmlid: "account.menu_action_payment_term_form" },
                ],
            },
            { label: "Unpaid Invoices", xmlid: "account.menu_action_move_in_invoice_type" },
            { label: "Expected Deliveries", xmlid: "purchase.menu_purchase_form_action" },
            { label: "Pay Creditors", xmlid: "account.menu_action_account_payments_payable" },
            {
                label: "Reports on Creditors",
                children: [
                    { label: "Purchase Analysis", xmlid: "purchase.purchase_report" },
                    { label: "Invoice Analysis", xmlid: "account.menu_action_account_invoice_report_all" },
                    { label: "Vendor Bills", xmlid: "vuma_multi_tenant.menu_vuma_all_vendor_bills" },
                ],
            },
            {
                label: "Creditors Documents",
                children: [
                    { label: "Supplier Contacts", xmlid: "vuma_retail_ops.menu_vuma_contacts_suppliers" },
                    { label: "Payment Terms", xmlid: "account.menu_action_payment_term_form" },
                ],
            },
        ],
    },
    {
        key: "accounting",
        label: "Accounting",
        icon: "account_balance",
        appXmlid: "account.menu_finance",
        items: [
            { label: "Accounting Dashboard", xmlid: "account.menu_board_journal_1" },
            { label: "Journal Entries", xmlid: "vuma_multi_tenant.menu_vuma_all_journal_entries" },
            { label: "Journal Items", xmlid: "vuma_multi_tenant.menu_vuma_all_journal_items" },
            {
                label: "Tax Reporting",
                children: [
                    { label: "Invoice Analysis", xmlid: "account.menu_action_account_invoice_report_all" },
                    { label: "Taxes", xmlid: "account.menu_action_tax_form" },
                    { label: "Tax Groups", xmlid: "account.menu_action_tax_group" },
                ],
            },
            {
                label: "Expense Logging",
                children: [
                    { label: "My Expenses", xmlid: "hr_expense.menu_hr_expense_my_expenses_all" },
                    { label: "Expenses to Process", xmlid: "hr_expense.menu_hr_expense_expenses_to_process" },
                    { label: "Expenses Analysis", xmlid: "hr_expense.menu_hr_expense_all_expenses" },
                ],
            },
            {
                label: "Remittances & Cash",
                children: [
                    { label: "Remittances to Verify", xmlid: "cognify_remittance.menu_cognify_remittance_office" },
                    { label: "Cash and Bank Position", xmlid: "cognify_management_reports.menu_cognify_cash" },
                ],
            },
            { label: "Management Reports", xmlid: "cognify_management_reports.menu_cognify_all_reports" },
        ],
    },
];

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
            // Quick Links: menu shortcuts starred by this user, newest first in
            // the panel. Persisted per user in localStorage (see favoritesKey).
            favorites: this.loadFavorites(),
            // Business-tab folder expansion, keyed by the synthetic nav keys
            // built in buildNavItem.
            navFolders: {},
            // Business-tab visibility, keyed by group key. All groups start open.
            bizGroups: {},
            // The legacy grouped app list is kept as a fallback for menus the
            // curated business tabs do not cover; it is revealed on demand.
            showAllApps: false,
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

    // ---------- Quick Links (favorites) ----------

    // Favorites are a per-user browser preference: starring is instant and
    // needs no server round-trip, and a user's phone does not have to agree
    // with their desk. The key carries the user id so two people sharing a
    // till never see each other's shortcuts.
    favoritesKey() {
        return `vuma.sidebar.favorites.v1.${(this.user && this.user.userId) || 0}`;
    }

    loadFavorites() {
        try {
            const raw = localStorage.getItem(this.favoritesKey());
            if (!raw) {
                return [];
            }
            const list = JSON.parse(raw);
            if (!Array.isArray(list)) {
                return [];
            }
            return list.filter((f) => f && typeof f === "object" && (f.xmlid || f.id));
        } catch (e) {
            return [];
        }
    }

    saveFavorites() {
        try {
            localStorage.setItem(this.favoritesKey(), JSON.stringify(this.state.favorites));
        } catch (e) {
            /* private browsing / quota: favorites stay in memory for this tab */
        }
    }

    // A stable storage key for a menu. xmlid survives menu re-creation; the id
    // fallback covers menus created from the UI that have no module data.
    favoriteKeyFor(menu) {
        if (!menu) {
            return "";
        }
        return menu.xmlid || `id:${menu.id}`;
    }

    isFavoriteMenu(menu) {
        const key = this.favoriteKeyFor(menu);
        return !!key && this.state.favorites.some((f) => (f.xmlid || `id:${f.id}`) === key);
    }

    isFavoriteItem(item) {
        return !!(item && item.menu && this.isFavoriteMenu(item.menu));
    }

    toggleFavoriteMenu(menu, label, path) {
        if (!menu || !menu.actionID) {
            return;
        }
        const key = this.favoriteKeyFor(menu);
        if (!key) {
            return;
        }
        const list = this.state.favorites.slice();
        const index = list.findIndex((f) => (f.xmlid || `id:${f.id}`) === key);
        if (index >= 0) {
            list.splice(index, 1);
        } else {
            list.push({
                xmlid: menu.xmlid || "",
                id: menu.id,
                label: label || menu.name,
                path: path !== undefined && path !== null ? path : this.menuPath(menu),
            });
        }
        this.state.favorites = list;
        this.saveFavorites();
    }

    toggleFavoriteItem(item) {
        if (item && item.menu) {
            this.toggleFavoriteMenu(item.menu, item.label, item.path);
        }
    }

    // Resolve stored favorites against the live menu tree. Entries whose menu
    // was removed, or that the current user may no longer see, are skipped
    // rather than rendered as dead rows.
    favoriteEntries() {
        const { byId, byXmlid } = this._menuMaps();
        const entries = [];
        const seen = new Set();
        for (const fav of this.state.favorites) {
            const menu = (fav.xmlid && byXmlid[fav.xmlid]) || byId[fav.id];
            if (!menu || !menu.actionID) {
                continue;
            }
            const key = this.favoriteKeyFor(menu);
            if (seen.has(key)) {
                continue;
            }
            seen.add(key);
            entries.push({
                key,
                menu,
                label: fav.label || menu.name,
                path: fav.path || "",
            });
        }
        return entries;
    }

    openFavorite(fav) {
        this.selectMenu(fav.menu);
    }

    // ---------- Business tabs ----------

    _menuMaps() {
        const byId = {};
        const byXmlid = {};
        const parentOf = {};
        for (const menu of this.menuService.getAll()) {
            byId[menu.id] = menu;
            if (menu.xmlid) {
                byXmlid[menu.xmlid] = menu;
            }
            for (const childId of menu.children || []) {
                parentOf[childId] = menu.id;
            }
        }
        return { byId, byXmlid, parentOf };
    }

    _menuPath(menu, maps) {
        if (!menu) {
            return "";
        }
        // A root application is its own path: showing "Settings / Settings" in
        // Quick Links adds nothing. Only descendants get a breadcrumb.
        const parentId = maps.parentOf[menu.id];
        if (!parentId || parentId === "root") {
            return "";
        }
        const parts = [];
        let current = menu;
        let guard = 0;
        while (current && current.id !== "root" && guard < 12) {
            parts.unshift(current.name);
            current = maps.parentOf[current.id] ? maps.byId[maps.parentOf[current.id]] : null;
            guard += 1;
        }
        return parts.join(" / ");
    }

    menuPath(menu) {
        try {
            return this._menuPath(menu, this._menuMaps());
        } catch (e) {
            return "";
        }
    }

    // Turn the declarative BUSINESS_GROUPS config into render data, resolving
    // each leaf against the user's visible menus. Folders with no resolvable
    // children disappear so a missing module cannot leave an empty tab.
    buildNavItem(item, baseKey, maps, parentPath) {
        // Curated breadcrumb: "Debtors Dashboard / Outstanding Balances". It is
        // what a person sees in the sidebar, so it is also what search must
        // match — several leaves have menu names that came from the accounting
        // module and never say "debtors" anywhere.
        const curatedPath = parentPath ? `${parentPath} / ${item.label}` : item.label;
        if (item.children) {
            const children = item.children
                .map((child, index) => this.buildNavItem(child, `${baseKey}-${index}`, maps, curatedPath))
                .filter(Boolean);
            if (!children.length) {
                return null;
            }
            return {
                key: baseKey,
                label: item.label,
                icon: item.icon || "",
                menu: null,
                path: curatedPath,
                children,
            };
        }
        const menu = maps.byXmlid[item.xmlid];
        if (!menu || !menu.actionID) {
            return null;
        }
        const odooPath = this._menuPath(menu, maps);
        return {
            key: baseKey,
            label: item.label,
            icon: item.icon || "",
            menu,
            // The Odoo path is what Quick Links shows, because it says where
            // the menu actually lives in the webclient.
            path: odooPath,
            // Search haystack: curated label, the tab it sits under, the real
            // menu name and the Odoo path, lowercased once here instead of on
            // every keystroke.
            searchText: `${curatedPath} ${menu.name} ${odooPath}`.toLowerCase(),
            // Where the item sits in the Vuma tabs; search results show this as
            // the caption because "Debtors Dashboard" means more to the user
            // than "Management / Customer Balances".
            context: parentPath,
            children: null,
        };
    }

    businessGroups() {
        const maps = this._menuMaps();
        const appsByXmlid = {};
        for (const app of this.allApps) {
            if (app.xmlid) {
                appsByXmlid[app.xmlid] = app;
            }
        }
        return BUSINESS_GROUPS.map((group) => ({
            key: group.key,
            label: group.label,
            icon: group.icon,
            // Same brand icon as this app wears in the All Apps list, so the
            // two views read as one product.
            app: appsByXmlid[group.appXmlid] || null,
            items: group.items
                .map((item, index) => this.buildNavItem(item, `${group.key}-${index}`, maps, ""))
                .filter(Boolean),
        })).filter((group) => group.items.length > 0);
    }

    isBusinessGroupOpen(key) {
        const value = this.state.bizGroups[key];
        return value === undefined ? true : value;
    }

    toggleBusinessGroup(key) {
        this.state.bizGroups[key] = !this.isBusinessGroupOpen(key);
    }

    isNavItemOpen(item) {
        return !!this.state.navFolders[item.key];
    }

    toggleNavItem(item) {
        this.state.navFolders[item.key] = !this.isNavItemOpen(item);
    }

    toggleAllApps() {
        this.state.showAllApps = !this.state.showAllApps;
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
        const seen = new Set();
        const push = (entry) => {
            const id = entry.menu ? `m${entry.menu.id}` : `a${entry.app.id}`;
            const key = `${id}::${entry.label || ""}`;
            if (seen.has(key)) {
                return;
            }
            seen.add(key);
            out.push(entry);
        };
        // Curated business-tab labels first, so "Debtors Dashboard" and the
        // like are findable even though they are not Odoo menu names. The
        // folder itself has no action to navigate to, so a folder hit surfaces
        // its children instead — each carries the folder name in searchText.
        const walkNav = (items) => {
            for (const item of items) {
                if (item.children) {
                    walkNav(item.children);
                    continue;
                }
                if (item.searchText.includes(q)) {
                    push({
                        type: "nav",
                        app: null,
                        menu: item.menu,
                        label: item.label,
                        path: item.context || item.path,
                    });
                }
            }
        };
        for (const group of this.businessGroups()) {
            walkNav(group.items);
        }
        for (const app of this.allApps) {
            if (app.name.toLowerCase().includes(q)) {
                push({ type: "app", app, menu: null, label: app.name, path: "" });
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
                            push({
                                type: "menu",
                                app,
                                menu: child,
                                label: child.name,
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

    // ---------- Company scope ----------

    // The same scopes as the navbar company switcher. Living in the sidebar
    // as well makes the current company impossible to miss: every list, chart
    // and capture form follows it.
    get companyScopes() {
        try {
            const allowed = this.user.allowedCompanies || [];
            const trade = allowed.find((company) => company.name === "Trade Rite");
            const siyaya = allowed.find((company) => company.name === "Siyaya DC");
            const scopes = [];
            if (trade) {
                scopes.push({
                    id: "tr",
                    label: trade.name,
                    icon: "business",
                    dotStyle: this.companyDotStyle([trade]),
                });
            }
            if (siyaya) {
                scopes.push({
                    id: "sy",
                    label: siyaya.name,
                    icon: "local_shipping",
                    dotStyle: this.companyDotStyle([siyaya]),
                });
            }
            if (trade && siyaya) {
                scopes.push({
                    id: "both",
                    label: "Both Companies",
                    icon: "apps",
                    hint: "Trade Rite + Siyaya DC — for cross-company orders and payments",
                    dotStyle: this.companyDotStyle([trade, siyaya]),
                });
            }
            if (allowed.length > 1) {
                scopes.push({
                    id: "all",
                    label: "All Companies",
                    icon: "public",
                    dotStyle: this.companyDotStyle(allowed),
                });
            }
            return scopes;
        } catch (e) {
            return [];
        }
    }

    companyDotStyle(companies) {
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

    get activeCompanyScope() {
        try {
            const active = this.user.activeCompanies.map((company) => company.id);
            const allowed = this.user.allowedCompanies.map((company) => company.id);
            const trade = this.user.allowedCompanies.find((c) => c.name === "Trade Rite");
            const siyaya = this.user.allowedCompanies.find((c) => c.name === "Siyaya DC");
            const bothActive =
                trade && siyaya && active.includes(trade.id) && active.includes(siyaya.id);
            const onlyTradingActive =
                bothActive &&
                active.every((id) => id === trade.id || id === siyaya.id);
            if (onlyTradingActive) {
                return "both";
            }
            if (allowed.length && allowed.every((id) => active.includes(id))) {
                return "all";
            }
            if (trade && active.length === 1 && active[0] === trade.id) {
                return "tr";
            }
            if (siyaya && active.length === 1 && active[0] === siyaya.id) {
                return "sy";
            }
        } catch (e) {
            /* fall through */
        }
        return "custom";
    }

    get companyScopeLabel() {
        const scope = this.companyScopes.find((s) => s.id === this.activeCompanyScope);
        if (scope) {
            return scope.label;
        }
        return this.user.activeCompany ? this.user.activeCompany.name : "Select company";
    }

    get companyScopeIcon() {
        const scope = this.companyScopes.find((s) => s.id === this.activeCompanyScope);
        return scope ? scope.icon : "business";
    }

    selectCompanyScope(scopeId) {
        const trade = this.user.allowedCompanies.find((c) => c.name === "Trade Rite");
        const siyaya = this.user.allowedCompanies.find((c) => c.name === "Siyaya DC");
        const allIds = this.user.allowedCompanies.map((company) => company.id);
        const targetIds = {
            all: allIds,
            both: trade && siyaya ? [trade.id, siyaya.id] : null,
            tr: trade ? [trade.id] : null,
            sy: siyaya ? [siyaya.id] : null,
        }[scopeId];
        if (!targetIds || !targetIds.length) {
            return;
        }
        this.closeMobile();
        Promise.resolve(
            this.user.activateCompanies(targetIds, {
                includeChildCompanies: false,
                reload: true,
            })
        ).catch(() => {
            // A failed scope change must never take the sidebar down.
        });
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