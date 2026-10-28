/** @odoo-module **/
/**
 * Large-button picking screen for warehouse tablets.
 *
 * The backend form view is fine for an office, but a picker walking a warehouse
 * needs three things: a big product name, a big quantity input and a button
 * that is easy to hit with a thumb. Every tap is written to the saved slip
 * immediately, so putting the tablet down or losing the browser tab does not
 * lose the quantities captured so far.
 */
import { Component, onWillStart, useProps, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { _t } from "@web/core/l10n/translation";
import { standardActionServiceProps } from "@web/webclient/actions/action_plugin";

const SLIP_FIELDS = [
    "id",
    "name",
    "date",
    "state",
    "user_id",
    "note",
    "invoice_count",
    "line_count",
    "picked_count",
    "short_count",
    "finished_by_id",
    "finished_date",
];

const LINE_FIELDS = [
    "id",
    "sequence",
    "product_id",
    "product_uom_id",
    "demand_qty",
    "picked_qty",
    "is_picked",
    "source_company_id",
    "source_label",
    "product_barcode",
];

const STATE_LABELS = {
    draft: _t("Draft"),
    in_progress: _t("Picking"),
    done: _t("Done"),
};

export class PickingSlipTablet extends Component {
    static template = "vuma_picking_slip.Tablet";
    props = useProps(standardActionServiceProps);

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");

        this.state = useState({
            loading: true,
            error: null,
            slip: null,
            lines: [],
            pending: 0,
        });

        onWillStart(() => this.load());
    }

    get slipId() {
        return (
            this.props.action.params?.slip_id ||
            this.props.action.context?.slip_id ||
            false
        );
    }

    get stateLabel() {
        return STATE_LABELS[this.state.slip?.state] || "";
    }

    get pickedCount() {
        return this.state.lines.filter((line) => line.is_picked).length;
    }

    get shortCount() {
        return this.state.lines.filter((line) => this.isShort(line)).length;
    }

    get progress() {
        const total = this.state.lines.length;
        return total ? Math.round((this.pickedCount * 100) / total) : 0;
    }

    get saving() {
        return this.state.pending > 0;
    }

    isShort(line) {
        return Boolean(line.is_picked && line.picked_qty < line.demand_qty);
    }

    remaining(line) {
        return Math.max(line.demand_qty - line.picked_qty, 0);
    }

    formatQty(value) {
        const number = Number(value || 0);
        return number.toLocaleString(undefined, { maximumFractionDigits: 3 });
    }

    errorMessage(error) {
        return error?.data?.message || error?.message || String(error);
    }

    async load(silent = false) {
        if (!silent) {
            this.state.loading = true;
            this.state.error = null;
        }
        try {
            const slipId = this.slipId;
            if (!slipId) {
                throw new Error(_t("No picking slip was selected."));
            }
            const [slip] = await this.orm.read(
                "vuma.picking.slip",
                [slipId],
                SLIP_FIELDS
            );
            if (!slip) {
                throw new Error(_t("This picking slip no longer exists."));
            }
            const lines = await this.orm.searchRead(
                "vuma.picking.slip.line",
                [["slip_id", "=", slipId]],
                LINE_FIELDS,
                { order: "sequence, id" }
            );
            this.state.slip = slip;
            this.state.lines = lines;
        } catch (error) {
            if (silent) {
                this.notification.add(this.errorMessage(error), {
                    type: "danger",
                });
            } else {
                this.state.error = this.errorMessage(error);
            }
        } finally {
            if (!silent) {
                this.state.loading = false;
            }
        }
    }

    async writeLine(line, values) {
        // Update the screen first so a picker can tap the next line without
        // waiting for the round trip. If the server rejects the write, the
        // lines are reloaded from Odoo and the local edit disappears.
        Object.assign(line, values);
        if (this.state.slip.state === "draft") {
            this.state.slip.state = "in_progress";
        } else if (this.state.slip.state === "done" && values.is_picked === false) {
            this.state.slip.state = "in_progress";
        }
        this.state.pending += 1;
        try {
            await this.orm.write("vuma.picking.slip.line", [line.id], values);
        } catch (error) {
            this.notification.add(this.errorMessage(error), {
                type: "danger",
            });
            await this.load(true);
        } finally {
            this.state.pending -= 1;
        }
    }

    async togglePicked(line) {
        if (line.is_picked) {
            await this.writeLine(line, { is_picked: false });
            return;
        }
        const values = { is_picked: true };
        // Tapping "Picked" on an untouched line means "I found all of it".
        // A picker who found fewer taps the quantity down afterwards.
        if (!line.picked_qty) {
            values.picked_qty = line.demand_qty;
        }
        await this.writeLine(line, values);
    }

    async changeQty(line, delta) {
        const next = Math.max(
            0,
            Math.round((Number(line.picked_qty || 0) + delta) * 1000) / 1000
        );
        if (next === Number(line.picked_qty || 0)) {
            return;
        }
        await this.writeLine(line, { picked_qty: next });
    }

    async onQtyChange(line, ev) {
        let value = Number(ev.target.value);
        if (!Number.isFinite(value) || value < 0) {
            value = 0;
            ev.target.value = 0;
        }
        if (value === Number(line.picked_qty || 0)) {
            return;
        }
        await this.writeLine(line, { picked_qty: value });
    }

    async markAll() {
        try {
            await this.orm.call(
                "vuma.picking.slip",
                "action_mark_all_picked",
                [[this.slipId]]
            );
            await this.load(true);
            this.notification.add(_t("Every line is marked picked."), {
                type: "success",
            });
        } catch (error) {
            this.notification.add(this.errorMessage(error), {
                type: "warning",
            });
        }
    }

    async finish() {
        try {
            await this.orm.call("vuma.picking.slip", "action_finish", [
                [this.slipId],
            ]);
            await this.load(true);
            this.notification.add(_t("Picking finished."), {
                type: "success",
            });
        } catch (error) {
            this.notification.add(this.errorMessage(error), {
                type: "warning",
            });
        }
    }

    async reopen() {
        try {
            await this.orm.call("vuma.picking.slip", "action_reopen", [
                [this.slipId],
            ]);
            await this.load(true);
            this.notification.add(_t("Picking slip reopened."), {
                type: "info",
            });
        } catch (error) {
            this.notification.add(this.errorMessage(error), {
                type: "danger",
            });
        }
    }

    async printPdf() {
        try {
            const reportAction = await this.orm.call(
                "vuma.picking.slip",
                "action_print_pdf",
                [[this.slipId]]
            );
            await this.action.doAction(reportAction);
        } catch (error) {
            this.notification.add(this.errorMessage(error), {
                type: "danger",
            });
        }
    }

    async openForm() {
        await this.action.doAction({
            type: "ir.actions.act_window",
            name: this.state.slip?.name || _t("Picking Slip"),
            res_model: "vuma.picking.slip",
            res_id: this.slipId,
            view_mode: "form",
            views: [[false, "form"]],
            target: "current",
        });
    }

    async backToList() {
        await this.action.doAction("vuma_picking_slip.action_picking_slip");
    }
}

registry.category("actions").add("vuma_picking_slip.tablet", PickingSlipTablet);
