"""Regression tests for the defects found in the production-readiness audit.

Every test here reproduces a failure that was observed against a freshly
installed database, so a failure of one of them is a real regression rather
than a hypothetical one.

The defects are grouped the way they were found: quantity arithmetic,
the receiving control chain, credit-note integrity, stock-move integrity,
permissions and the audit trail.
"""

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'customer_goods_pickup'


@tagged('post_install', '-at_install', MODULE)
class TestCustomerGoodsPickupAudit(TransactionCase):
    """Audit regressions: fractional quantities, custody and permissions."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        cls.sale_journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.tax = cls.env['account.tax'].search(
            [('type_tax_use', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.customer = cls.env['res.partner'].create({
            'name': 'Audit Ltd', 'customer_rank': 1})
        # The test users get *only* their own group: on Odoo 20 a user created
        # without explicit groups is handed ``base.default_user_group``, which
        # in this database already contains the inventory and accounting
        # administrator groups. Pinning the groups is what makes a passing
        # security assertion mean something.
        cls.store_user = cls._isolated_user('audit.store', 'group_goods_pickup_store_receiver')
        cls.accountant = cls._isolated_user('audit.acct', 'group_goods_pickup_accountant')
        cls.rep = cls._isolated_user('audit.rep', 'group_goods_pickup_sales_rep')

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _isolated_user(cls, login, group_xmlid):
        user = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': login, 'login': login,
                'company_id': cls.company.id,
                'company_ids': [(6, 0, [cls.company.id])],
                'group_ids': [Command.set([
                    cls.env.ref('customer_goods_pickup.%s' % group_xmlid).id])],
            })
        return user

    @classmethod
    def _product(cls, name, price=10.0, tracking=None):
        vals = {
            'name': name, 'type': 'consu', 'is_storable': True,
            'list_price': price, 'standard_price': price,
            'categ_id': cls.env.ref('product.product_category_goods').id,
            'taxes_id': [Command.set(cls.tax.ids)] if cls.tax else [],
        }
        if tracking:
            vals['tracking'] = tracking
        return cls.env['product.product'].create(vals)

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

    def _pickup(self, invoice, line_cmds):
        return self.env['customer.goods.pickup'].create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.warehouse.lot_stock_id.id,
            'line_ids': line_cmds,
        })

    @staticmethod
    def _pick_cmd(invoice_line, qty):
        return Command.create({
            'invoice_line_id': invoice_line.id,
            'product_id': invoice_line.product_id.id,
            'product_uom_id': invoice_line.product_uom_id.id,
            'qty_collected': qty,
        })

    def _on_hand(self, product):
        return sum(self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'child_of', self.warehouse.lot_stock_id.id),
        ]).mapped('quantity'))

    def _receive(self, pickup, counts, reject_whole=False):
        """counts: list of (received, accepted, reason) aligned with the wizard.

        ``reject_whole`` fills in the header-level reason and disposition that
        a full rejection needs on top of the per-line ones.
        """
        wizard = self.env['customer.goods.receipt.receive'].create(
            {'pickup_id': pickup.id})
        for index, line in enumerate(wizard.line_ids):
            received, accepted, reason = counts[index]
            line.write({'qty_received': received, 'qty_accepted': accepted})
            if accepted < received:
                line.write({'rejection_reason': reason or 'Damaged in transit'})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(lambda r: r.state == 'draft')[-1]
        if reject_whole:
            receipt.write({
                'rejection_reason': 'The whole delivery was turned down',
                'rejected_disposition': 'held',
            })
        receipt.action_validate_receipt()
        pickup.invalidate_recordset()
        return self.env['customer.goods.receipt'].browse(receipt.id)

    # ============================================ A. quantity arithmetic
    def test_a1_helpers_use_product_unit_precision_not_integer_precision(self):
        """A1: ``units.is_zero``/``compare`` must honour 'Product Unit'.

        They were built on ``precision_digits=0``, whose epsilon is ``1``:
        every quantity below 0.5 was "zero" and 1.4 compared equal to 1.45.
        """
        from odoo.addons.customer_goods_pickup import units
        self.assertEqual(units.unit_rounding(self.env), 0.01)
        self.assertFalse(units.is_zero(self.env, 0.4))
        self.assertFalse(units.is_zero(self.env, 0.01))
        self.assertTrue(units.is_zero(self.env, 0.0))
        self.assertTrue(units.is_zero(self.env, 0.004))
        # 1.4 and 1.45 are different numbers at two decimals. Under the old
        # ``precision_digits=0`` they rounded to the same integer and compared
        # equal, so an over-receipt of 1.45 against an outstanding 1.4 passed.
        self.assertEqual(units.compare(self.env, 1.4, 1.45), -1)
        self.assertEqual(units.compare(self.env, 10.2, 10.25), -1)
        self.assertEqual(units.compare(self.env, 1.44, 1.44), 0)
        self.assertTrue(units.greater(self.env, 1.45, 1.4))
        self.assertFalse(units.greater(self.env, 1.4, 1.45))
        self.assertFalse(units.greater(self.env, 1.45, 1.45))

    def test_a2_fractional_acceptance_is_recorded_stocked_and_creditable(self):
        """A2: accepting 0.4 of 2 units must not look like accepting nothing."""
        product = self._product('Fractional widget', 10.0)
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 2.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 2.0)])
        pickup.action_confirm_collection()
        before = self._on_hand(product)

        receipt = self._receive(pickup, [(2.0, 0.4, 'Rest was spoiled')])

        self.assertEqual(receipt.total_accepted_qty, 0.4)
        self.assertTrue(receipt.picking_id, '0.4 accepted units never got a stock move')
        self.assertAlmostEqual(self._on_hand(product) - before, 0.4, places=3)
        self.assertEqual(pickup.state, 'partially_rejected')
        receipt.action_approve_credit()
        receipt.action_create_credit_note()
        self.assertEqual(
            receipt.move_id.invoice_line_ids.filtered(
                lambda l: l.display_type == 'product').mapped('quantity'), [0.4])

    # ============================================ B. the receiving control chain
    def test_b1_create_cannot_forge_the_credit_approval(self):
        """B1: the accountant-only guard on ``write`` was missing on ``create``."""
        product = self._product('Forged approval widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 5.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 5.0)])
        pickup.action_confirm_collection()
        line = pickup.line_ids[0]

        with self.assertRaises(AccessError):
            self.env['customer.goods.receipt'].with_user(self.store_user).create({
                'pickup_id': pickup.id,
                'dest_location_id': self.warehouse.lot_stock_id.id,
                'credit_approval_state': 'approved',
                'credit_approval_by_id': self.accountant.id,
                'line_ids': [Command.create({
                    'pickup_line_id': line.id,
                    'invoice_line_id': line.invoice_line_id.id,
                    'qty_received': 5.0, 'qty_accepted': 5.0})],
            })

    def test_b2_a_receipt_cannot_be_forced_to_done_without_moving_stock(self):
        """B2: only ``action_validate_receipt`` may close a receipt.

        Writing ``state`` straight to ``done`` skipped the stock transfer, so a
        later credit note settled goods that were never in the warehouse.
        """
        product = self._product('Forced done widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 5.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 5.0)])
        pickup.action_confirm_collection()
        wizard = self.env['customer.goods.receipt.receive'].with_user(
            self.store_user).create({'pickup_id': pickup.id})
        wizard.action_confirm_receiving()
        receipt = pickup.receipt_ids.filtered(lambda r: r.state == 'draft')[-1]

        with self.assertRaises(UserError):
            receipt.with_user(self.store_user).write({'state': 'done'})

        self.assertEqual(receipt.state, 'draft')

    # ============================================ C. credit-note integrity
    def test_c1_credit_lines_cannot_be_edited_past_the_eligible_quantity(self):
        """C1: the quantity on a linked credit line is not editable.

        Editing the draft note from the eligible quantity up to a larger one
        and posting it credited more goods than the store ever accepted.
        """
        product = self._product('Tamper widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(4.0, 4.0, None)])
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit = receipt.move_id
        credit_line = credit.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product')

        with self.assertRaises(UserError):
            credit_line.with_user(self.accountant).write({'quantity': 40.0})
        self.assertEqual(credit_line.quantity, 4.0)

    def test_c2_credit_line_quantity_stays_bounded_for_every_writer(self):
        """C1: the bound is a server-side rule, not a UI one.

        Driving the write through ``account.move.line`` directly is the point:
        the guard has to hold for a script, a server action or an import, not
        only for the button that raised the note.
        """
        product = self._product('Tamper widget 2')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(4.0, 4.0, None)])
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit_line = receipt.move_id.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product')

        with self.assertRaises(UserError):
            self.env['account.move.line'].with_user(self.accountant).browse(
                credit_line.id).write({'quantity': 40.0})

        self.assertEqual(credit_line.quantity, 4.0)
        receipt.move_id.with_user(self.accountant).action_post()
        self.assertLessEqual(receipt.total_credited_qty, receipt.total_accepted_qty)
        self.assertEqual(receipt.total_credited_qty, 4.0)

    def test_c3_a_posted_credit_cannot_be_repointed_at_another_receipt_line(self):
        """C1: the receipt-line link is the duplicate guard, so it is frozen."""
        product = self._product('Repoint widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(4.0, 4.0, None)])
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit = receipt.move_id
        credit_line = credit.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product')
        credit.with_user(self.accountant).action_post()

        with self.assertRaises(UserError):
            credit_line.with_user(self.accountant).write(
                {'goods_pickup_receipt_line_id': False})
        self.assertEqual(credit_line.goods_pickup_receipt_line_id, receipt.line_ids[0])
        self.assertEqual(receipt.total_credited_qty, 4.0)

    def test_c4_the_approval_stamp_cannot_be_forged(self):
        """C2: only ``action_approve_credit`` may stamp who approved."""
        product = self._product('Stamp widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(4.0, 4.0, None)])

        with self.assertRaises(UserError):
            receipt.with_user(self.accountant).write({
                'credit_approval_by_id': self.store_user.id})
        self.assertFalse(receipt.credit_approval_by_id)

    # ============================================ D. stock-move integrity
    def test_d1_every_accepted_line_keeps_its_own_stock_move(self):
        """D1: Odoo merged the moves, so a line lost its link.

        Two invoice lines for the same product produced two receipt lines but a
        single merged stock move, so ``_reverse_stock_picking`` only ever sent
        part of the goods back out.
        """
        product = self._product('Merged widget')
        invoice = self._invoice([
            Command.create({'product_id': product.id, 'quantity': 2.0,
                            'price_unit': 10.0,
                            'tax_ids': [Command.set(product.taxes_id.ids)]}),
            Command.create({'product_id': product.id, 'quantity': 3.0,
                            'price_unit': 10.0,
                            'tax_ids': [Command.set(product.taxes_id.ids)]}),
        ])
        lines = invoice.invoice_line_ids
        pickup = self._pickup(invoice, [
            self._pick_cmd(lines[0], 2.0), self._pick_cmd(lines[1], 3.0)])
        pickup.action_confirm_collection()
        before = self._on_hand(product)

        receipt = self._receive(pickup, [(2.0, 2.0, None), (3.0, 3.0, None)])

        self.assertEqual(len(receipt.line_ids), 2)
        for line in receipt.line_ids:
            self.assertTrue(line.stock_move_id,
                            'receipt line %s lost its stock move link' % line.id)
        self.assertAlmostEqual(self._on_hand(product) - before, 5.0, places=3)

        receipt.action_reverse_stock_receipt()
        self.assertAlmostEqual(
            sum(receipt.reverse_picking_id.move_ids.mapped('product_uom_qty')),
            5.0, places=3,
            msg='the reversal did not cover every accepted unit')
        self.assertAlmostEqual(self._on_hand(product), before, places=3)

    # ============================================ E. permissions
    def test_e1_a_default_hired_user_is_not_a_goods_pickup_manager(self):
        """E1: the manager group must not escalate to ``base.group_system``.

        ``stock.group_stock_manager`` was made to imply the goods pickup
        manager, which implied ``base.group_system``. Odoo 20 hands every new
        user ``base.default_user_group``, which in this database contains the
        inventory administrator -- so every employee created in
        Inventory > Users became a goods pickup manager and a system
        administrator. This test drives the real path: it creates a user the
        way an administrator does, with no groups chosen by hand.
        """
        hired = self.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': 'audit.hired', 'login': 'audit.hired',
                'company_id': self.company.id,
                'company_ids': [(6, 0, [self.company.id])],
            })
        self.assertFalse(
            hired.has_group('customer_goods_pickup.group_goods_pickup_manager'),
            'a newly hired user is a goods pickup manager: %s'
            % hired.group_ids.mapped('full_name'))
        self.assertFalse(
            hired.has_group('base.group_system'),
            'a newly hired user is a system administrator: %s'
            % hired.group_ids.mapped('full_name'))

        # And the implication that produced it is gone.
        stock_manager = self.env.ref('stock.group_stock_manager')
        manager = self.env.ref('customer_goods_pickup.group_goods_pickup_manager')
        self.assertNotIn(manager, stock_manager.implied_ids)
        self.assertNotIn(self.env.ref('base.group_system'), manager.all_implied_ids)

    def test_e2_an_isolated_sales_rep_cannot_delete_confirmed_lines(self):
        """E2: ``pickup.write`` froze the lines, ``line.unlink`` did not."""
        product = self._product('Unlink widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.action_confirm_collection()

        with self.assertRaises(UserError):
            pickup.line_ids.with_user(self.rep).unlink()
        self.assertEqual(len(pickup.line_ids), 1)

    def test_e3_a_sales_rep_cannot_reassign_a_confirmed_pickup(self):
        """E2: the owner field was not in the freeze list either."""
        product = self._product('Reassign widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.sales_rep_id = self.rep.id
        pickup.action_confirm_collection()
        other = self._isolated_user('audit.rep2', 'group_goods_pickup_sales_rep')

        with self.assertRaises(UserError):
            pickup.with_user(self.rep).write({'sales_rep_id': other.id})
        self.assertEqual(pickup.sales_rep_id, self.rep)

    # ============================================ F. audit trail
    def test_f1_the_receiving_decision_is_written_to_the_chatter(self):
        """F1: accepting or rejecting left no trace in the document chatter."""
        product = self._product('Audit widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 6.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 6.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(6.0, 4.0, 'Two units were rusted through')])

        bodies = ' '.join((m.body or '') for m in receipt.message_ids).lower()
        self.assertIn('accept', bodies)
        self.assertIn('rust', bodies, 'the per-line rejection reason is not on the record')
        self.assertTrue(receipt.received_by_id)

    def test_f2_rejected_goods_keep_a_physical_custody_record(self):
        """F2: refused goods now have a real, traceable physical custody record.

        A previous audit found they were only a location *name* on the receipt.
        They are now moved into quarantine through a validated stock transfer,
        so the custody record is backed by a quant and a stock move — and the
        location sits outside every warehouse, so that quant is neither visible
        to availability nor reservable.
        """
        product = self._product('Custody widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 5.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 5.0)])
        pickup.action_confirm_collection()
        before = self._on_hand(product)
        receipt = self._receive(pickup, [(5.0, 0.0, 'Does not qualify for return')],
                                reject_whole=True)

        self.assertEqual(receipt.total_rejected_qty, 5.0)
        self.assertFalse(receipt.picking_id, 'nothing was accepted, so no stock receipt')
        self.assertEqual(self._on_hand(product), before)

        quarantine = receipt.rejected_location_id
        self.assertTrue(quarantine)
        self.assertEqual(quarantine.usage, 'internal')
        self.assertTrue(quarantine.cognify_goods_return)
        # The design constraint: outside the warehouse tree entirely. A quant
        # under WH/Stock would be visible to availability and reservation even
        # though it is a "quarantine" name.
        self.assertFalse(quarantine.warehouse_id)
        self.assertFalse(quarantine.replenish_location)
        self.assertTrue(receipt.rejected_disposition)
        self.assertTrue(receipt.line_ids[0].rejection_reason)

        # Real custody: a quant, a validated transfer, a stock move link.
        self.assertEqual(self.env['stock.quant'].search_count([
            ('product_id', '=', product.id),
            ('location_id', '=', quarantine.id)]), 1)
        self.assertEqual(receipt.quarantine_picking_id.state, 'done')
        self.assertEqual(receipt.quarantine_picking_id.location_dest_id, quarantine)
        self.assertEqual(len(receipt.quarantine_ids), 1)
        self.assertEqual(receipt.quarantine_ids.qty_pending, 5.0)
        self.assertEqual(receipt.quarantine_ids.qty_released, 0.0)
        self.assertEqual(receipt.quarantine_ids.stock_move_id.state, 'done')
        self.assertEqual(receipt.quarantine_ids.state, 'quarantine')
        self.assertEqual(receipt.quarantine_ids.picking_id, receipt.quarantine_picking_id)

        # ... but not sellable stock: availability ignores it entirely, because
        # the location is outside every warehouse.
        self.assertEqual(product.with_context(
            warehouse_id=self.warehouse.id).qty_available, 0.0)
        self.assertEqual(product.free_qty, 0.0)
        self.assertEqual(product.virtual_available, 0.0)

        self.assertEqual(receipt.credit_state, 'not_eligible')
        self.assertEqual(receipt.total_credited_qty, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_to_credit, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_reinspected_accepted, 0.0)

    # ============================================ G. accounting fidelity
    def test_g1_tax_exclusive_discounted_invoice_credits_exactly(self):
        """G1: no tax, a 20% discount: the credit must match the invoice."""
        product = self._product('Discounted widget', 12.5)
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 4.0, 'price_unit': 12.5,
            'discount': 20.0, 'tax_ids': []})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 4.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(4.0, 4.0, None)])
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()

        credit = receipt.move_id
        credit_line = credit.invoice_line_ids.filtered(
            lambda l: l.display_type == 'product')
        invoice_line = invoice.invoice_line_ids[0]
        self.assertFalse(credit_line.tax_ids)
        self.assertEqual(credit_line.discount, 20.0)
        self.assertAlmostEqual(credit_line.price_subtotal, 40.0, places=2)
        self.assertAlmostEqual(credit.amount_total, invoice.amount_total, places=2)

        credit.with_user(self.accountant).action_post()
        self.assertAlmostEqual(invoice.amount_residual, 0.0, places=2)

    def test_g2_a_cancelled_draft_credit_releases_the_reservation(self):
        """G2: cancelling the draft note must make the quantity creditable again."""
        product = self._product('Reuse widget')
        invoice = self._invoice([Command.create({
            'product_id': product.id, 'quantity': 6.0, 'price_unit': 10.0,
            'tax_ids': [Command.set(product.taxes_id.ids)]})])
        pickup = self._pickup(invoice, [self._pick_cmd(invoice.invoice_line_ids[0], 6.0)])
        pickup.action_confirm_collection()
        receipt = self._receive(pickup, [(6.0, 6.0, None)])
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()

        first = receipt.move_id
        first.with_user(self.accountant).button_cancel()
        self.assertEqual(receipt.line_ids[0].qty_credited, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_to_credit, 6.0)

        receipt.action_create_credit_note()
        second = receipt.move_id
        self.assertNotEqual(first, second)
        second.with_user(self.accountant).action_post()
        self.assertEqual(receipt.total_credited_qty, 6.0)

        # ... and now that the quantity is credited, nothing more may be raised.
        with self.assertRaises(UserError):
            receipt.action_create_credit_note()
