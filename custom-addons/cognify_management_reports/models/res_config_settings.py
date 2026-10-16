# -*- coding: utf-8 -*-
from odoo import api, fields, models

PARAM_DEAD_STOCK_DAYS = 'cognify_management_reports.dead_stock_days'
PARAM_SLOW_STOCK_QTY = 'cognify_management_reports.slow_stock_qty'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    # There used to be an annual leave entitlement here. It is gone because a
    # number in a setting is not an entitlement: the staff report now reads the
    # time off allocations HR actually granted, so what is left is free and
    # nobody has to keep two figures in step.
    cognify_dead_stock_days = fields.Integer(
        string='Dead Stock After', default=90,
        help='Days without a sale before stock still on hand counts as dead. '
             'This is the judgement behind the Dead and Slow Moving Stock '
             'report and the dead and low flags on Stock Health.')
    cognify_slow_stock_qty = fields.Float(
        string='Slow Mover Threshold', default=5.0,
        help='Fewer units sold over that period counts as a slow mover. The '
             'period follows the dead stock setting.')

    @api.model
    def get_values(self):
        res = super().get_values()
        parameters = self.env['ir.config_parameter'].sudo()
        stored = parameters._get(PARAM_DEAD_STOCK_DAYS)[0]
        res['cognify_dead_stock_days'] = int(stored) if stored else 90
        stored = parameters._get(PARAM_SLOW_STOCK_QTY)[0]
        res['cognify_slow_stock_qty'] = float(stored) if stored else 5.0
        return res

    def set_values(self):
        res = super().set_values()
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_int(PARAM_DEAD_STOCK_DAYS, self.cognify_dead_stock_days)
        parameters.set_float(PARAM_SLOW_STOCK_QTY, self.cognify_slow_stock_qty)
        return res