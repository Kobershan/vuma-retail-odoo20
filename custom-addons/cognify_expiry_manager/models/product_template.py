# -*- coding: utf-8 -*-
import datetime

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from ..expiry_period import EXPIRY_ALERT_UNITS, alert_date_from_expiration, period_description


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    expiry_alert_value = fields.Integer(
        string='Alert Lead Time',
        default=0,
        help="How far ahead of the expiry date this product should raise an alert. "
             "Pick the unit next to it — some goods only matter a day before they "
             "expire, others need warning three months in advance.")
    expiry_alert_unit = fields.Selection(
        EXPIRY_ALERT_UNITS, string='Alert Unit', default='days',
        required=True,
        help="Unit the alert lead time is counted in. Months and years follow the calendar.")
    expiry_alert_window = fields.Char(
        string='Alert Window', compute='_compute_expiry_alert_window',
        help="Read-only summary of the alert window, e.g. '3 Months'.")

    # FEFO helper. Odoo's FEFO strategy sorts on the lot's *removal* date, which
    # is the expiry date moved back by `removal_time`. Keeping removal_time at 0
    # makes FEFO mean exactly "closest expiry date goes out first", which is what
    # a shop floor expects when it is told FEFO.
    fefo_expiration_date = fields.Boolean(
        string='Pick by Closest Expiry',
        default=False,
        help="When enabled, this product's removal date is kept equal to its expiry "
             "date, so picking by FEFO really does send out the goods closest to "
             "expiry first. Turn this off if you need a separate "
             "'pull off the shelf' date that sits before the expiry date.")

    def _compute_expiry_alert_window(self):
        for template in self:
            template.expiry_alert_window = period_description(
                template.expiry_alert_value, template.expiry_alert_unit)

    def _expiry_alert_date(self, expiration_date):
        """Return the alert date for one of this template's products.

        Falls back to the standard ``alert_time`` day count when the
        value/unit pair has not been filled in, so lots created before this
        module was installed keep the behaviour the user already had.

        :param datetime.datetime expiration_date: the lot's expiry date
        :return: datetime.datetime or False when the product has no alert
        """
        self.ensure_one()
        if self.expiry_alert_value and self.expiry_alert_unit:
            return alert_date_from_expiration(
                expiration_date, self.expiry_alert_value, self.expiry_alert_unit)
        if self.alert_time:
            return expiration_date - datetime.timedelta(days=self.alert_time)
        return False

    @api.constrains('expiry_alert_value')
    def _check_expiry_alert_value(self):
        for template in self:
            if template.expiry_alert_value < 0:
                raise ValidationError(
                    _("The alert lead time on %(product)s cannot be negative.",
                      product=template.display_name))

    def write(self, vals):
        """Re-arm the alert window of existing lots when the window changes.

        ``stock.lot`` keeps a flag saying "this lot has already been alerted".
        If the user widens or narrows the window we must clear it for lots that
        newly fall inside — or fall outside — it, otherwise nobody would ever be
        told about them.
        """
        res = super().write(vals)
        if {'expiry_alert_value', 'expiry_alert_unit'} & set(vals):
            self.filtered('use_expiration_date')._rearm_expiry_lots()
        return res

    def _rearm_expiry_lots(self):
        """Clear the reminded flag on lots of these products that still hold stock."""
        self.env['stock.lot']._reset_expiry_alert_flags(self.product_variant_ids)

    def action_apply_fefo(self):
        """Make these products' category pick the closest expiry date first.

        Sets the FEFO removal strategy on the product category and zeroes the
        removal lead time so the removal date and the expiry date coincide.
        """
        for template in self:
            fefo = self.env['product.removal'].search([('method', '=', 'fefo')], limit=1)
            if not fefo:
                # `product_expiry` seeds this record; fall back to its xml id.
                fefo = self.env.ref('product_expiry.removal_fefo', raise_if_not_found=False)
            if not fefo:
                raise UserError(_("The FEFO removal strategy is missing. Please upgrade the "
                                  "'Products Expiration Date' (product_expiry) module first."))
            template.categ_id.removal_strategy_id = fefo
        self.filtered('fefo_expiration_date').write({'removal_time': 0})
        return True

    def action_view_expiry_lots(self):
        """Open the lots of these products that are approaching or past expiry."""
        self.ensure_one()
        return {
            'name': _('Expiring lots for %s', self.display_name),
            'type': 'ir.actions.act_window',
            'res_model': 'stock.lot',
            'views': [
                (self.env.ref('cognify_expiry_manager.stock_lot_view_expiry_tree').id, 'list'),
                (self.env.ref('stock.view_production_lot_form').id, 'form'),
            ],
            'view_mode': 'list,form',
            'domain': [
                ('product_id', 'in', self.product_variant_ids.ids),
                ('product_qty', '>', 0),
                ('expiration_date', '!=', False),
                '|', ('alert_date', '<=', fields.Datetime.now()),
                     ('expiration_date', '<=', fields.Datetime.now()),
            ],
            'context': {'create': False},
        }


class ProductProduct(models.Model):
    """Variant-side entry points for the expiry buttons.

    ``product.product`` inherits the template's *fields* through
    ``_inherits`` but not its methods.  The expiry form is inherited onto the
    product variant form as well, so the buttons must exist here or Odoo
    rejects the view with "action_apply_fefo is not a valid action on
    product.product".  Both actions are category- or template-level, so they
    delegate to the template.
    """

    _inherit = 'product.product'

    def action_apply_fefo(self):
        self.ensure_one()
        return self.product_tmpl_id.action_apply_fefo()

    def action_view_expiry_lots(self):
        self.ensure_one()
        return self.product_tmpl_id.action_view_expiry_lots()
