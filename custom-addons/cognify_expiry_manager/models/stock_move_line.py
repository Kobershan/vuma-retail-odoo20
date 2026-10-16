# -*- coding: utf-8 -*-
import datetime

from odoo import _, api, fields, models


def _midnight(date_value):
    """Turn the PO line's plain ``Date`` into the ``Datetime`` the lot stores.

    Expiry dates are day-granular but ``stock.lot.expiration_date`` is a
    timestamp, so the supplier's date lands at midnight rather than at whatever
    time the receipt happened to be validated.
    """
    return datetime.datetime.combine(date_value, datetime.time.min)


class StockMoveLine(models.Model):
    _inherit = 'stock.move.line'

    cognify_expiration_override = fields.Datetime(
        string='Expiry Date Override',
        copy=False,
        help="Set this only to correct the date that will be stored on the lot. "
             "Normally leave it empty: the supplier's expiry date from the purchase "
             "order is used, falling back to the product's shelf life.")
    cognify_incoming_expiration_date = fields.Datetime(
        string='Expiry Date on Receipt',
        compute='_compute_cognify_incoming_expiration_date',
        help="The date that will land on the lot when this operation is validated.")

    @api.depends('move_id.purchase_line_id.supplier_expiration_date',
                 'cognify_expiration_override',
                 'expiration_date')
    def _compute_cognify_incoming_expiration_date(self):
        """Show the receipt screen which expiry date the lot is going to get."""
        for move_line in self:
            date = move_line._cognify_resolved_expiration_date()
            move_line.cognify_incoming_expiration_date = date

    def _cognify_resolved_expiration_date(self):
        """The expiry date that should win for this operation.

        Precedence, highest first:

        1. ``cognify_expiration_override`` — typed on the operation to correct
           the date after a mistype,
        2. the supplier's date from the purchase order line,
        3. ``expiration_date`` — Odoo's own shelf-life calculation.

        ``expiration_date`` is a stored read-only compute, so on a brand new lot
        its value is always (3) and never something a user typed. That is what
        makes it safe for (1) and (2) to win over it.
        """
        self.ensure_one()
        if self.cognify_expiration_override:
            return self.cognify_expiration_override
        supplier_date = self.move_id.purchase_line_id.supplier_expiration_date
        if supplier_date:
            return _midnight(supplier_date)
        return self.expiration_date

    def _prepare_new_lot_vals(self):
        """Stamp the lot being created with the agreed expiry date.

        ``product_expiry`` copies ``expiration_date`` onto the new lot, which on
        a brand new lot is the shelf-life calculation. When the purchase order
        carries the supplier's printed date — or the clerk typed a correction —
        that date has to win, because it is the date on the physical label.

        Only new lots are touched: this method runs from
        ``_create_and_assign_production_lot``, which by definition only creates
        lots that do not exist yet, so re-receiving never restamps a live lot.
        """
        vals = super()._prepare_new_lot_vals()
        date = self._cognify_resolved_expiration_date()
        if date:
            vals['expiration_date'] = date
        return vals


class StockPicking(models.Model):
    _inherit = 'stock.picking'

    has_expiring_lines = fields.Boolean(
        string='Has Expiry Dates',
        compute='_compute_has_expiring_lines',
        help="This transfer carries goods with an expiry date, so its slip will "
             "show the expiry column.")

    @api.depends('move_line_ids.cognify_incoming_expiration_date')
    def _compute_has_expiring_lines(self):
        for picking in self:
            picking.has_expiring_lines = bool(
                picking.move_line_ids.filtered(
                    lambda ml: ml.cognify_incoming_expiration_date))