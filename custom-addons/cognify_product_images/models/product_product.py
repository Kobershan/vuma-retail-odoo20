# -*- coding: utf-8 -*-
import base64
import io
import logging

from odoo import models

_logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration keys
# ---------------------------------------------------------------------------
# One switch per document, so a shop can put pictures on the picking sheet it
# works from while keeping the purchase order clean if a supplier objects.
PARAM_PICKING = 'cognify_product_images.picking'
PARAM_PURCHASE_ORDER = 'cognify_product_images.purchase_order'
PARAM_STOCK_TAKE = 'cognify_product_images.stock_take'
PARAM_SIZE = 'cognify_product_images.size'

# Document key (as used in the report templates) -> configuration key.
DOCUMENT_PARAMETERS = {
    'picking': PARAM_PICKING,
    'purchase_order': PARAM_PURCHASE_ORDER,
    'stock_take': PARAM_STOCK_TAKE,
}

# Printed box for the thumbnail. Kept small on purpose: a picking sheet runs to
# 40 lines, and a full-size picture on each one makes the PDF unusable.
THUMBNAIL_SIZES = {
    'small': '12mm',
    'medium': '20mm',
    'large': '30mm',
}
DEFAULT_SIZE = 'medium'

# The settings dropdown is built from this so the label can never drift away
# from the size actually used when printing.
IMAGE_SIZE_SELECTION = [
    (key, '%s (%s cm)' % (key.capitalize(), size[:-2]))
    for key, size in THUMBNAIL_SIZES.items()
]

# The picture is read from the 128px variant: bigger than any thumbnail box
# above at print resolution, and stored as an attachment, so reading it costs
# one small row per product.
IMAGE_FIELD = 'image_128'

# wkhtmltopdf 0.12.6 (Qt WebKit) cannot decode WebP -- the picture is dropped
# from the PDF silently, leaving a blank space. JPEG, PNG, GIF and base64 SVG
# were all verified to render, so WebP is the only format needing conversion.
# SVG data URIs can also fail in some wkhtmltopdf versions; convert to PNG.
WEBP_MIMETYPE = 'image/webp'
SVG_MIMETYPE = 'image/svg+xml'
PNG_MIMETYPE = 'image/png'
JPEG_MIMETYPE = 'image/jpeg'
JPEG_QUALITY = 85
PNG_QUALITY = 90
MAX_PIXELS = 256


def _data_uri(mimetype, payload):
    """Return ``payload`` as an RFC 2397 data URI."""
    return 'data:%s;base64,%s' % (mimetype, base64.b64encode(payload).decode())


def _print_image_uri(product):
    """Return ``product``'s picture as a data URI a PDF engine can draw.

    Returns ``''`` when the product has no picture, and never raises: a broken
    picture must not stop a document printing.
    """
    # Read as superuser. The picture is printed next to the product name, which
    # the document already shows to whoever prints it -- but a portal user
    # downloading a purchase order PDF has no rights on product.product and
    # should not get an AccessError over a cosmetic picture.
    image = product.sudo()[IMAGE_FIELD]
    if not image:
        return ''
    try:
        if image.mimetype == WEBP_MIMETYPE:
            return _data_uri(JPEG_MIMETYPE, _webp_as_jpeg(image))
        if image.mimetype == SVG_MIMETYPE:
            return _data_uri(PNG_MIMETYPE, _svg_as_png(image))
        return _data_uri(image.mimetype, bytes(image))
    except Exception:
        _logger.warning(
            'cognify_product_images: cannot read the picture of %s %s (%s); '
            'it will print without one',
            product._name, product.id, product.display_name, exc_info=True)
        return ''


def _svg_as_png(image):
    """Re-encode an SVG picture as a small PNG.

    Some wkhtmltopdf versions cannot render SVG data URIs reliably.
    Converting to PNG ensures consistent PDF output.
    """
    from PIL import Image
    # Register SVG plugin if available (cairosvg or similar)
    try:
        import cairosvg  # noqa: F401
    except ImportError:
        pass

    with image.open() as stream:
        svg_data = stream.read()
    # Convert SVG to PNG using cairosvg if available, else fall back to PIL
    try:
        import cairosvg
        png_data = cairosvg.svg2png(
            bytestring=svg_data,
            output_width=MAX_PIXELS,
            output_height=MAX_PIXELS,
        )
    except Exception:
        # Fallback: try PIL (may not support SVG without plugins)
        with image.open() as stream:
            with Image.open(stream) as source:
                source.thumbnail((MAX_PIXELS, MAX_PIXELS), Image.Resampling.LANCZOS)
                if source.mode in ('RGBA', 'LA') or (source.mode == 'P' and 'transparency' in source.info):
                    pass  # Keep alpha for PNG
                else:
                    source = source.convert('RGBA')
                buffer = io.BytesIO()
                source.save(buffer, format='PNG', optimize=True)
                png_data = buffer.getvalue()
    return png_data


# Both models carry image_128 and can reach the same picture, so both get the
# same helper rather than one of them raising on a template that hands over a
# product.template.
class ProductProduct(models.Model):
    _inherit = 'product.product'

    def _cognify_print_image_uri(self):
        """Return this product's picture as a PDF-safe data URI, or ''."""
        self.ensure_one()
        return _print_image_uri(self)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _cognify_print_image_uri(self):
        """Return this template's picture as a PDF-safe data URI, or ''."""
        self.ensure_one()
        return _print_image_uri(self)