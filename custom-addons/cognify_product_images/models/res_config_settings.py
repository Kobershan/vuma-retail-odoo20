# -*- coding: utf-8 -*-
from odoo import api, fields, models

from .product_product import (
    DEFAULT_SIZE,
    IMAGE_SIZE_SELECTION,
    PARAM_PICKING,
    PARAM_PURCHASE_ORDER,
    PARAM_SIZE,
    PARAM_STOCK_TAKE,
)


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    cognify_picking_images = fields.Boolean(
        string='Picking Operations', default=True,
        help='Print the product picture on the Picking Operations sheet the '
             'picker works from.')
    cognify_purchase_order_images = fields.Boolean(
        string='Purchase Orders', default=True,
        help='Print the product picture on Purchase Orders, so the order can be '
             'checked against the shelf.')
    cognify_stock_take_images = fields.Boolean(
        string='Stock Take Sheets', default=True,
        help='Print the product picture on the Count Sheet printed from '
             'Inventory → Adjustments → Physical Inventory.')
    cognify_image_size = fields.Selection(
        selection=IMAGE_SIZE_SELECTION, string='Thumbnail Size',
        default=DEFAULT_SIZE, required=True,
        help='Applies to every document above. Medium fits a picking sheet '
             'without crowding it.')

    @api.model
    def get_values(self):
        res = super().get_values()
        parameters = self.env['ir.config_parameter'].sudo()
        res.update({
            'cognify_picking_images': parameters.get_bool(PARAM_PICKING, True),
            'cognify_purchase_order_images': parameters.get_bool(PARAM_PURCHASE_ORDER, True),
            'cognify_stock_take_images': parameters.get_bool(PARAM_STOCK_TAKE, True),
            'cognify_image_size': parameters.get_str(PARAM_SIZE) or DEFAULT_SIZE,
        })
        return res

    def set_values(self):
        res = super().set_values()
        parameters = self.env['ir.config_parameter'].sudo()
        parameters.set_bool(PARAM_PICKING, self.cognify_picking_images)
        parameters.set_bool(PARAM_PURCHASE_ORDER, self.cognify_purchase_order_images)
        parameters.set_bool(PARAM_STOCK_TAKE, self.cognify_stock_take_images)
        parameters.set_str(PARAM_SIZE, self.cognify_image_size)
        return res