# -*- coding: utf-8 -*-
from markupsafe import Markup

from odoo import api, models
from odoo.exceptions import UserError

from .product_product import (
    DEFAULT_SIZE,
    DOCUMENT_PARAMETERS,
    PARAM_SIZE,
    THUMBNAIL_SIZES,
)

# Templates may only hold a picture for these models. Both carry ``image_128``.
PICTURE_MODELS = ('product.product', 'product.template')

# Top-aligned rather than a float or vertical-align:middle. A float is taken
# out of the flow, so on a one-line product name the picture would hang below
# the cell instead of growing the row. With vertical-align:middle a tall
# picture pushes the name down towards its bottom edge, which reads as if the
# name belongs to whatever comes next. Aligning the top of the picture with the
# top of the line keeps the name beside its own picture.
THUMBNAIL_STYLE = (
    'display:inline-block;'
    'vertical-align:top;'
    'margin-right:3mm;'
    'max-height:%(size)s;'
    'max-width:%(size)s;'
)


class IrQweb(models.AbstractModel):
    _inherit = 'ir.qweb'

    def _prepare_environment(self, values):
        """Add ``cognify_product_image(product, document)`` to template names."""
        ir_qweb = super()._prepare_environment(values)
        values['cognify_product_image'] = self._cognify_product_image
        return ir_qweb

    @api.model
    def _cognify_product_image(self, product, document):
        """Return the ``<img>`` markup for a product picture on a document.

        Returns an empty string -- which prints as nothing -- when the document
        is switched off, when the line has no product at all, or when the
        product has no usable picture. That lets a template drop the result
        straight into a product cell with no conditionals of its own.
        """
        if document not in DOCUMENT_PARAMETERS:
            raise UserError("cognify_product_images: unknown document '%s'. "
                            "Known documents: %s" % (
                                document, ', '.join(sorted(DOCUMENT_PARAMETERS))))
        parameters = self.env['ir.config_parameter'].sudo()
        if not parameters.get_bool(DOCUMENT_PARAMETERS[document], True):
            return ''

        # A purchase order line can be a note or a section with no product
        # behind it, and a move can be a free-text line.
        if not isinstance(product, models.BaseModel) or product._name not in PICTURE_MODELS or not product:
            return ''

        uri = product._cognify_print_image_uri()
        if not uri:
            return ''

        size = THUMBNAIL_SIZES.get(
            parameters.get_str(PARAM_SIZE, DEFAULT_SIZE),
            THUMBNAIL_SIZES[DEFAULT_SIZE])
        alt = product.sudo().display_name
        # Markup, not a plain string: t-out escapes anything else, which would
        # print the tag itself as text.
        return Markup(
            '<img class="cognify-product-image" src="%(uri)s" alt="%(alt)s" title="%(alt)s"'
            ' style="' + THUMBNAIL_STYLE + '"/>'
        ) % {
            'uri': uri,
            'alt': alt,
            'size': size,
        }