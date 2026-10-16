# -*- coding: utf-8 -*-
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    supplier_expiration_date = fields.Date(
        string='Supplier Expiry Date',
        company_dependent=True,
        help="Expiry date printed by the supplier on this line's goods.\n"
             "When the receipt is validated this date is carried onto the lot "
             "created for it, so the system always shows the date that is on the "
             "physical label.\n"
             "Leave it empty to let Odoo calculate the date from the product's "
             "shelf life instead.")
    use_expiration_date = fields.Boolean(related='product_id.use_expiration_date', readonly=True)
    expiry_alert_window = fields.Char(
        string='Alert Window', compute='_compute_expiry_alert_window',
        help="How long before the expiry date the buyer will be warned about this line.")

    @api.depends('product_id', 'product_id.expiry_alert_value', 'product_id.expiry_alert_unit')
    def _compute_expiry_alert_window(self):
        for line in self:
            line.expiry_alert_window = line.product_id.product_tmpl_id.expiry_alert_window

    @api.constrains('supplier_expiration_date')
    def _check_supplier_expiration_date(self):
        """Catch a mistyped year before the goods even arrive."""
        today = fields.Date.today()
        for line in self:
            if line.supplier_expiration_date and line.supplier_expiration_date < today:
                raise ValidationError(_(
                    "The supplier expiry date on the line for %(product)s is %(date)s, "
                    "which is already in the past. Goods cannot be received already "
                    "expired — please check the date printed on the supplier's label.",
                    product=line.product_id.display_name,
                    date=line.supplier_expiration_date))