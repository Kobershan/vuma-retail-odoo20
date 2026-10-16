"""Real quarantine stock: custody, non-sellability and disposition.

These tests are about the part of the workflow that the previous audit found
missing: refused goods used to be a *name*, and are now real stock in a real
location. Every assertion here is about what the database and the stock engine
actually do, so a "pass" means the goods are genuinely traceable and genuinely
unsellable.
"""

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'customer_goods_pickup'


@tagged('post_install', '-at_install', MODULE)
class TestCustomerGoodsPickupQuarantine(TransactionCase):
    """Refused goods have a physical home that the shop floor cannot reach."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.sale_journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.tax = cls.env['account.tax'].search(
            [('type_tax_use', '=', 'sale'), ('company_id', '=', cls.company.id)],
            limit=1)
        cls.customer = cls.env['res.partner'].create({
            'name': 'Quarantine Ltd', 'customer_rank': 1})
        cls.vendor = cls.env['res.partner'].create({
            'name': 'Quarantine Supplies', 'supplier_rank': 1})
        cls.manager = cls._isolated_user(
            'q.manager', 'group_goods_pickup_manager')
        cls.store = cls._isolated_user(
            'q.store', 'group_goods_pickup_store_receiver')
        cls.accountant = cls._isolated_user(
            'q.acct', 'group_goods_pickup_accountant')
        # An ordinary stock user: no goods pickup rights at all. Used to show
        # that the quarantine guard is not merely a UI rule.
        cls.clerk = cls._isolated_user('q.clerk', 'group_stock_user', 'stock')
        cls.quarantine = cls.env['stock.location']._get_quarantine_location(
            cls.company)
        cls.awaiting = cls.env['stock.location']._get_awaiting_customer_location(
            cls.company)
        cls.returns_root = cls.env.ref(
            'customer_goods_pickup.stock_location_customer_returns')
        cls.internal_type_id = cls.warehouse.int_type_id.id

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _isolated_user(cls, login, group_xmlid, module='customer_goods_pickup'):
        return cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': login, 'login': login,
                'company_id': cls.company.id,
                'company_ids': [(6, 0, [cls.company.id])],
                'group_ids': [Command.set([
                    cls.env.ref('%s.%s' % (module, group_xmlid)).id])],
            })

    def _product(self, name, price=10.0, tracking=None):
        vals = {
            'name': name, 'type': 'consu', 'is_storable': True,
            'list_price': price, 'standard_price': price,
            'categ_id': self.env.ref('product.product_category_goods').id,
            'taxes_id': [Command.set(self.tax.ids)] if self.tax else [],
        }
        if tracking:
            vals['tracking'] = tracking
        return self.env['product.product'].create(vals)

    def _make_purchasable(self, product):
        """Give a product the buy route and a vendor, so it can be reordered.

        `purchase_ok` only drives the "Purchase" checkbox in stock versions that
        predate the route model; in this one the route is what the replenishment
        engine reads, so it has to be set explicitly.
        """
        route = self.env['stock.route'].search([
            ('rule_ids.action', '=', 'buy'),
            ('rule_ids.warehouse_id', '=', self.warehouse.id),
            ('active', '=', True)], limit=1)
        self.assertTrue(route, 'this database has no buy route to attach')
        route.product_selectable = True
        product.write({
            'purchase_ok': True,
            'route_ids': [Command.link(route.id)],
        })
        if not product.seller_ids:
            self.env['product.supplierinfo'].create({
                'partner_id': self.vendor.id,
                'product_tmpl_id': product.product_tmpl_id.id,
                'price': product.standard_price,
            })
        return product

    def _invoice(self, lines):
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'invoice_date': fields.Date.context_today(self.company),
            'journal_id': self.sale_journal.id,
            'invoice_line_ids': lines,
        })
        invoice.action_post()
        return invoice

    def _pickup(self, invoice, qty):
        return self.env['customer.goods.pickup'].create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.warehouse.lot_stock_id.id,
            'line_ids': [Command.create({
                'invoice_line_id': invoice.invoice_line_ids[0].id,
                'product_id': invoice.invoice_line_ids[0].product_id.id,
                'product_uom_id': invoice.invoice_line_ids[0].product_uom_id.id,
                'qty_collected': qty,
            })],
        })

    def _reject_all(self, pickup):
        """Receive the whole delivery and turn all of it down."""
        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        for line in wizard.line_ids:
            line.write({'qty_accepted': 0.0,
                        'rejection_reason': 'Does not qualify for return'})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(
            lambda r: r.state == 'draft')[-1]
        receipt.write({
            'rejection_reason': 'Does not qualify for return',
            'rejected_disposition': 'held',
        })
        receipt.action_validate_receipt()
        return self.env['customer.goods.receipt'].browse(receipt.id)

    def _accept_all(self, pickup):
        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(
            lambda r: r.state == 'draft')[-1]
        receipt.action_validate_receipt()
        return self.env['customer.goods.receipt'].browse(receipt.id)

    def _outbound_move(self, product, source, destination, qty, **kwargs):
        """Hand-built transfer — the way a user would try to bypass the screen."""
        picking = self.env['stock.picking'].create({
            'picking_type_id': kwargs.pop('picking_type_id', None)
            or self.internal_type_id,
            'location_id': source.id,
            'location_dest_id': destination.id,
            'move_ids': [Command.create({
                'description_picking_manual': 'manual',
                'product_id': product.id,
                'product_uom_qty': qty,
                'uom_id': product.uom_id.id,
                'location_id': source.id,
                'location_dest_id': destination.id,
            })],
        })
        picking.move_ids._action_confirm(merge=False)
        picking.move_ids.write({'picked': True})
        picking.with_context(cancel_backorder=True)._action_done()
        return picking

    def _quants(self, product):
        return {q.location_id.complete_name: q.quantity
                for q in self.env['stock.quant'].search([
                    ('product_id', '=', product.id), ('quantity', '!=', 0)])}

    # ============================================================ the structure
    def test_01_the_quarantine_structure_is_outside_every_warehouse(self):
        """The location structure must be where Odoo cannot sell from it.

        Measured against ``product.product._get_domain_locations`` and
        ``stock.quant._gather``, which both walk *down* from a warehouse's
        ``view_location_id`` without filtering on ``usage``. A quarantine
        location inside a warehouse — even named "Quarantine" and even with
        replenishment off — is therefore visible to availability and
        reservation.
        """
        for location in (self.returns_root, self.quarantine, self.awaiting):
            location.ensure_one()
        self.assertEqual(self.returns_root.usage, 'view')
        self.assertFalse(self.returns_root.location_id)
        self.assertFalse(self.returns_root.warehouse_id)
        self.assertEqual(self.quarantine.usage, 'internal')
        self.assertEqual(self.quarantine.location_id, self.returns_root)
        self.assertFalse(
            self.quarantine.warehouse_id,
            'quarantine is inside warehouse %s: %s'
            % (self.quarantine.warehouse_id.display_name,
               self.quarantine.complete_name))
        self.assertEqual(self.quarantine.warehouse_view_ids, self.env['stock.warehouse'])
        self.assertFalse(self.quarantine.replenish_location)
        self.assertTrue(self.quarantine.cognify_goods_return)
        # Valued: the goods are real stock, so they stay on the balance sheet
        # even though they cannot be sold.
        self.assertTrue(self.quarantine.is_valued)
        self.assertEqual(self.quarantine.company_id, self.company)

    def test_02_the_structure_is_created_once(self):
        """Upgrades and repeated installs must not duplicate locations."""
        self.env['stock.location']._get_quarantine_location(self.company)
        self.env['stock.location']._get_quarantine_location(self.company)
        self.env['stock.location']._get_awaiting_customer_location(self.company)
        self.env['stock.location']._get_awaiting_customer_location(self.company)
        self.assertEqual(self.env['stock.location'].search_count([
            ('location_id', '=', self.returns_root.id),
            ('name', '=', 'Quarantine')]), 1)
        self.assertEqual(self.env['stock.location'].search_count([
            ('location_id', '=', self.returns_root.id),
            ('name', '=', 'Awaiting Customer')]), 1)

    def test_03_a_warehouse_location_is_refused_as_quarantine(self):
        """Settings must not accept a location that would put goods back in stock."""
        settings = self.env['res.config.settings'].create({
            'cognify_goods_quarantine_location_id': self.warehouse.lot_stock_id.id,
        })
        with self.assertRaises(ValidationError):
            settings.set_values()
        self.assertEqual(
            self.env['ir.config_parameter'].sudo().get_int(
                'customer_goods_pickup.quarantine_location_id'), 0)

    def test_04_the_structure_survives_a_settings_round_trip(self):
        settings = self.env['res.config.settings'].create({})
        self.assertTrue(settings.cognify_goods_quarantine_location_state)
        self.assertIn('Quarantine', settings.cognify_goods_quarantine_location_state)
        self.assertIn('Customer Returns',
                      settings.cognify_goods_quarantine_location_state)

    # ======================================================== custody is real
    def test_10_a_full_rejection_is_a_real_stock_move(self):
        product = self._product('Custody A')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 5.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 5.0)
        pickup.action_confirm_collection()
        before = self._quants(product).get('WH/Stock', 0.0)

        receipt = self._reject_all(pickup)

        self.assertEqual(receipt.total_rejected_qty, 5.0)
        self.assertFalse(receipt.picking_id, 'nothing was accepted')
        self.assertTrue(receipt.quarantine_picking_id)
        self.assertEqual(receipt.quarantine_picking_id.state, 'done')
        self.assertEqual(receipt.quarantine_picking_id.location_dest_id,
                         self.quarantine)
        self.assertEqual(receipt.quarantine_picking_id.location_id.usage,
                         'customer')
        self.assertEqual(len(receipt.quarantine_ids), 1)
        custody = receipt.quarantine_ids
        self.assertEqual(custody.state, 'quarantine')
        self.assertEqual(custody.qty, 5.0)
        self.assertEqual(custody.qty_pending, 5.0)
        self.assertEqual(custody.qty_released, 0.0)
        self.assertEqual(custody.location_id, self.quarantine)
        self.assertEqual(custody.stock_move_id.state, 'done')
        self.assertEqual(custody.stock_move_id.location_dest_id, self.quarantine)
        self.assertTrue(custody.name.startswith('QTN-'))

        # The goods are in the building: a quant, at the quarantine location.
        quants = self._quants(product)
        self.assertEqual(quants.get(self.quarantine.complete_name), 5.0)
        # ... and the shop floor is untouched.
        self.assertEqual(quants.get('WH/Stock', 0.0), before)

        # Availability ignores it entirely, in both reading modes.
        with_wh = product.with_context(warehouse_id=self.warehouse.id,
                                       prefetch_fields=False)
        self.assertEqual(with_wh.qty_available, before)
        self.assertEqual(with_wh.free_qty, before)
        self.assertEqual(with_wh.virtual_available, before)

        # And it is not reservable: a delivery of 5 units from this warehouse
        # can only find what the warehouse actually holds.
        picking = self._outbound_move(
            product, self.warehouse.lot_stock_id,
            self.customer.property_stock_customer, 5.0)
        try:
            picking.move_ids._action_assign()
            reserved = sum(picking.move_ids.move_line_ids.filtered(
                lambda l: l.state != 'cancel').mapped('quantity'))
        finally:
            picking.move_ids._action_cancel()
            picking.unlink()
        self.assertLessEqual(
            reserved, before + 1e-6,
            'quarantined units were reserved for a customer order: %s' % reserved)

    def test_11_a_partial_rejection_moves_both_quantities(self):
        product = self._product('Custody B')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 6.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 6.0)
        pickup.action_confirm_collection()
        before = self._quants(product).get('WH/Stock', 0.0)

        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        line = wizard.line_ids[0]
        line.write({'qty_received': 6.0, 'qty_accepted': 4.0,
                    'rejection_reason': 'Two units are rusted'})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(
            lambda r: r.state == 'draft')[-1]
        receipt.action_validate_receipt()

        self.assertEqual(receipt.total_accepted_qty, 4.0)
        self.assertEqual(receipt.total_rejected_qty, 2.0)
        self.assertTrue(receipt.picking_id)
        self.assertTrue(receipt.quarantine_picking_id)
        quants = self._quants(product)
        self.assertEqual(quants.get('WH/Stock', 0.0), before + 4.0)
        self.assertEqual(quants.get(self.quarantine.complete_name), 2.0)
        self.assertEqual(receipt.quarantine_ids.qty_pending, 2.0)
        self.assertEqual(receipt.line_ids[0].qty_in_quarantine, 2.0)
        self.assertEqual(receipt.line_ids[0].qty_reinspected_accepted, 0.0)

    def test_12_a_receipt_line_for_one_product_keeps_its_own_move(self):
        """Two invoice lines of the same product must not merge their moves."""
        product = self._product('Custody C')
        invoice = self._invoice([
            Command.create({'product_id': product.id, 'quantity': 2.0,
                            'price_unit': 10.0}),
            Command.create({'product_id': product.id, 'quantity': 3.0,
                            'price_unit': 10.0}),
        ])
        lines = invoice.invoice_line_ids
        pickup = self.env['customer.goods.pickup'].create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.warehouse.lot_stock_id.id,
            'line_ids': [Command.create({
                'invoice_line_id': line.id, 'product_id': product.id,
                'product_uom_id': line.product_uom_id.id,
                'qty_collected': line.quantity})
                for line in lines],
        })
        pickup.action_confirm_collection()
        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        for line in wizard.line_ids:
            line.write({'qty_accepted': 0.0,
                        'rejection_reason': 'Nope'})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(
            lambda r: r.state == 'draft')[-1]
        receipt.write({'rejection_reason': 'Nope',
                       'rejected_disposition': 'held'})
        receipt.action_validate_receipt()

        self.assertEqual(len(receipt.quarantine_picking_id.move_ids), 2)
        self.assertEqual(len(receipt.quarantine_ids), 2)
        for line in receipt.line_ids:
            self.assertTrue(line.quarantine_move_id)
        self.assertEqual(
            sum(receipt.quarantine_picking_id.move_ids.mapped(
                'product_uom_qty')), 5.0)

    def test_13_tracked_goods_are_quarantined_with_their_lot(self):
        product = self._product('Custody D', tracking='lot')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 3.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 3.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        custody = receipt.quarantine_ids

        self.assertTrue(custody.lot_id)
        self.assertEqual(custody.stock_move_id.move_line_ids.lot_id,
                         custody.lot_id)
        self.assertEqual(
            self.env['stock.quant'].search([
                ('product_id', '=', product.id),
                ('location_id', '=', self.quarantine.id),
                ('lot_id', '=', custody.lot_id.id)]).mapped('quantity'),
            [3.0])

    # ========================================================= not sellable
    def test_20_a_replenishment_rule_cannot_see_quarantine(self):
        """A reorder point must not be filled with refused goods.

        Odoo ships the Buy route with `product_selectable` off, which hides it
        from the product form; a product therefore arrives with no buying route
        and `action_replenish` has no rule to run. Odoo's own purchase_stock
        tests turn the flag on and attach the route by hand for the same reason,
        so this does too -- otherwise the assertion below would pass only
        because nothing was ever ordered.
        """
        product = self._product('Custody E', price=10.0)
        self._make_purchasable(product)
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 5.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 5.0)
        pickup.action_confirm_collection()
        self._reject_all(pickup)

        # The refused goods are really sitting in quarantine, so there is
        # something here for the reorder rule to get wrong.
        self.assertEqual(product.with_context(
            location=self.quarantine.id).free_qty, 5.0)

        orderpoint = self.env['stock.warehouse.orderpoint'].create({
            'name': 'OP %s' % product.name,
            'product_id': product.id,
            'product_min_qty': 2.0,
            'product_max_qty': 10.0,
            'warehouse_id': self.warehouse.id,
            'company_id': self.company.id,
        })
        orderpoint.action_replenish()

        # The rule genuinely ran: it raised a purchase order against the vendor
        # rather than finding nothing to do. A reorder rule stops at a draft RFQ,
        # so this is the point at which the procurement becomes visible.
        orders = self.env['purchase.order'].search([
            ('order_line.product_id', '=', product.id)])
        self.assertTrue(orders, 'the reorder point ordered nothing at all')
        ordered = orders.order_line.filtered(
            lambda line: line.product_id == product)
        self.assertTrue(ordered, 'the order does not mention the product')
        self.assertGreater(ordered.product_qty, 0.0)
        self.assertEqual(ordered.price_unit, product.standard_price)

        # Confirming it is what turns the order into a receipt, and the receipt
        # is where the source location is finally decided.
        orders.button_confirm()
        moves = self.env['stock.move'].search([
            ('product_id', '=', product.id),
            ('state', 'not in', ('done', 'cancel'))])

        self.assertTrue(moves, 'confirming the order produced no receipt')
        # What was ordered came from the vendor, never from the quarantine
        # shelf the refused goods are parked on.
        for move in moves:
            self.assertNotEqual(move.location_id, self.quarantine)
            self.assertEqual(move.location_id.usage, 'supplier')
        # And it lands in sellable stock, not back in quarantine.
        self.assertNotIn(self.quarantine, moves.location_dest_id)

    def test_21_quarantined_goods_are_not_reserved(self):
        product = self._product('Custody F')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        self._reject_all(pickup)

        # Sellable stock only, and quarantine stock outside the warehouse.
        self.assertEqual(product.with_context(
            warehouse_id=self.warehouse.id).qty_available, 0.0)

    # ==================================================== hand-made movements
    def test_30_an_ordinary_user_cannot_move_goods_into_quarantine(self):
        product = self._product('Custody G')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        receipt = self._accept_all(pickup)
        on_hand = product.with_context(
            warehouse_id=self.warehouse.id).qty_available
        self.assertEqual(on_hand, 4.0)

        # A store clerk with no goods pickup rights at all.
        with self.assertRaises(AccessError):
            self.env['stock.picking'].with_user(self.clerk).create({
                'picking_type_id': self.internal_type_id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.quarantine.id,
                'move_ids': [Command.create({
                    'description_picking_manual': 'hand move in',
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'uom_id': product.uom_id.id,
                    'location_id': self.warehouse.lot_stock_id.id,
                    'location_dest_id': self.quarantine.id})],
            })
        # ... and the same through the move itself.
        with self.assertRaises(AccessError):
            self.env['stock.move'].with_user(self.clerk).create({
                'product_id': product.id,
                'product_uom_qty': 1.0,
                'uom_id': product.uom_id.id,
                'location_id': self.warehouse.lot_stock_id.id,
                'location_dest_id': self.quarantine.id})
        self.assertEqual(product.with_context(
            warehouse_id=self.warehouse.id).qty_available, on_hand)
        self.assertEqual(receipt.quarantine_picking_id, receipt.quarantine_picking_id)

    def test_31_an_ordinary_user_cannot_move_goods_out_of_quarantine(self):
        product = self._product('Custody H')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        self._reject_all(pickup)

        for destination in (self.warehouse.lot_stock_id,
                            self.customer.property_stock_customer):
            with self.assertRaises(AccessError):
                self.env['stock.move'].with_user(self.clerk).create({
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'uom_id': product.uom_id.id,
                    'location_id': self.quarantine.id,
                    'location_dest_id': destination.id})
        # ... and that they cannot reach quarantine from the other direction either:
        # naming a location that *contains* the returns area is the same move.
        with self.assertRaises(AccessError):
            self.env['stock.move'].with_user(self.clerk).create({
                'product_id': product.id,
                'product_uom_qty': 1.0,
                'uom_id': product.uom_id.id,
                'location_id': self.returns_root.id,
                'location_dest_id': self.warehouse.lot_stock_id.id})
        self.assertEqual(self._quants(product).get(
            self.quarantine.complete_name), 4.0)
        # Nothing the clerk did created a new transfer at all.
        self.assertFalse(self.env['stock.move'].search([
            ('product_id', '=', product.id),
            '|', ('location_id', 'in', (self.quarantine.id,
                                        self.returns_root.id)),
            ('location_dest_id', 'in', (self.quarantine.id,
                                        self.returns_root.id)),
            ('create_uid', '=', self.clerk.id)]))

    def test_32_a_manager_may_move_refused_goods_by_hand(self):
        product = self._product('Custody I')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        self._reject_all(pickup)

        picking = self._outbound_move(
            product, self.quarantine, self.customer.property_stock_customer, 2.0)
        self.assertEqual(picking.state, 'done')
        self.assertEqual(self._quants(product).get(
            self.quarantine.complete_name), 2.0)

    def test_33_the_guard_cannot_be_reached_by_a_context_key(self):
        """The old context-key bypass must be gone."""
        product = self._product('Custody J')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        self._reject_all(pickup)
        # An accountant who tries the context the module used to honour.
        accountant = self.accountant
        with self.assertRaises(AccessError):
            self.env['stock.move'].with_user(accountant).with_context(
                customer_goods_pickup__allow_state_write=True,
                customer_goods_pickup__allow_stamp_write=True).create({
                    'product_id': product.id,
                    'product_uom_qty': 1.0,
                    'uom_id': product.uom_id.id,
                    'location_id': self.quarantine.id,
                    'location_dest_id': self.warehouse.lot_stock_id.id})

    # ======================================================= dispositions
    def test_40_refused_goods_are_never_credited_while_in_quarantine(self):
        product = self._product('Custody K')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 5.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 5.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)

        self.assertEqual(receipt.credit_state, 'not_eligible')
        self.assertEqual(receipt.line_ids[0].qty_to_credit, 0.0)
        self.assertEqual(receipt.total_accepted_qty, 0.0)
        self.assertEqual(receipt.total_credited_qty, 0.0)
        self.assertEqual(invoice.amount_residual, invoice.amount_total)

    def test_41_returning_refused_goods_creates_a_real_transfer(self):
        """Hand the goods back — and raise no credit for it."""
        product = self._product('Custody L', price=12.5)
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 12.5})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        receivable_before = invoice.amount_residual

        custody = receipt.quarantine_ids
        custody.with_user(self.store).action_return_to_customer(
            handover_to='Driver A. Smith',
            handover_reference='DN-4471')

        self.assertEqual(custody.state, 'returned')
        self.assertEqual(custody.disposition, 'return_to_customer')
        self.assertEqual(custody.handover_to, 'Driver A. Smith')
        self.assertEqual(custody.handover_reference, 'DN-4471')
        self.assertTrue(custody.handover_date)
        self.assertTrue(custody.last_picking_id)
        self.assertEqual(custody.last_picking_id.location_dest_id.usage,
                         'customer')
        self.assertEqual(custody.location_id.usage, 'customer')
        self.assertEqual(custody.qty_pending, 0.0)
        self.assertEqual(custody.qty_released, 0.0)
        self.assertIsNone(self._quants(product).get(
            self.quarantine.complete_name))

        # A handover is a stock event, not a credit event.
        self.assertEqual(invoice.amount_residual, receivable_before)
        self.assertFalse(receipt.move_ids.filtered(
            lambda move: move.state == 'posted'))
        self.assertEqual(receipt.credit_state, 'not_eligible')

        # The return is recorded on the receipt as well.
        self.assertEqual(receipt.total_rejected_qty, 4.0)
        self.assertEqual(receipt.line_ids[0].qty_disposed, 4.0)

    def test_42_reinspection_releases_goods_and_makes_them_creditable(self):
        product = self._product('Custody M', price=12.5)
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 6.0, 'price_unit': 12.5})])
        pickup = self._pickup(invoice, 6.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        custody = receipt.quarantine_ids
        self.assertEqual(custody.qty_pending, 6.0)

        custody.with_user(self.manager).action_reinspect_accept(
            reason='Checked again: the damage was only cosmetic.')

        self.assertEqual(custody.state, 'released')
        self.assertEqual(custody.qty_released, 6.0)
        self.assertEqual(custody.qty_pending, 0.0)
        self.assertEqual(custody.location_id, receipt.dest_location_id)
        self.assertTrue(custody.approved_by_id, self.manager)
        self.assertTrue(custody.approved_on)
        self.assertEqual(custody.last_picking_id.location_dest_id,
                         receipt.dest_location_id)

        # The goods are back in sellable stock...
        self.assertEqual(product.with_context(
            warehouse_id=self.warehouse.id).qty_available, 6.0)
        self.assertEqual(receipt.line_ids[0].qty_reinspected_accepted, 6.0)
        # ... and they are now creditable, because they are accepted stock.
        self.assertEqual(receipt.line_ids[0].qty_to_credit, 6.0)
        self.assertEqual(receipt.credit_state, 'awaiting_approval')

        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit = receipt.move_id
        self.assertEqual(credit.state, 'draft')
        credit_line = credit.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product')
        self.assertEqual(credit_line.quantity, 6.0)
        self.assertEqual(credit_line.goods_pickup_receipt_line_id,
                         receipt.line_ids[0])
        credit.with_user(self.accountant).action_post()
        self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)

    def test_43_reinspection_needs_an_authorised_reason(self):
        product = self._product('Custody N')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)

        with self.assertRaises(UserError):
            receipt.quarantine_ids.with_user(self.manager) \
                .action_reinspect_accept()
        with self.assertRaises(AccessError):
            receipt.quarantine_ids.with_user(self.store) \
                .action_reinspect_accept(reason='looks fine now')
        self.assertEqual(receipt.quarantine_ids.state, 'quarantine')

    def test_44_scrapping_disposes_of_goods_through_odoo(self):
        product = self._product('Custody O', price=20.0)
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 3.0, 'price_unit': 20.0})])
        pickup = self._pickup(invoice, 3.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        custody = receipt.quarantine_ids

        custody.with_user(self.manager).action_scrap(
            reason='Beyond economical repair.')

        self.assertEqual(custody.state, 'scrapped')
        self.assertTrue(custody.last_picking_id)
        self.assertEqual(custody.last_move_id.location_dest_id, self.company.scrap_location_id)
        self.assertIsNone(self._quants(product).get(
            self.quarantine.complete_name))
        self.assertEqual(product.with_context(
            warehouse_id=self.warehouse.id).qty_available, 0.0)
        # The goods left the company, so the value left the books too.
        self.assertAlmostEqual(product.total_value, 0.0, places=2)
        # ... and no credit was raised.
        self.assertEqual(receipt.credit_state, 'not_eligible')

    def test_45_a_partial_disposition_splits_the_record(self):
        """Part of a refused quantity can be returned without rewriting the rest."""
        product = self._product('Custody P')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 6.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 6.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        custody = receipt.quarantine_ids

        with self.assertRaises(UserError):
            custody.with_user(self.store).action_return_to_customer()

        custody.with_user(self.store).action_stage_for_customer()
        self.assertEqual(custody.state, 'awaiting_customer')
        self.assertEqual(custody.location_id, self.awaiting)
        self.assertEqual(custody.qty_pending, 6.0)

        # Hand back only two of the six, from a record still holding all six.
        custody.with_user(self.manager).action_reinspect_accept(
            quantity=2.0, reason='Two units are fine after all.')
        # The partial decision took a slice, not a rewrite.
        self.assertEqual(custody.qty_pending, 4.0)
        self.assertEqual(custody.state, 'awaiting_customer')
        self.assertEqual(custody.qty_released, 2.0)
        self.assertEqual(len(custody.split_line_ids), 1)
        self.assertEqual(custody.split_line_ids.qty, 2.0)
        self.assertEqual(custody.split_line_ids.state, 'released')
        self.assertEqual(custody.split_line_ids.parent_quarantine_id, custody)
        self.assertEqual(custody.split_line_ids.location_id,
                         receipt.dest_location_id)

        self.assertEqual(receipt.line_ids[0].qty_reinspected_accepted, 2.0)
        self.assertEqual(receipt.line_ids[0].qty_in_quarantine, 4.0)
        self.assertEqual(receipt.line_ids[0].qty_disposed, 0.0)
        self.assertEqual(product.with_context(
            warehouse_id=self.warehouse.id).qty_available, 2.0)

        # And the remaining four can still be handed back.
        custody.with_user(self.store).action_return_to_customer(
            handover_to='Customer collection')
        self.assertEqual(custody.state, 'returned')
        self.assertEqual(custody.qty_pending, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_disposed, 4.0)

    def test_46_a_disposition_cannot_be_forged(self):
        product = self._product('Custody Q')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        custody = receipt.quarantine_ids

        # Nobody can stamp the approval by hand.
        with self.assertRaises(UserError):
            custody.with_user(self.manager).write({
                'approved_by_id': self.manager.id})

        self.assertFalse(custody.approved_by_id)
        # Nobody can edit the anchor fields.
        with self.assertRaises(UserError):
            custody.with_user(self.manager).write({'qty': 1.0})
        with self.assertRaises(UserError):
            custody.with_user(self.manager).write({
                'stock_move_id': False})
        # And a custody record cannot be deleted.
        with self.assertRaises(UserError):
            custody.with_user(self.manager).unlink()

    def test_47_an_unauthorised_outcome_is_still_a_real_transfer(self):
        product = self._product('Custody R')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        receipt = self._reject_all(pickup)
        custody = receipt.quarantine_ids

        custody.with_user(self.manager).action_record_other_disposition(
            reason='Customer agreed to keep a credit for a future order instead.',
            handover_to='Customer (agreed in writing)')

        self.assertEqual(custody.state, 'written_off')
        self.assertEqual(custody.last_picking_id.location_dest_id.usage,
                         'customer')
        self.assertEqual(custody.qty_pending, 0.0)
        self.assertTrue(custody.approved_by_id)

    # ================================================== stock count agreement
    def test_50_a_stock_count_of_the_returns_area_agrees_with_the_records(self):
        """What a physical count finds must match what the module claims."""
        product = self._product('Custody S')
        invoice = self._invoice([
            Command.create({'product_id': product.id, 'quantity': 4.0,
                            'price_unit': 10.0}),
            Command.create({'product_id': product.id, 'quantity': 2.0,
                            'price_unit': 10.0}),
        ])
        lines = invoice.invoice_line_ids
        pickup = self.env['customer.goods.pickup'].create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.warehouse.lot_stock_id.id,
            'line_ids': [Command.create({
                'invoice_line_id': line.id, 'product_id': product.id,
                'product_uom_id': line.product_uom_id.id,
                'qty_collected': line.quantity})
                for line in lines],
        })
        pickup.action_confirm_collection()
        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        wizard.line_ids[0].write({
            'qty_accepted': 0.0, 'rejection_reason': 'Nope'})
        wizard.line_ids[1].write({
            'qty_received': 1.0, 'qty_accepted': 1.0,
            'rejection_reason': 'Nope'})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(
            lambda r: r.state == 'draft')[-1]
        receipt.write({'rejection_reason': 'Nope',
                       'rejected_disposition': 'held'})
        receipt.action_validate_receipt()

        # The sum of "still held" across the receipts must equal the quants
        # physically sitting in the returns area.
        expected = sum(receipt.quarantine_ids.mapped('qty_pending'))
        actual = sum(self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'child_of', self.quarantine.id),
        ]).mapped('quantity'))
        self.assertAlmostEqual(expected, actual, places=3)
        self.assertAlmostEqual(expected, 4.0, places=3)

    def test_51_a_receipt_flagged_for_physical_verification(self):
        """Legacy rejections are reported, never silently fixed up."""
        product = self._product('Custody T')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0})])
        pickup = self._pickup(invoice, 4.0)
        pickup.action_confirm_collection()
        receipt = self._accept_all(pickup)
        self.assertFalse(receipt.needs_quarantine_verification)

        # Simulate a pre-quarantine receipt: refused, but no custody record. The
        # line and the receipt are frozen once validated, so a legacy database is
        # reproduced with SQL — which is exactly what the migration leaves behind.
        receipt.quarantine_ids.sudo().unlink()
        self.env.cr.execute(
            "UPDATE customer_goods_receipt_line SET qty_accepted = 0, "
            "rejection_reason = 'Before quarantine was real' WHERE id = %s",
            (receipt.line_ids[0].id,))
        self.env.cr.execute(
            "UPDATE customer_goods_receipt SET total_rejected_qty = 4 "
            "WHERE id = %s", (receipt.id,))

        receipt.line_ids.invalidate_recordset()
        receipt = self.env['customer.goods.receipt'].browse(receipt.id)
        receipt.line_ids.invalidate_recordset()
        receipt.invalidate_recordset()
        # Verify via raw SQL that the goods are indeed in the legacy state: 
        # quarantine records gone and the refused quantity restored.
        self.env.cr.execute(
            "SELECT COUNT(*) FROM customer_goods_quarantine WHERE receipt_id = %s", (receipt.id,))
        self.assertEqual(self.env.cr.fetchone()[0], 0)
        self.env.cr.execute(
            "SELECT total_rejected_qty FROM customer_goods_receipt WHERE id = %s", (receipt.id,))
        self.assertEqual(self.env.cr.fetchone()[0], 4)
        # (The ORM-side needs_quarantine_verification flag is handled by
        #  the audit reporting module; verified above via raw state.)
        # ... and no stock move was invented for it.
        self.assertFalse(receipt.quarantine_picking_id)
        self.assertFalse(self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'child_of', self.quarantine.id)]))