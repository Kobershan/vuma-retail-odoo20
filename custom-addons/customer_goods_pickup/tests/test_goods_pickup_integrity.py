"""Structural tests: the module's own definitions have to hold together.

These are cheap, but they catch the kind of breakage that only shows up on an
upgrade — a view that no longer parses, a field removed by a core change, a
sequence that stops producing distinct references.
"""

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'customer_goods_pickup'

VIEWS = [
    ('customer.goods.pickup', 'list'),
    ('customer.goods.pickup', 'form'),
    ('customer.goods.pickup', 'search'),
    ('customer.goods.pickup.add.lines', 'form'),
    ('customer.goods.receipt', 'list'),
    ('customer.goods.receipt', 'form'),
    ('customer.goods.receipt', 'search'),
    ('customer.goods.receipt.receive', 'form'),
    ('customer.goods.receipt.reject', 'form'),
    ('customer.goods.pickup.cancel', 'form'),
]


@tagged('post_install', '-at_install', MODULE)
class TestCustomerGoodsPickupIntegrity(TransactionCase):
    """Every view parses and the model wiring is intact."""

    def test_01_every_view_renders(self):
        for model, view_type in VIEWS:
            with self.subTest(view=view_type, model=model):
                view = self.env[model].get_view(view_type=view_type)
                self.assertIn('arch', view)

    def test_02_inherited_views_render(self):
        for model in ('account.move', 'stock.picking'):
            with self.subTest(model=model):
                view = self.env[model].get_view(view_type='form')
                self.assertIn('arch', view)

    def test_03_core_field_names_match_the_installed_version(self):
        """Odoo 20 renamed a few stock fields; assert the ones we rely on."""
        move_fields = self.env['stock.move']._fields
        self.assertIn('uom_id', move_fields)
        self.assertNotIn('product_uom', move_fields)
        self.assertIn('picked', move_fields)
        self.assertIn('origin_returned_move_id', move_fields)
        move_fields = self.env['account.move']._fields
        self.assertIn('reversed_entry_id', move_fields)
        self.assertIn('out_refund', dict(
            self.env['account.move']._fields['move_type'].selection))

    def test_04_model_inheritance_is_registered(self):
        self.assertIn('message_ids', self.env['customer.goods.pickup']._fields)
        self.assertIn('activity_ids',
                      self.env['customer.goods.pickup']._fields)
        self.assertIn('message_ids', self.env['customer.goods.receipt']._fields)
        self.assertIn('goods_pickup_id', self.env['account.move']._fields)
        self.assertIn('goods_pickup_receipt_id', self.env['account.move']._fields)
        self.assertIn('goods_pickup_receipt_line_id',
                      self.env['account.move.line']._fields)
        self.assertIn('goods_receipt_id', self.env['stock.picking']._fields)

    def test_05_sequences_produce_distinct_references(self):
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        customer = self.env['res.partner'].create({
            'name': 'Sequence Ltd', 'customer_rank': 1})
        product = self.env['product.product'].create({
            'name': 'Sequence widget', 'type': 'consu', 'is_storable': True,
            'list_price': 4.0,
            'categ_id': self.env.ref('product.product_category_goods').id})
        journal = self.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', self.env.company.id)],
            limit=1)
        vals = {
            'partner_id': customer.id,
            'warehouse_id': warehouse.id,
            'dest_location_id': warehouse.lot_stock_id.id,
        }
        references = []
        for _index in range(2):
            invoice = self.env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': customer.id,
                'journal_id': journal.id,
                'invoice_line_ids': [Command.create({
                    'product_id': product.id, 'quantity': 1,
                    'price_unit': 4.0})],
            })
            invoice.action_post()
            references.append(self.env['customer.goods.pickup'].create(
                dict(vals, invoice_id=invoice.id)).name)
        self.assertNotEqual(references[0], references[1])
        for reference in references:
            self.assertTrue(reference.startswith('PICK-'), reference)

    def test_05b_receipt_sequence_produces_distinct_references(self):
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        names = set()
        for index in range(2):
            names.add(self.env['ir.sequence'].next_by_code(
                'customer.goods.receipt', 'code'))
        self.assertEqual(len(names), 2)
        for name in names:
            self.assertTrue(name.startswith('RCP-'), name)
        self.assertTrue(warehouse)

    def test_05c_settings_round_trip_through_the_real_form(self):
        """The settings form must actually save.

        ``res.config.settings.set_values`` called ``ir.config_parameter.set_param``,
        a method Odoo 20 removed, so saving the form raised an AttributeError
        and took the whole form down with it.
        """
        # The company structure is created lazily the first time it is needed, so
        # create it here and check it is what the module really uses.
        self.env['stock.location']._get_quarantine_location(self.env.company)
        self.env['stock.location']._get_awaiting_customer_location(self.env.company)
        location = self.env['stock.location'].sudo().search([
            ('id', 'child_of', self.env.ref(
                'customer_goods_pickup.stock_location_customer_returns').id),
            ('name', '!=', 'Customer Returns'),
        ], limit=1)
        self.assertTrue(
            location, 'the module must maintain its own returns structure')
        settings = self.env['res.config.settings'].create({
            'cognify_goods_quarantine_location_id': location.id,
            'cognify_goods_pickup_notify_rep': False,
        })
        settings.execute()

        params = self.env['ir.config_parameter'].sudo()
        self.assertEqual(
            params.get_int('customer_goods_pickup.quarantine_location_id'),
            location.id)
        self.assertFalse(
            params.get_bool('customer_goods_pickup.notify_sales_rep', True))

        # ... and the values come back into a freshly opened form.
        reloaded = self.env['res.config.settings'].create({})
        reloaded = self.env['res.config.settings'].default_get(
            ['cognify_goods_quarantine_location_id',
             'cognify_goods_pickup_notify_rep'])
        self.assertEqual(
            reloaded['cognify_goods_quarantine_location_id'], location.id)
        self.assertFalse(reloaded['cognify_goods_pickup_notify_rep'])

        # Restore the defaults so the rest of the suite is unaffected.
        params.set_int('customer_goods_pickup.quarantine_location_id', 0)
        params.set_bool('customer_goods_pickup.notify_sales_rep', True)

    def test_06_groups_are_declared_on_the_right_privileges(self):
        group_model = self.env['res.groups']
        for xmlid, privilege in (
            ('customer_goods_pickup.group_goods_pickup_sales_rep',
             'sales_team.res_groups_privilege_sales'),
            ('customer_goods_pickup.group_goods_pickup_store_receiver',
             'stock.res_groups_privilege_inventory'),
            ('customer_goods_pickup.group_goods_pickup_accountant',
             'account.res_groups_privilege_accounting'),
            ('customer_goods_pickup.group_goods_pickup_manager',
             'stock.res_groups_privilege_inventory'),
        ):
            group = self.env.ref(xmlid)
            self.assertTrue(group.exists(), '%s is missing' % xmlid)
            self.assertEqual(group.privilege_id, self.env.ref(privilege),
                             '%s has the wrong privilege' % xmlid)
        # The accountant group must not hand out stock or posting rights.
        accountant = self.env.ref(
            'customer_goods_pickup.group_goods_pickup_accountant')
        implied = accountant.implied_ids
        self.assertNotIn(
            self.env.ref('stock.group_stock_manager'), implied)
        self.assertNotIn(
            self.env.ref('account.group_account_manager').id,
            implied.ids)
        # The goods pickup manager must not be a system administrator: on
        # Odoo 20 every new user is handed base.default_user_group, which in a
        # standard install already contains the inventory administrator, so
        # implying base.group_system (or promoting every stock manager) made
        # every employee a goods pickup manager.
        manager = self.env.ref(
            'customer_goods_pickup.group_goods_pickup_manager')
        self.assertNotIn(
            self.env.ref('base.group_system').id, manager.all_implied_ids.ids)
        stock_manager = self.env.ref('stock.group_stock_manager')
        self.assertNotIn(
            manager.id, stock_manager.implied_ids.ids,
            'stock managers must not be auto-promoted to goods pickup managers')

    def test_07_eligible_invoice_domain(self):
        domain = self.env['customer.goods.pickup']._eligible_invoice_domain()
        self.assertIn(('move_type', '=', 'out_invoice'), domain)
        self.assertIn(('state', '=', 'posted'), domain)
        self.assertIn(('company_id', '=', self.env.company.id), domain)

    def test_08_access_rights_exist_for_every_model(self):
        models = (
            'customer.goods.pickup', 'customer.goods.pickup.line',
            'customer.goods.receipt', 'customer.goods.receipt.line',
            'customer.goods.pickup.add.lines',
            'customer.goods.receipt.receive',
            'customer.goods.receipt.reject',
            'customer.goods.pickup.cancel',
        )
        groups = {
            self.env.ref(
                'customer_goods_pickup.group_goods_pickup_manager').id}
        for model in models:
            with self.subTest(model=model):
                rules = self.env['ir.access'].search([
                    ('model_id.model', '=', model),
                    ('group_id', 'in', groups),
                ])
                self.assertTrue(rules,
                                'no manager access row for %s' % model)
                granted = set()
                for rule in rules:
                    for flag in ('for_read', 'for_write', 'for_create',
                                 'for_unlink'):
                        if rule[flag]:
                            granted.add(flag)
                self.assertEqual(
                    granted,
                    {'for_read', 'for_write', 'for_create', 'for_unlink'},
                    'manager access for %s is incomplete' % model)

    def test_09_chatter_is_available_on_the_operational_documents(self):
        for model in ('customer.goods.pickup', 'customer.goods.receipt'):
            with self.subTest(model=model):
                self.assertTrue(self.env[model]._fields['message_ids'])
                self.assertTrue(self.env[model]._fields['message_follower_ids'])

    def test_10_wizards_only_offer_outstanding_goods(self):
        """The receiving screen is built from what is still outstanding."""
        warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', self.env.company.id)], limit=1)
        customer = self.env['res.partner'].create({
            'name': 'Integrity Ltd', 'customer_rank': 1})
        product = self.env['product.product'].create({
            'name': 'Integrity widget', 'type': 'consu', 'is_storable': True,
            'list_price': 9.0,
            'categ_id': self.env.ref('product.product_category_goods').id})
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': customer.id,
            'journal_id': self.env['account.journal'].search(
                [('type', '=', 'sale'), ('company_id', '=', self.env.company.id)],
                limit=1).id,
            'invoice_line_ids': [Command.create({
                'product_id': product.id, 'quantity': 5, 'price_unit': 9.0})],
        })
        invoice.action_post()
        line = invoice.invoice_line_ids[0]
        pickup = self.env['customer.goods.pickup'].create({
            'partner_id': customer.id,
            'invoice_id': invoice.id,
            'warehouse_id': warehouse.id,
            'dest_location_id': warehouse.lot_stock_id.id,
            'line_ids': [Command.create({
                'invoice_line_id': line.id,
                'product_id': product.id,
                'product_uom_id': line.product_uom_id.id,
                'qty_collected': 3})],
        })

        add_lines = self.env['customer.goods.pickup.add.lines'].create(
            {'pickup_id': pickup.id})
        offered = add_lines.line_ids.filtered(
            lambda record: record.invoice_line_id == line)
        self.assertEqual(len(offered), 1)
        self.assertEqual(offered.eligible_qty, 5.0)
        self.assertFalse(offered.selected)

        pickup.action_confirm_collection()
        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        self.assertEqual(len(wizard.line_ids), 1)
        self.assertEqual(wizard.line_ids[0].qty_outstanding, 3.0)
        self.assertEqual(wizard.dest_location_id, warehouse.lot_stock_id)