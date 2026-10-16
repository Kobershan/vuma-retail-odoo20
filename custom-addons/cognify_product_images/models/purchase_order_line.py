# -*- coding: utf-8 -*-
"""Thumbnail column for purchase order lines on screen.

The printed purchase order already carries the product picture (see the
report files in this module). This adds the same picture to the order form's
line list, so whoever is checking an order against the shelf sees the goods
without printing anything.

The image is exposed as a related field on the line so the list view can use
the standard ``image`` widget. A line whose product has no picture simply
renders no image, exactly as the printed document does.
"""
from odoo import fields, models


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    product_image_128 = fields.Binary(
        related="product_id.image_128",
        string="Picture",
        readonly=True,
        store=False,
    )