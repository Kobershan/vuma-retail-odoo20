"""Permission tests for the customer goods pickup workflow.

These exercise the server-side guards rather than the views: a representative
must not be able to receive goods or touch money, store staff must not be able
to approve a credit, and a record from another company must stay out of reach.

The users are created with *only* the module's own group (and whatever Odoo
implies with it), so a test that passes here means the module genuinely
separates the roles rather than relying on somebody already being an admin.
"""

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'customer_goods_pickup'


@tagged('post_install', '-at_install', MODULE)
class TestCustomerGoodsPickupSecurity(TransactionCase):
    """Roles are separated on the server, not only hidden in the views."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.sale_journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.customer = cls.env['res.partner'].create({
            'name': 'Permissions Ltd', 'customer_rank': 1})
        cls.product = cls.env['product.product'].create({
            'name': 'Permission widget',
            'type': 'consu',
            'is_storable': True,
            'list_price': 15.0,
            'categ_id': cls.env.ref('product.product_category_goods').id,
        })

        cls.rep = cls._user('perm.rep', 'group_goods_pickup_sales_rep')
        cls.store = cls._user('perm.store', 'group_goods_pickup_store_receiver')
        cls.accountant = cls._user('perm.acct', 'group_goods_pickup_accountant')
        cls.manager = cls._user('perm.manager', 'group_goods_pickup_manager')

        invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': cls.customer.id,
            'invoice_date': fields.Date.context_today(cls.env.company),
            'journal_id': cls.sale_journal.id,
            'invoice_line_ids': [Command.create({
                'product_id': cls.product.id,
                'quantity': 5,
                'price_unit': 15.0,
            })],
        })
        invoice.action_post()
        cls.invoice = invoice

    @classmethod
    def _user(cls, login, group_xmlid):
        """A user holding *only* the group under test.

        The groups are pinned with ``Command.set`` on purpose. Odoo 20 gives a
        user created without explicit groups ``base.default_user_group``,
        which in this database contains the inventory and accounting
        administrator groups. Appending the group under test with
        ``Command.link`` left those in place, so these users turned out to
        hold the goods pickup manager group too and every permission
        assertion below passed or failed for the wrong reason.
        """
        user = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': login,
                'login': login,
                'company_id': cls.company.id,
                'company_ids': [(6, 0, [cls.company.id])],
                'group_ids': [Command.set([
                    cls.env.ref('customer_goods_pickup.%s' % group_xmlid).id])],
            })
        return user

    def _pickup(self, rep=None, qty=2):
        invoice_line = self.invoice.invoice_line_ids[0]
        return self.env['customer.goods.pickup'].create({
            'partner_id': self.customer.id,
            'invoice_id': self.invoice.id,
            'sales_rep_id': (rep or self.rep).id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.warehouse.lot_stock_id.id,
            'line_ids': [Command.create({
                'invoice_line_id': invoice_line.id,
                'product_id': self.product.id,
                'product_uom_id': invoice_line.product_uom_id.id,
                'qty_collected': qty,
            })],
        })

    def _received_receipt(self):
        pickup = self._pickup()
        pickup.action_confirm_collection()
        wizard = self.env['customer.goods.receipt.receive'].with_user(
            self.store).create({'pickup_id': pickup.id})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(lambda r: r.state == 'draft')[-1]
        receipt.with_user(self.store).action_validate_receipt()
        return pickup, receipt

    # ================================================================ tests
    def test_01a_representative_can_read_their_own_lines_after_confirmation(self):
        """A rep keeps sight of what they collected and what came back."""
        pickup = self._pickup()
        pickup.action_confirm_collection()
        rep = self.env['customer.goods.pickup'].with_user(self.rep)
        lines = rep.browse(pickup.id).line_ids
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines.qty_collected, 2)

        wizard = self.env['customer.goods.receipt.receive'].with_user(
            self.store).create({'pickup_id': pickup.id})
        wizard.action_confirm_receiving()
        receipt = self.env['customer.goods.receipt'].with_user(
            self.store).browse(
                pickup.receipt_ids.filtered(
                    lambda r: r.state == 'draft')[-1].id)
        receipt.action_validate_receipt()

        # The rep can read the outcome, including the rejection reason.
        rep_lines = self.env['customer.goods.receipt.line'].with_user(
            self.rep).search([('receipt_id', '=', receipt.id)])
        self.assertEqual(len(rep_lines), 1)
        self.assertEqual(rep_lines.qty_accepted, 2)

        # ... but cannot change it.
        with self.assertRaises(UserError):
            rep_lines.write({'qty_accepted': 0})

    def test_01_representative_only_sees_their_own_pickups(self):
        other_rep = self._user('perm.rep2', 'group_goods_pickup_sales_rep')
        mine = self._pickup()
        theirs = self._pickup(rep=other_rep, qty=1)

        visible = self.env['customer.goods.pickup'].with_user(self.rep).search([])
        self.assertIn(mine, visible)
        self.assertNotIn(theirs, visible)

        visible_for_all = self.env['customer.goods.pickup'].with_user(
            self.store).search([])
        self.assertIn(mine, visible_for_all)
        self.assertIn(theirs, visible_for_all)

    def test_02_representative_cannot_open_the_receiving_screen(self):
        pickup = self._pickup()
        pickup.action_confirm_collection()
        with self.assertRaises(AccessError):
            pickup.with_user(self.rep).action_start_receiving()

    def test_03_representative_cannot_validate_a_stock_receipt(self):
        pickup, receipt = self._received_receipt()
        with self.assertRaises(AccessError):
            receipt.with_user(self.rep).action_validate_receipt()
        self.assertEqual(pickup.state, 'received')

    def test_04_representative_cannot_approve_or_raise_a_credit(self):
        _pickup, receipt = self._received_receipt()
        with self.assertRaises(AccessError):
            receipt.with_user(self.rep).action_approve_credit()
        with self.assertRaises(AccessError):
            receipt.with_user(self.rep).action_create_credit_note()
        self.assertEqual(receipt.credit_state, 'awaiting_approval')
        self.assertFalse(receipt.move_ids)
        self.assertEqual(receipt.total_credited_qty, 0.0)

    def test_05_store_staff_cannot_touch_the_credit_workflow(self):
        _pickup, receipt = self._received_receipt()
        self.assertNotIn(
            self.env.ref(
                'customer_goods_pickup.group_goods_pickup_accountant').id,
            self.store.group_ids.ids)
        with self.assertRaises((AccessError, UserError)):
            receipt.with_user(self.store).write(
                {'credit_approval_state': 'approved'})
        with self.assertRaises(AccessError):
            receipt.with_user(self.store).action_approve_credit()
        with self.assertRaises(AccessError):
            receipt.with_user(self.store).action_create_credit_note()

    def test_06_store_staff_cannot_change_the_count_after_validation(self):
        _pickup, receipt = self._received_receipt()
        with self.assertRaises(UserError):
            receipt.with_user(self.store).line_ids[0].write({'qty_accepted': 0})

    def test_07_accountant_owns_the_credit_and_nothing_else(self):
        _pickup, receipt = self._received_receipt()
        # The accountant can run the credit workflow.
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        self.assertEqual(receipt.move_id.state, 'draft')
        # ... but cannot re-count the goods. Either the access rights or the
        # module's own freeze stops them; both are acceptable answers here.
        with self.assertRaises((AccessError, UserError)):
            receipt.with_user(self.accountant).line_ids[0].write(
                {'qty_accepted': 0})
        with self.assertRaises((AccessError, UserError)):
            receipt.with_user(self.accountant).write(
                {'dest_location_id': self.warehouse.view_location_id.id})

    def test_08_store_staff_cannot_reverse_stock(self):
        _pickup, receipt = self._received_receipt()
        with self.assertRaises(AccessError):
            receipt.with_user(self.store).action_reverse_stock_receipt()
        self.assertFalse(receipt.is_stock_reversed)

    def test_09_manager_can_reverse_stock(self):
        _pickup, receipt = self._received_receipt()
        on_hand = sum(self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', 'child_of', self.warehouse.lot_stock_id.id),
        ]).mapped('quantity'))
        self.assertEqual(on_hand, 2)

        receipt.with_user(self.manager).action_reverse_stock_receipt()

        self.assertTrue(receipt.is_stock_reversed)
        remaining = sum(self.env['stock.quant'].search([
            ('product_id', '=', self.product.id),
            ('location_id', 'child_of', self.warehouse.lot_stock_id.id),
        ]).mapped('quantity'))
        self.assertEqual(remaining, 0)
        self.assertEqual(receipt.state, 'done')

    def test_10_records_from_another_company_are_out_of_reach(self):
        """Multi-company: nothing from another company can back a pickup."""
        other_company = self.env['res.company'].create({'name': 'Second Company'})
        other_warehouse = self.env['stock.warehouse'].search(
            [('company_id', '=', other_company.id)], limit=1) \
            or self.env['stock.warehouse'].create({
                'name': 'Second WH', 'code': 'WH2',
                'company_id': other_company.id})
        self.assertNotEqual(other_warehouse.company_id, self.company)

        # The invoice domain is always scoped to the company in hand.
        domain = self.env['customer.goods.pickup'].with_company(
            self.company)._eligible_invoice_domain()
        self.assertIn(('company_id', '=', self.company.id), domain)

        # A pickup cannot be pointed at another company's warehouse, and a
        # receipt cannot be received into one either: Odoo's check-company
        # rule on the field refuses both.
        with self.assertRaises((UserError, ValidationError)):
            self.env['customer.goods.pickup'].create({
                'partner_id': self.customer.id,
                'invoice_id': self.invoice.id,
                'warehouse_id': other_warehouse.id,
                'dest_location_id': other_warehouse.lot_stock_id.id,
            })
        with self.assertRaises((UserError, ValidationError)):
            self.env['customer.goods.pickup'].create({
                'partner_id': self.customer.id,
                'invoice_id': self.invoice.id,
                'warehouse_id': self.warehouse.id,
                'dest_location_id': other_warehouse.lot_stock_id.id,
            })
        own_pickup = self._pickup()
        with self.assertRaises((UserError, ValidationError)):
            self.env['customer.goods.receipt'].create({
                'pickup_id': own_pickup.id,
                'dest_location_id': other_warehouse.lot_stock_id.id,
            })