# -*- coding: utf-8 -*-
"""End-to-end verification of cognify_product_images.

Covers the two halves of the feature separately, because they can fail apart:

1. **The picture itself** -- what comes back from the helper for a PNG, a
   JPEG and (the interesting one) a WebP, and for a product with no picture.
2. **The documents** -- that the picture is really in the rendered HTML of a
   picking sheet, a purchase order and a stock take sheet, and that the PDF
   those HTMLs turn into is *measurably bigger* than the same document printed
   with pictures switched off. Comparing file sizes is the honest way to prove
   bytes made it into the PDF; asserting on a string inside it would not.

Every test runs in a savepoint Odoo rolls back, so the database is left as it
was found.
"""
import base64
import io
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

from PIL import Image

from odoo.tests import TransactionCase, tagged
from odoo.tools import image as odoo_tools_image

try:
    # Pillow only registers the WebP saver once its plugin is imported, so an
    # unqualified save(format='WEBP') raises KeyError. Guarded because the
    # WebP tests are the interesting ones, but must not break a Pillow that
    # was built without WebP.
    from PIL import WebPImagePlugin  # noqa: F401
    WEBP_SUPPORTED = True
except ImportError:
    WEBP_SUPPORTED = False

from ..models.product_product import (
    DEFAULT_SIZE,
    PARAM_PICKING,
    PARAM_PURCHASE_ORDER,
    PARAM_SIZE,
    PARAM_STOCK_TAKE,
    THUMBNAIL_SIZES,
)

# Counts pictures in rendered HTML. Counting 'data:image/' would also catch the
# company logo, which every external layout embeds the same way.
THUMBNAIL_MARKER = 'cognify-product-image'


# custom-addons/, so a subprocess can import the addon by package name.
ADDONS_PATH = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The Odoo source root, so a subprocess can import odoo.tools the way a worker does.
ODOO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(odoo_tools_image.__file__)))


def image_bytes(fmt='PNG', size=(320, 240), colour=(200, 40, 40)):
    """A small solid-colour picture in the requested format."""
    buffer = io.BytesIO()
    Image.new('RGB', size, colour).save(buffer, format=fmt)
    return buffer.getvalue()


def data_uri_payload(uri):
    """Return the bytes behind a ``data:`` URI."""
    assert uri.startswith('data:'), uri
    return base64.b64decode(uri.split(';base64,', 1)[1])


@tagged('post_install', '-at_install')
class TestProductImages(TransactionCase):
    """Pictures on printed documents."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.parameters = cls.env['ir.config_parameter'].sudo()
        cls.qweb = cls.env['ir.qweb']

        cls.stock_location = cls.env.ref('stock.stock_location_stock')
        cls.customers_location = cls.env.ref('stock.stock_location_customers')
        cls.out_picking_type = cls.env.ref('stock.picking_type_out')

        cls.plain_product = cls.env['product.product'].create({
            'name': 'Picture Test: no picture',
            'type': 'consu',
            'is_storable': True,
        })
        cls.png_product = cls._product_with_picture('Picture Test: png', image_bytes('PNG'))
        cls.jpeg_product = cls._product_with_picture('Picture Test: jpeg', image_bytes('JPEG'))
        cls.webp_product = (cls._product_with_picture(
            'Picture Test: webp', image_bytes('WEBP', size=(1600, 1200)))
            if WEBP_SUPPORTED else None)

        # A WebP on disk, for the subprocess test that reproduces a live server.
        cls.webp_payload_path = os.path.join(
            tempfile.mkdtemp(prefix='cognify_webp_'), 'picture.webp')
        with open(cls.webp_payload_path, 'wb') as handle:
            handle.write(image_bytes('WEBP', size=(400, 400)))

    @classmethod
    def tearDownClass(cls):
        directory = os.path.dirname(cls.webp_payload_path)
        if os.path.isdir(directory):
            shutil.rmtree(directory, ignore_errors=True)
        super().tearDownClass()

    @classmethod
    def _product_with_picture(cls, name, payload):
        """A storable product carrying ``payload`` as its picture."""
        product = cls.env['product.product'].create({
            'name': name,
            'type': 'consu',
            'is_storable': True,
        })
        # A Binary field is written with base64 text (or a BinaryValue), the
        # same shape the web client sends.
        product.image_1920 = base64.b64encode(payload).decode()
        return product

    # ------------------------------------------------------------------
    # 1. the picture
    # ------------------------------------------------------------------
    def test_png_picture_is_passed_through(self):
        uri = self.png_product._cognify_print_image_uri()
        self.assertTrue(uri.startswith('data:image/png;base64,'), uri[:40])
        # Compared as a decoded picture, not byte for byte: Odoo re-encodes
        # whatever is uploaded when it builds the resized variants. 128px is
        # the variant read, which is the whole point of reading that one.
        self.assert_solid_picture(data_uri_payload(uri), (128, 96), (200, 40, 40))

    def test_jpeg_picture_is_passed_through(self):
        uri = self.jpeg_product._cognify_print_image_uri()
        self.assertTrue(uri.startswith('data:image/jpeg;base64,'), uri[:40])
        with Image.open(io.BytesIO(data_uri_payload(uri))) as decoded:
            self.assertEqual(decoded.format, 'JPEG')

    @unittest.skipUnless(WEBP_SUPPORTED, 'Pillow has no WebP support')
    def test_webp_picture_is_converted_because_the_pdf_engine_drops_it(self):
        """wkhtmltopdf 0.12.6 cannot decode WebP and drops it silently."""
        uri = self.webp_product._cognify_print_image_uri()
        self.assertTrue(uri.startswith('data:image/jpeg;base64,'), uri[:40])

        payload = data_uri_payload(uri)
        with Image.open(io.BytesIO(payload)) as decoded:
            self.assertEqual(decoded.format, 'JPEG')
            # Capped, so a 40-line picking sheet does not become a 20MB PDF.
            self.assertLessEqual(max(decoded.size), 256, decoded.size)
            self.assertEqual(decoded.size, (256, 192), 'aspect ratio must survive')

    def test_product_without_picture_yields_nothing(self):
        self.assertEqual(self.plain_product._cognify_print_image_uri(), '')

    def test_variant_picture_wins_over_the_template_picture(self):
        """Odoo keeps a per-variant picture; the printed one is that variant's."""
        attribute = self.env['product.attribute'].create({
            'name': 'Picture Test: size',
            'create_variant': 'always',
        })
        # Values are made on their own: written inline under value_ids they
        # arrive without the attribute_id the model requires.
        values = self.env['product.attribute.value'].create([
            {'name': 'Small', 'attribute_id': attribute.id},
            {'name': 'Large', 'attribute_id': attribute.id},
        ])
        parent = self.env['product.product'].create({
            'name': 'Picture Test: variants',
            'type': 'consu',
            'is_storable': True,
            'attribute_line_ids': [(0, 0, {
                'attribute_id': attribute.id,
                'value_ids': [(6, 0, values.ids)],
            })],
        })
        # Variants are built on demand, not at create time, and building them replaces
        # the single product this template started with, so keep only its id.
        template_id = parent.product_tmpl_id.id
        parent.product_tmpl_id._create_variant_ids()
        variant = self.env['product.product'].search([
            ('product_tmpl_id', '=', template_id),
            ('product_template_variant_value_ids.name', '=', 'Large'),
        ])
        self.assertEqual(len(variant), 1, 'no variant was created for the Large value')
        variant.image_1920 = base64.b64encode(
            image_bytes('PNG', colour=(0, 0, 200))).decode()
        uri = variant._cognify_print_image_uri()
        self.assertTrue(uri.startswith('data:image/png;base64,'), uri[:40])

    # ------------------------------------------------------------------
    # 2. the markup
    # ------------------------------------------------------------------
    def test_markup_carries_the_picture_and_the_configured_size(self):
        tag = self.qweb._cognify_product_image(self.png_product, 'picking')
        self.assertTrue(str(tag).startswith('<img '), tag)
        self.assertIn('data:image/png;base64,', tag)
        self.assertIn('max-height:%s' % THUMBNAIL_SIZES[DEFAULT_SIZE], tag)

    def test_the_helper_also_works_on_a_template(self):
        """Templates carry image_128 too, and must not blow up when one is passed."""
        template = self.png_product.product_tmpl_id
        uri = template._cognify_print_image_uri()
        self.assertTrue(uri.startswith('data:image/png;base64,'), uri[:40])

    @unittest.skipUnless(WEBP_SUPPORTED, 'Pillow has no WebP support')
    def test_webp_converts_in_a_process_where_odoo_has_pinned_pillow(self):
        """The bug this catches only appears outside the test runner.

        Odoo loads PIL with ``Image.preinit()`` and pins
        ``Image._initialized = 2`` (see ``odoo/tools/image.py``), so a real
        server process never registers Pillow's WebP handler on its own. The
        test files above import ``WebPImagePlugin`` themselves, which masks
        that; a subprocess that does not is the only honest reproduction.
        """
        program = textwrap.dedent("""
            import sys
            sys.path.insert(0, {odoo_root!r})
            import odoo.addons
            odoo.addons.__path__.append({custom_addons!r})
            # Reproduce the live server: Odoo preloads PIL and stops Pillow
            # from registering its remaining plugins.
            from odoo.tools import image as odoo_image
            from PIL import Image
            assert not any('WebP' in name for name in Image.ID), (
                'precondition failed: WebP was already registered')

            from odoo.addons.cognify_product_images.models.product_product import _webp_as_jpeg

            class FakeWebp:
                mimetype = 'image/webp'
                def open(self):
                    return open({payload!r}, 'rb')

            print(len(_webp_as_jpeg(FakeWebp())))
        """).format(odoo_root=ODOO_ROOT, custom_addons=ADDONS_PATH,
                    payload=self.webp_payload_path)

        process = subprocess.run([sys.executable, '-c', program],
                                 capture_output=True, text=True, timeout=120)
        self.assertEqual(process.returncode, 0,
                         process.stderr[-800:] if process.stderr else '')
        self.assertGreater(int(process.stdout.strip()), 0,
                           'no JPEG came back out of the conversion')

    def test_no_picture_means_no_markup(self):
        self.assertEqual(self.qweb._cognify_product_image(self.plain_product, 'picking'), '')

    def test_a_line_without_a_product_is_left_alone(self):
        """Purchase order notes/sections carry no product; they must not break."""
        self.assertEqual(self.qweb._cognify_product_image(False, 'purchase_order'), '')
        self.assertEqual(self.qweb._cognify_product_image(self.env['product.product'], 'picking'), '')
        self.assertEqual(self.qweb._cognify_product_image(self.partner(), 'purchase_order'), '')

    def test_unknown_document_is_refused_loudly(self):
        with self.assertRaises(Exception):
            self.qweb._cognify_product_image(self.png_product, 'carrier_pigeon')

    def test_each_document_has_its_own_switch(self):
        for document, param in (('picking', PARAM_PICKING),
                                ('purchase_order', PARAM_PURCHASE_ORDER),
                                ('stock_take', PARAM_STOCK_TAKE)):
            self.parameters.set_bool(param, False)
            try:
                self.assertEqual(self.qweb._cognify_product_image(self.png_product, document), '')
            finally:
                self.parameters.set_bool(param, True)
            self.assertTrue(
                self.qweb._cognify_product_image(self.png_product, document),
                'switch %s did not come back on' % param)

    def test_thumbnail_size_is_configurable(self):
        self.parameters.set_str(PARAM_SIZE, 'large')
        tag = self.qweb._cognify_product_image(self.png_product, 'picking')
        self.assertIn('max-height:%s' % THUMBNAIL_SIZES['large'], tag)

    def test_an_unrecognised_size_falls_back_instead_of_printing_nothing(self):
        self.parameters.set_str(PARAM_SIZE, 'enormous')
        tag = self.qweb._cognify_product_image(self.png_product, 'picking')
        self.assertIn('max-height:%s' % THUMBNAIL_SIZES[DEFAULT_SIZE], tag)

    def test_settings_round_trip_through_the_database(self):
        settings = self.env['res.config.settings'].create({
            'cognify_picking_images': False,
            'cognify_purchase_order_images': True,
            'cognify_stock_take_images': False,
            'cognify_image_size': 'large',
        })
        settings.execute()
        self.assertFalse(self.parameters.get_bool(PARAM_PICKING, True))
        self.assertTrue(self.parameters.get_bool(PARAM_PURCHASE_ORDER, True))
        self.assertFalse(self.parameters.get_bool(PARAM_STOCK_TAKE, True))
        self.assertEqual(self.parameters.get_str(PARAM_SIZE), 'large')

        read_back = self.env['res.config.settings'].create({}).get_values()
        self.assertFalse(read_back['cognify_picking_images'])
        self.assertFalse(read_back['cognify_stock_take_images'])
        self.assertEqual(read_back['cognify_image_size'], 'large')

    # ------------------------------------------------------------------
    # 3. the documents
    # ------------------------------------------------------------------
    def test_picking_sheet_carries_the_picture(self):
        picking = self._picking_with(self.png_product)
        self._assert_document_has_picture('stock.action_report_picking', picking)
        self._assert_picture_reaches_the_pdf('stock.action_report_picking', picking)

    def test_purchase_order_carries_the_picture(self):
        order = self._purchase_order_with(self.jpeg_product)
        self._assert_document_has_picture('purchase.action_report_purchase_order', order)
        self._assert_picture_reaches_the_pdf('purchase.action_report_purchase_order', order)

    @unittest.skipUnless(WEBP_SUPPORTED, 'Pillow has no WebP support')
    def test_a_webp_product_prints_on_the_purchase_order(self):
        """The converted picture has to survive the whole render, not just the helper."""
        order = self._purchase_order_with(self.webp_product)
        html = self._render_html('purchase.action_report_purchase_order', order)
        self.assertIn('data:image/jpeg;base64,', html)
        self.assertNotIn('data:image/webp', html)
        self._assert_picture_reaches_the_pdf('purchase.action_report_purchase_order', order)

    def test_stock_take_sheet_carries_the_picture(self):
        quant = self._quant_for(self.png_product)
        self._assert_document_has_picture('stock.action_report_inventory', quant)
        self._assert_picture_reaches_the_pdf('stock.action_report_inventory', quant)

    def test_a_line_without_a_product_still_prints(self):
        """A description-only line must print exactly as it did before.

        Buyers type lines with no product behind them (a made-to-order note,
        a service charge). Those lines have no picture to print, and asking
        for one must not break the document.
        """
        order = self.env['purchase.order'].create({
            'partner_id': self.partner().id,
            'order_line': [
                (0, 0, {
                    'name': 'Made to order, no catalogue product',
                    'product_qty': 1,
                    'price_unit': 50.0,
                }),
                (0, 0, {
                    'product_id': self.png_product.id,
                    'name': self.png_product.name,
                    'product_qty': 2,
                    'uom_id': self.png_product.uom_id.id,
                    'price_unit': 12.5,
                }),
            ],
        })
        html = self._render_html('purchase.action_report_purchase_order', order)
        self.assertIn('Made to order, no catalogue product', html)
        self.assertEqual(html.count(THUMBNAIL_MARKER), 1,
                         'only the line that has a product may get a picture')
        report = self.env['ir.actions.report'].with_context(force_report_rendering=True)
        pdf, _type = report._render_qweb_pdf('purchase.action_report_purchase_order', order.ids)
        self.assertTrue(bytes(pdf).startswith(b'%PDF-'))

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def assert_solid_picture(self, payload, size, colour):
        """Assert ``payload`` decodes to a flat picture of one colour."""
        with Image.open(io.BytesIO(payload)) as decoded:
            self.assertEqual(decoded.size, size)
            self.assertEqual(decoded.convert('RGB').getpixel((0, 0)), colour)

    def partner(self):
        return self.env['res.partner'].create({'name': 'Picture Test Vendor'})

    def _picking_with(self, product):
        # Stock first: the picking sheet only prints its table once the moves
        # carry move lines with a reserved quantity, which happens on confirm
        # when the goods are actually available.
        self.env['stock.quant']._update_available_quantity(product, self.stock_location, 3)
        picking = self.env['stock.picking'].create({
            'picking_type_id': self.out_picking_type.id,
            'location_id': self.stock_location.id,
            'location_dest_id': self.customers_location.id,
            'move_ids': [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': 3,
                'location_id': self.stock_location.id,
                'location_dest_id': self.customers_location.id,
            })],
        })
        picking.action_confirm()
        picking.action_assign()
        self.assertTrue(picking.move_line_ids, 'no move lines: the sheet would be empty')
        return picking

    def _purchase_order_with(self, product):
        return self.env['purchase.order'].create({
            'partner_id': self.partner().id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': product.name,
                'product_qty': 5,
                'uom_id': product.uom_id.id,
                'price_unit': 12.5,
            })],
        })

    def _quant_for(self, product):
        self.env['stock.quant']._update_available_quantity(
            product, self.stock_location, 10)
        return self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', self.stock_location.id),
        ])

    def _render_html(self, report_ref, records):
        return self.env['ir.actions.report']._render_qweb_html(report_ref, records.ids)[0].decode()

    def _render_pdf_size(self, report_ref, records):
        # In a test run Odoo quietly returns HTML from _render_qweb_pdf unless
        # asked for the real thing. The PDF engine is the whole point of this
        # module (it is what drops WebP), so the real thing is what we ask for.
        report = self.env['ir.actions.report'].with_context(force_report_rendering=True)
        pdf, _type = report._render_qweb_pdf(report_ref, records.ids)
        self.assertTrue(bytes(pdf).startswith(b'%PDF-'), 'PDF did not render')
        return len(pdf)

    def _assert_document_has_picture(self, report_ref, records):
        html = self._render_html(report_ref, records)
        self.assertIn('data:image/', html,
                      'no picture in the rendered %s' % report_ref)

    def _assert_picture_reaches_the_pdf(self, report_ref, records):
        """The PDF must actually grow by the picture, not just the HTML."""
        with_picture = self._render_pdf_size(report_ref, records)
        self.parameters.set_bool(PARAM_STOCK_TAKE, False)
        self.parameters.set_bool(PARAM_PURCHASE_ORDER, False)
        self.parameters.set_bool(PARAM_PICKING, False)
        try:
            without_picture = self._render_pdf_size(report_ref, records)
        finally:
            self.parameters.set_bool(PARAM_STOCK_TAKE, True)
            self.parameters.set_bool(PARAM_PURCHASE_ORDER, True)
            self.parameters.set_bool(PARAM_PICKING, True)
        self.assertGreater(
            with_picture, without_picture,
            '%s: PDF is the same size with and without pictures '
            '(%d vs %d bytes) -- the picture never made it into the file'
            % (report_ref, with_picture, without_picture))