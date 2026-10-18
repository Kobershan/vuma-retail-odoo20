/** @odoo-module **/

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { View } from "@web/views/view";

/**
 * Every Management report on one scrollable page.
 *
 * Each card embeds an ordinary Odoo view by its real view id and reuses the
 * domain/context of the matching `ir.actions.act_window`, so the numbers are
 * exactly what the individual report shows - nothing is recomputed here.
 */

// Confirmed order lines only. Quotation states are handled per-report.
const CONFIRMED = [["state", "not in", ["draft", "sent", "cancel"]]];

const SALE_CTX = (measures, rowGroupBy, extra = {}) => ({
    pivot_measures: measures,
    pivot_row_group_by: rowGroupBy,
    pivot_column_group_by: [],
    ...extra,
});

const SECTIONS = [
    {
        key: "sales",
        title: "Sales",
        subtitle: "Revenue, margin and mix — read from confirmed invoices",
        reports: [
            {
                title: "Sales by Month",
                note: "Untaxed total per month",
                resModel: "sale.report",
                type: "graph",
                viewId: 3956,
                domain: CONFIRMED,
                context: SALE_CTX(
                    ["price_subtotal", "margin", "margin_percent", "product_uom_qty"],
                    ["date:month"],
                    { graph_measure: "price_subtotal" }
                ),
            },
            {
                title: "Sales by Category",
                note: "Where the revenue comes from",
                resModel: "sale.report",
                type: "graph",
                viewId: 3957,
                domain: CONFIRMED,
                context: SALE_CTX(["price_subtotal", "margin_percent"], ["categ_id"]),
            },
            {
                title: "Sales by Channel",
                note: "Split by sales team",
                resModel: "sale.report",
                type: "graph",
                viewId: 2954,
                domain: CONFIRMED,
                context: SALE_CTX(["price_subtotal", "margin"], ["team_id"]),
            },
            {
                title: "Salesperson Performance",
                note: "Per rep, per month",
                resModel: "sale.report",
                type: "graph",
                viewId: 2955,
                domain: CONFIRMED,
                context: SALE_CTX(["price_subtotal", "margin"], ["user_id"]),
            },
            {
                title: "Cost of Sales and Margin",
                note: "Line detail",
                resModel: "sale.report",
                type: "list",
                viewId: 2956,
                domain: CONFIRMED,
                context: SALE_CTX(
                    ["price_subtotal", "purchase_price", "margin", "margin_percent"],
                    ["categ_id"]
                ),
            },
            {
                title: "Pipeline (Quotations)",
                note: "Not yet invoiced",
                resModel: "sale.report",
                type: "list",
                viewId: 2956,
                domain: [["state", "in", ["draft", "sent"]]],
                context: SALE_CTX(["price_subtotal", "product_uom_qty"], ["user_id"]),
            },
        ],
    },
    {
        key: "customers",
        title: "Customers",
        subtitle: "Who owes what, and how overdue it is",
        reports: [
            {
                title: "Customer Balances",
                resModel: "cognify.customer.credit",
                type: "list",
                viewId: 3941,
                context: { search_default_filter_owing: 1 },
            },
            {
                title: "Debt Ageing",
                note: "Buckets at 30 / 60 / 90+ days",
                resModel: "cognify.customer.ageing",
                type: "graph",
                viewId: 3992,
            },
        ],
    },
    {
        key: "finance",
        title: "Finance and Cash",
        subtitle: "What moved through the accounts",
        reports: [
            {
                title: "Cash and Bank Position",
                resModel: "cognify.cash.position",
                type: "graph",
                viewId: 3993,
            },
            {
                title: "Expense Spend",
                note: "Claimed expenses as a share of revenue",
                resModel: "cognify.expense.report",
                type: "graph",
                viewId: 3996,
            },
        ],
    },
    {
        key: "stock",
        title: "Stock",
        subtitle: "Value at cost and retail, dead and running out",
        reports: [
            {
                title: "Stock Health and Dead Stock",
                resModel: "cognify.stock.health",
                type: "graph",
                viewId: 3994,
            },
            {
                title: "Dead and Slow Moving Stock",
                resModel: "cognify.stock.health",
                type: "list",
                viewId: 3964,
                domain: [["is_dead", "=", true]],
            },
        ],
    },
    {
        key: "people",
        title: "Targets and People",
        subtitle: "Targets against actuals, hours, leave and utilisation",
        reports: [
            {
                title: "Achievement Against Target",
                resModel: "cognify.rep.achievement",
                type: "graph",
                viewId: 3995,
            },
            {
                title: "Timesheet Utilisation",
                note: "Billable share, cost against revenue",
                resModel: "cognify.utilisation.report",
                type: "graph",
                viewId: 3997,
            },
            {
                title: "Sales Targets",
                resModel: "cognify.sales.target",
                type: "list",
                viewId: 3967,
            },
            {
                title: "Staff Hours and Time Off",
                resModel: "cognify.staff.report",
                type: "list",
                viewId: 3945,
                context: { search_default_filter_this_year: 1 },
            },
        ],
    },
];

export class ManagementAllReports extends Component {
    static template = "cognify_management.AllReports";
    static components = { View };

    setup() {
        this.sections = SECTIONS.map((section) => ({
            ...section,
            reports: section.reports.map((r) => ({
                ...r,
                viewProps: {
                    resModel: r.resModel,
                    type: r.type,
                    viewId: r.viewId,
                    domain: r.domain || [],
                    context: { ...(r.context || {}) },
                    // Skip action menus and saved filters: they are per-report
                    // chrome that would clutter a stacked dashboard.
                    loadActionMenus: false,
                    loadIrFilters: false,
                    display: {},
                },
            })),
        }));
    }

    /** Total number of embedded reports, for the header line. */
    get reportCount() {
        return this.sections.reduce((n, s) => n + s.reports.length, 0);
    }
}

registry.category("actions").add("cognify_management.all_reports", ManagementAllReports);
