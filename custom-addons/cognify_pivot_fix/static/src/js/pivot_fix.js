import { patch } from "@web/core/utils/patch";
import { PivotModel } from "@web/views/pivot/pivot_model";

/**
 * Odoo's `PivotModel._getMeasureSpecs` throws when a measure has no
 * aggregator:
 *
 *     if (field.aggregator === undefined) {
 *         throw new Error("No aggregate function has been provided ...");
 *     }
 *
 * Core `sale_margin` declares `sale.report.margin_percent` with
 * `aggregator=None` on purpose, because a percentage share must not be
 * summed. That declaration survives to the client as JSON `null`, so the
 * check trips and the pivot refuses to render instead of guessing.
 *
 * This patch normalises the known percentage measures before the pivot asks
 * for them. `null` and `undefined` are both treated as "no aggregator", and a
 * measure missing from the metadata entirely is added, so the fallback holds
 * regardless of how the field description was serialised.
 *
 * The aggregation chosen for each field matches how it is computed upstream:
 * `margin_percent` on `sale.report` is a `MAX(...)` in SQL, and the custom
 * report shares are per-row percentages, so the group value is their average.
 */
const PERCENT_MEASURES = {
    margin_percent: "avg",
    overdue_percent: "avg",
    billable_percent: "avg",
    attainment: "avg",
    units_attainment: "avg",
    effective_rate: "avg",
    expense_ratio: "avg",
    timesheet_coverage: "avg",
};

function hasAggregator(field) {
    return field && field.aggregator !== undefined && field.aggregator !== null;
}

patch(PivotModel.prototype, {
    _getMeasureSpecs(config) {
        const fields = config?.metaData?.fields;
        const activeMeasures = config?.metaData?.activeMeasures;
        if (fields && Array.isArray(activeMeasures)) {
            for (const measure of activeMeasures) {
                if (measure === "__count" || !PERCENT_MEASURES[measure]) {
                    continue;
                }
                const field = fields[measure];
                if (!hasAggregator(field)) {
                    fields[measure] = { ...(field ?? {}), aggregator: PERCENT_MEASURES[measure] };
                }
            }
        }
        return super._getMeasureSpecs(config);
    },
});

/**
 * Clipboard fallback for non-secure contexts.
 *
 * `navigator.clipboard.writeText` is only available in secure contexts
 * (HTTPS or localhost). When Odoo is accessed over plain HTTP on a LAN IP
 * (e.g. http://192.168.8.116:8069), `navigator.clipboard` is undefined and
 * the "Copy to Clipboard" button on error dialogs throws:
 *
 *     TypeError: Cannot read properties of undefined (reading 'writeText')
 *
 * This patch adds a fallback using the legacy `document.execCommand('copy')`
 * API so the button works in all contexts.
 */
import { ErrorDialog } from "@web/core/errors/error_dialogs";

patch(ErrorDialog.prototype, {
    onClickClipboard() {
        const text = this.props.message;
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(text);
        }
        // Fallback for non-secure contexts (HTTP on LAN IP)
        const textarea = document.createElement("textarea");
        textarea.value = text;
        textarea.style.position = "fixed";
        textarea.style.opacity = "0";
        document.body.appendChild(textarea);
        textarea.select();
        try {
            document.execCommand("copy");
        } catch (e) {
            // Silent fail — nothing else we can do
        }
        document.body.removeChild(textarea);
        return Promise.resolve();
    },
});
