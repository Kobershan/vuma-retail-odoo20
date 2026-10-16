"""End-to-end tests for the customer goods pickup and receiving workflow.

The scenarios follow the path a shop actually walks:

1. a representative raises a pickup against a posted customer invoice;
2. collection is confirmed, and nothing moves in stock or in the accounts;
3. the store receives the goods and accepts, part-accepts or rejects them;
4. only the accepted quantity enters stock, through a real incoming transfer;
5. an accountant approves the credit and raises the customer credit note;
6. posting it moves the customer's balance through Odoo's own accounting.

Everything runs inside a transaction that is rolled back at the end, and
nothing is faked: stock goes through ``stock.picking``, the credit note goes
through ``account.move._reverse_moves`` and the customer balance is read from
``account.move.line``.
"""

from odoo import Command, fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = 'customer_goods_pickup'


@tagged('post_install', '-at_install', MODULE)
class TestCustomerGoodsPickup(TransactionCase):
    """Covers the whole pickup → receipt → credit-note path."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.warehouse = cls.env['stock.warehouse'].search(
            [('company_id', '=', cls.company.id)], limit=1)
        assert cls.warehouse, 'this database needs at least one warehouse'

        cls.sale_journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.tax = cls.env['account.tax'].search(
            [('type_tax_use', '=', 'sale'), ('company_id', '=', cls.company.id)],
            limit=1)

        cls.customer = cls.env['res.partner'].create({
            'name': 'Greensmith Hardware',
            'email': 'accounts@greensmith.example',
            'customer_rank': 1,
        })
        cls.other_customer = cls.env['res.partner'].create({
            'name': 'Northside Supplies',
            'email': 'ap@northside.example',
            'customer_rank': 1,
        })
        cls.rep = cls._create_user('pickup.rep', 'pickup.rep@example.com',
                                  'group_goods_pickup_sales_rep')
        cls.store_clerk = cls._create_user('pickup.clerk', 'clerk@example.com',
                                           'group_goods_pickup_store_receiver')
        cls.accountant = cls._create_user('pickup.acct', 'acct@example.com',
                                          'group_goods_pickup_accountant')

        cls.brake_pads = cls._make_product('Brake pads', 'BP-1', list_price=25.0)
        cls.air_filter = cls._make_product('Air filter', 'AF-1', list_price=40.0)

        cls.invoice = cls._post_invoice([(cls.brake_pads, 10), (cls.air_filter, 5)])
        cls.store_location = cls.warehouse.lot_stock_id

    # ------------------------------------------------------------- fixtures
    @classmethod
    def _create_user(cls, login, email, group_xmlid=None):
        """A user holding only the group under test.

        ``Command.set`` rather than ``Command.link``: on Odoo 20 a user
        created without explicit groups is handed
        ``base.default_user_group``, which in this database already contains
        the inventory and accounting administrator groups. Linking left those
        behind, so the "sales representative" below was also a stock manager
        and a goods pickup manager, and the freeze the tests check never
        engaged.
        """
        groups = [Command.set([cls.env.ref(
            'customer_goods_pickup.%s' % group_xmlid).id])] if group_xmlid else []
        user = cls.env['res.users'].with_context(
            no_reset_password=True).create({
                'name': login,
                'login': login,
                'email': email,
                'company_id': cls.company.id,
                'company_ids': [(6, 0, [cls.company.id])],
                'group_ids': groups,
            })
        return user

    @classmethod
    def _make_product(cls, name, code, list_price, tracking=None):
        vals = {
            'name': name,
            'default_code': code,
            'type': 'consu',
            'is_storable': True,
            'list_price': list_price,
            'standard_price': list_price,
            'categ_id': cls.env.ref('product.product_category_goods').id,
            'taxes_id': [(6, 0, cls.tax.ids)] if cls.tax else [],
        }
        # Odoo 20 tracks by quantity unless a lot or serial method is picked,
        # so 'untracked' means simply not setting the field.
        if tracking:
            vals['tracking'] = tracking
        return cls.env['product.product'].create(vals)

    @classmethod
    def _post_invoice(cls, lines, partner=None):
        """Post a real customer invoice the way the sales module would."""
        invoice = cls.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': (partner or cls.customer).id,
            'invoice_date': fields.Date.context_today(cls.env.company),
            'journal_id': cls.sale_journal.id,
            'invoice_line_ids': [
                Command.create({
                    'product_id': product.id,
                    'quantity': qty,
                    'price_unit': product.list_price,
                    'tax_ids': [Command.set(product.taxes_id.ids)],
                    'name': product.name,
                }) for product, qty in lines
            ],
        })
        invoice.action_post()
        return invoice

    def _pickup(self, invoice=None, rep=None, qty_brakes=0, qty_filters=0):
        """Create a draft pickup with the requested collected quantities.

        The record is written as the representative but handed back in the
        administrator's hands: the assertions are about what the module
        recorded, and reading a record through two different users in one
        transaction would only prove that Odoo caches per user. The
        permission rules themselves are covered by the security test class.
        """
        invoice = invoice or self.invoice
        rep = rep or self.rep
        pickup = self.env['customer.goods.pickup'].with_user(rep).create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'sales_rep_id': rep.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.store_location.id,
            'pickup_reason': 'customer_request',
        })
        wanted = {
            self.brake_pads.id: qty_brakes,
            self.air_filter.id: qty_filters,
        }
        commands, sequence = [], 10
        for invoice_line in invoice.invoice_line_ids:
            qty = wanted.get(invoice_line.product_id.id)
            if not qty:
                continue
            commands.append(Command.create({
                'invoice_line_id': invoice_line.id,
                'product_id': invoice_line.product_id.id,
                'product_uom_id': invoice_line.product_uom_id.id,
                'qty_collected': qty,
                'return_reason': 'customer_request',
                'sequence': sequence,
            }))
            sequence += 10
        if commands:
            pickup.write({'line_ids': commands})
        return self.env['customer.goods.pickup'].browse(pickup.id)

    def _invoice_line(self, invoice, product):
        return invoice.invoice_line_ids.filtered(
            lambda line: line.product_id == product)

    def _line(self, pickup, product):
        return pickup.line_ids.filtered(lambda line: line.product_id == product)

    def _receipt_line(self, receipt, product):
        return receipt.line_ids.filtered(lambda line: line.product_id == product)

    # -------------------------------------------------------------- helpers
    def _on_hand(self, product):
        quants = self.env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', 'child_of', self.store_location.id),
        ])
        return sum(quants.mapped('quantity'))

    def _receivable(self, invoice):
        """What the customer still owes on this invoice."""
        return invoice.amount_residual

    def _customer_balance(self, partner):
        """The customer's whole receivable balance, straight from the ledger."""
        return sum(self.env['account.move.line'].search([
            ('partner_id', '=', partner.id),
            ('account_type', '=', 'asset_receivable'),
            ('parent_state', '=', 'posted'),
            ('company_id', '=', self.company.id),
        ]).mapped('balance'))

    def _draft_receipt(self, pickup):
        """Open the receiving sheet without validating it."""
        wizard = self.env['customer.goods.receipt.receive'].with_user(
            self.store_clerk).create({'pickup_id': pickup.id})
        wizard.action_confirm_receiving()
        return self._draft_of(pickup)

    def _draft_of(self, pickup):
        """The newest un-validated receipt, read with full rights.

        The assertions are about what the module recorded, not about what the
        representative may read, so the record is fetched as the administrator.
        """
        receipt = pickup.receipt_ids.filtered(lambda r: r.state == 'draft')[-1]
        return self.env['customer.goods.receipt'].browse(receipt.id)

    def _receive(self, pickup, accepted=None, received=None):
        """Walk the store side: open the receiving sheet, count, validate."""
        wizard = self.env['customer.goods.receipt.receive'].with_user(
            self.store_clerk).create({'pickup_id': pickup.id})
        for line in wizard.line_ids:
            values = {}
            if received is not None:
                values['qty_received'] = received
            if accepted is not None:
                values['qty_accepted'] = accepted
            if values:
                line.write(values)
            if line.qty_received > line.qty_accepted \
                    and not line.rejection_reason:
                line.write({'rejection_reason': 'Damaged in transit'})
        wizard.action_confirm_receiving()
        receipt = self._draft_of(pickup)
        receipt.with_user(self.store_clerk).action_validate_receipt()
        return self._settled(pickup, receipt)

    def _receive_part(self, pickup, product, received, accepted, reason=None):
        """Present part of a line and validate."""
        wizard = self.env['customer.goods.receipt.receive'].with_user(
            self.store_clerk).create({'pickup_id': pickup.id})
        line = wizard.line_ids.filtered(lambda line: line.product_id == product)
        line.write({'qty_received': received, 'qty_accepted': accepted})
        if accepted < received:
            line.write({'rejection_reason': reason or 'Damaged in transit'})
        wizard.action_confirm_receiving()
        receipt = self._draft_of(pickup)
        receipt.with_user(self.store_clerk).action_validate_receipt()
        return self._settled(pickup, receipt)

    def _settled(self, pickup, receipt):
        """Drop the counters the store's own write left stale.

        The receipt is validated under the store clerk's identity, and Odoo
        only invalidates the cache of the environment that wrote — so the
        pickup figures are refreshed here before anything reads them.
        """
        pickup.invalidate_recordset([
            'total_collected_qty', 'total_received_qty', 'total_accepted_qty',
            'total_rejected_qty', 'total_credited_qty', 'total_credit_amount',
        ])
        return receipt

    # ================================================================ tests
    def test_00_choosing_the_invoice_offers_every_line_of_it(self):
        """Choosing the invoice is the only step; the lines fill themselves.

        The representative used to pick an invoice and then have to open a
        picker to add the invoice's lines one at a time. Now the whole list is
        offered the moment the invoice is chosen, so all that is left is to say
        how much of each line is being taken.
        """
        product_lines = self.invoice.invoice_line_ids.filtered(
            lambda line: line.display_type == 'product' and line.product_id)
        self.assertGreaterEqual(len(product_lines), 2,
                                'this test needs an invoice of two or more lines')

        # What the browser does: build the record in memory and choose the
        # invoice, which runs the onchange before anything is saved.
        form = self.env['customer.goods.pickup'].new({
            'partner_id': self.invoice.partner_id.id,
            'invoice_id': self.invoice.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.store_location.id,
            'pickup_reason': 'customer_request',
        })
        form._onchange_invoice_id()

        self.assertEqual(len(form.line_ids), len(product_lines))
        self.assertEqual(
            form.line_ids.mapped('invoice_line_id'),
            product_lines,
            'every product line of the invoice should be offered, in order')
        # Nothing is pre-filled as collected: the amount is the rep's to say.
        self.assertTrue(all(not line.qty_collected for line in form.line_ids))
        # The figures they need in order to say it come from the invoice line.
        for line in form.line_ids:
            self.assertEqual(line.product_id, line.invoice_line_id.product_id)
            self.assertEqual(line.product_uom_id, line.invoice_line_id.product_uom_id)
            self.assertEqual(line.invoiced_qty, line.invoice_line_id.quantity)
            self.assertEqual(line.eligible_qty, line.invoice_line_id.quantity)

    def test_00b_a_line_left_at_zero_is_simply_not_collected(self):
        """Confirming keeps the lines that were filled in and drops the rest.

        With every line of the invoice on offer, a row the rep did not fill in
        means "not taking this one". Making them delete those rows themselves
        is a row of busywork and an easy way to lose the goods they did collect.
        """
        pickup = self._pickup(qty_brakes=4, qty_filters=0)
        # The fixture writes a line for each product it was given a quantity
        # for; add the other invoice line at zero, as the form would.
        other = self._invoice_line(self.invoice, self.air_filter)[0]
        pickup.write({'line_ids': [Command.create({
            'invoice_line_id': other.id,
            'product_id': other.product_id.id,
            'product_uom_id': other.product_uom_id.id,
            'qty_collected': 0.0,
            'sequence': 20,
        })]})
        self.assertEqual(len(pickup.line_ids), 2)

        pickup.action_confirm_collection()
        self.assertEqual(pickup.state, 'collected')
        self.assertEqual(len(pickup.line_ids), 1,
                         'the untouched line should not have been collected')
        self.assertEqual(pickup.line_ids.product_id, self.brake_pads)
        self.assertEqual(pickup.total_collected_qty, 4)

    def test_00c_a_pickup_with_nothing_filled_in_cannot_be_confirmed(self):
        """The one thing that is still refused: confirming nothing at all."""
        pickup = self._pickup(qty_brakes=4, qty_filters=0)
        pickup.line_ids.write({'qty_collected': 0.0})
        with self.assertRaises(UserError):
            pickup.action_confirm_collection()

    def test_01_create_pickup_against_posted_invoice(self):
        """1. A representative can raise a pickup on a posted invoice."""
        pickup = self._pickup(qty_brakes=4, qty_filters=1)
        self.assertTrue(pickup.name.startswith('PICK-'))
        self.assertEqual(pickup.state, 'draft')
        self.assertEqual(pickup.invoice_id, self.invoice)
        self.assertEqual(pickup.invoice_number, self.invoice.name)
        self.assertEqual(len(pickup.line_ids), 2)
        line = self._line(pickup, self.brake_pads)
        self.assertEqual(line.invoiced_qty, 10)
        self.assertEqual(line.eligible_qty, 10)
        self.assertEqual(line.price_unit, self.brake_pads.list_price)
        self.assertEqual(line.invoice_line_id,
                         self._invoice_line(self.invoice, self.brake_pads)[0])

    def test_02_only_eligible_invoices_can_be_selected(self):
        """2. Draft, cancelled or wrong-customer invoices are refused."""
        pickup_vals = {
            'partner_id': self.customer.id,
            'sales_rep_id': self.rep.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.store_location.id,
        }
        draft_invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': self.customer.id,
            'journal_id': self.sale_journal.id,
            'invoice_line_ids': [Command.create({
                'product_id': self.brake_pads.id,
                'quantity': 2,
                'price_unit': 10.0,
            })],
        })
        other_invoice = self._post_invoice([(self.brake_pads, 1)],
                                          partner=self.other_customer)

        with self.assertRaises(ValidationError):
            self.env['customer.goods.pickup'].create(
                dict(pickup_vals, invoice_id=draft_invoice.id))
        # Right invoice, wrong customer.
        with self.assertRaises(ValidationError):
            self.env['customer.goods.pickup'].create(
                dict(pickup_vals, invoice_id=other_invoice.id))
        # A posted invoice of the right shape but a cancelled one is refused.
        cancellable = self._post_invoice([(self.brake_pads, 1)])
        cancellable.button_cancel()
        with self.assertRaises(ValidationError):
            self.env['customer.goods.pickup'].create(
                dict(pickup_vals, invoice_id=cancellable.id))

        domain = self.env['customer.goods.pickup']._eligible_invoice_domain(
            self.customer)
        self.assertIn(('state', '=', 'posted'), domain)
        self.assertIn(('move_type', '=', 'out_invoice'), domain)
        eligible = self.env['account.move'].search(domain)
        self.assertIn(self.invoice, eligible)
        self.assertNotIn(draft_invoice, eligible)
        self.assertNotIn(cancellable, eligible)
        self.assertNotIn(other_invoice, eligible)

    def test_03_creating_a_pickup_moves_nothing(self):
        """3. Raising a pickup touches neither stock nor the receivable."""
        on_hand = self._on_hand(self.brake_pads)
        receivable = self._receivable(self.invoice)
        pickup = self._pickup(qty_brakes=4)
        self.assertEqual(self._on_hand(self.brake_pads), on_hand)
        self.assertEqual(self._receivable(self.invoice), receivable)
        self.assertEqual(pickup.total_accepted_qty, 0.0)
        self.assertFalse(pickup.receipt_ids)

    def test_04_confirming_collection_moves_nothing(self):
        """4. Confirming collection is still inert."""
        pickup = self._pickup(qty_brakes=4)
        on_hand = self._on_hand(self.brake_pads)
        receivable = self._receivable(self.invoice)

        pickup.action_confirm_collection()

        self.assertEqual(pickup.state, 'collected')
        self.assertTrue(pickup.collection_date)
        self.assertEqual(self._on_hand(self.brake_pads), on_hand)
        self.assertEqual(self._receivable(self.invoice), receivable)
        self.assertEqual(pickup.total_accepted_qty, 0.0)

        # The picked quantities are frozen from here on for the
        # representative; only a manager may make the correction.
        with self.assertRaises(UserError):
            pickup.with_user(self.rep).write({'line_ids': [Command.update(
                pickup.line_ids[0].id, {'qty_collected': 9})]})

    def test_05_full_acceptance_receives_into_stock(self):
        """5/13. Accepting receives the goods through a real stock transfer."""
        pickup = self._pickup(qty_brakes=6)
        pickup.action_confirm_collection()
        before = self._on_hand(self.brake_pads)

        receipt = self._receive(pickup)

        self.assertEqual(receipt.state, 'done')
        self.assertEqual(receipt.total_accepted_qty, 6)
        self.assertTrue(receipt.picking_id)
        self.assertEqual(receipt.picking_id.state, 'done')
        self.assertEqual(receipt.picking_id.origin, pickup.name)
        self.assertEqual(self._on_hand(self.brake_pads), before + 6)
        self.assertEqual(pickup.state, 'received')
        self.assertTrue(receipt.received_by_id)
        self.assertTrue(receipt.receipt_date)
        # The transfer points back at the receipt and the pickup.
        self.assertEqual(receipt.picking_id.goods_receipt_id, receipt)
        self.assertEqual(receipt.picking_id.goods_pickup_id, pickup)
        self.assertEqual(receipt.picking_id.goods_invoice_number,
                         self.invoice.name)

    def test_06_partial_acceptance_only_accepts_what_the_store_accepts(self):
        """6. 10 presented, 8 accepted, 2 rejected: only 8 reach stock."""
        pickup = self._pickup(qty_brakes=10)
        pickup.action_confirm_collection()
        before = self._on_hand(self.brake_pads)

        receipt = self._receive_part(
            pickup, self.brake_pads, received=10, accepted=8,
            reason='Two pads are cracked')

        self.assertEqual(receipt.total_received_qty, 10)
        self.assertEqual(receipt.total_accepted_qty, 8)
        self.assertEqual(receipt.total_rejected_qty, 2)
        self.assertEqual(self._on_hand(self.brake_pads), before + 8)
        self.assertEqual(pickup.state, 'partially_rejected')
        self.assertEqual(pickup.total_accepted_qty, 8)
        self.assertEqual(pickup.total_rejected_qty, 2)
        self.assertTrue(receipt.rejected_location_id)
        self.assertTrue(receipt.rejected_disposition)

    def test_07_full_rejection_does_not_increase_stock(self):
        """7. Turning the whole delivery down leaves stock untouched."""
        pickup = self._pickup(qty_brakes=5)
        pickup.action_confirm_collection()
        before = self._on_hand(self.brake_pads)

        draft = self._draft_receipt(pickup)
        wizard = self.env['customer.goods.receipt.reject'].with_user(
            self.store_clerk).create({
                'receipt_id': draft.id,
                'rejection_reason': 'Goods are in good condition and do not '
                                    'qualify for return.',
                'rejected_disposition': 'return_to_customer',
                'rejected_disposition_notes': 'Rep takes them back on Friday.',
            })
        wizard.action_reject()
        receipt = wizard.receipt_id
        receipt.action_validate_receipt()

        self.assertEqual(receipt.total_accepted_qty, 0.0)
        self.assertEqual(receipt.total_received_qty, 5)
        self.assertFalse(receipt.picking_id)
        self.assertEqual(self._on_hand(self.brake_pads), before)
        self.assertEqual(pickup.state, 'rejected')
        self.assertEqual(pickup.total_credited_qty, 0.0)
        self.assertEqual(receipt.credit_state, 'not_eligible')

    def test_08_rejection_requires_a_reason(self):
        """8. Neither a line nor a whole delivery is turned down silently."""
        pickup = self._pickup(qty_brakes=4)
        pickup.action_confirm_collection()
        receipt = self._draft_receipt(pickup)
        line = self._receipt_line(receipt, self.brake_pads)
        line.write({'qty_received': 4})
        line.write({'rejection_reason': 'One pad is rusted'})
        line.write({'qty_accepted': 3})
        self.assertEqual(line.qty_rejected, 1.0)
        # Taking the reason away again is refused.
        with self.assertRaises(ValidationError):
            line.write({'rejection_reason': False})

        # Turning the whole delivery down needs an overall reason as well:
        # the per-line reasons above are not enough on their own.
        line.write({'qty_accepted': 0})
        receipt.write({'rejection_reason': False})
        with self.assertRaises(UserError):
            receipt.action_validate_receipt()
        receipt.write({
            'rejection_reason': 'Goods do not qualify for return',
            'rejected_disposition': 'return_to_customer',
        })
        receipt.action_validate_receipt()
        self.assertEqual(receipt.total_accepted_qty, 0.0)
        self.assertEqual(pickup.state, 'rejected')

    def test_09_rejected_quantity_is_never_credited(self):
        """9. Only the accepted quantity is eligible; rejected is not."""
        pickup = self._pickup(qty_brakes=6)
        pickup.action_confirm_collection()
        receipt = self._receive_part(
            pickup, self.brake_pads, received=6, accepted=4,
            reason='Two pads seized')

        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        receipt_line = self._receipt_line(receipt, self.brake_pads)
        # The note reserves the accepted quantity but has credited none of it.
        self.assertEqual(receipt_line.qty_credit_pending, 4.0)
        self.assertEqual(receipt_line.qty_credited, 0.0)
        self.assertEqual(receipt_line.qty_to_credit, 0.0)
        credit = receipt.move_id
        self.assertEqual(credit.move_type, 'out_refund')
        self.assertEqual(sum(
            credit.invoice_line_ids.filtered(
                lambda line: line.product_id == self.brake_pads
            ).mapped('quantity')), 4.0)
        # The pickup as a whole may only ever credit four.
        self.assertEqual(pickup.total_accepted_qty, 4.0)
        self.assertEqual(pickup.total_rejected_qty, 2.0)

    def test_10_draft_credit_note_is_not_a_financial_credit(self):
        """10. A draft note does not touch the receivable."""
        pickup = self._pickup(qty_brakes=3)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        before = self._receivable(self.invoice)

        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit = receipt.move_id

        self.assertEqual(credit.state, 'draft')
        self.assertEqual(receipt.credit_state, 'draft_note')
        self.assertEqual(receipt.total_credited_qty, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_credited, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_credit_pending, 3.0)
        self.assertEqual(self._receivable(self.invoice), before)

    def test_11_posting_the_credit_note_moves_the_receivable(self):
        """11. Posting updates the balance through Odoo's accounting."""
        pickup = self._pickup(qty_brakes=3)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        before = self._customer_balance(self.customer)
        self.assertGreater(before, 0)

        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit = receipt.move_id
        # A draft refund carries its amount as a positive figure; the sign
        # lives in amount_total_signed.
        credit_amount = abs(credit.amount_total)

        credit.with_user(self.accountant).action_post()

        self.assertEqual(credit.state, 'posted')
        self.assertEqual(receipt.credit_state, 'posted')
        self.assertEqual(receipt.total_credited_qty, 3.0)
        self.assertEqual(receipt.line_ids[0].qty_credited, 3.0)
        # The customer's receivable drops by exactly the credited amount.
        self.assertAlmostEqual(self._customer_balance(self.customer),
                               before - credit_amount, places=2)
        # ... and so does what is left outstanding on the invoice.
        self.assertLess(self._receivable(self.invoice), before)
        # The original invoice is not marked paid and is otherwise untouched.
        self.assertEqual(self.invoice.state, 'posted')
        self.assertNotEqual(self.invoice.payment_state, 'paid')

    def test_12_credit_note_references_the_original_invoice(self):
        """12. The credit note is Odoo's own reversal of the invoice."""
        pickup = self._pickup(qty_brakes=2)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        credit = receipt.move_id

        self.assertEqual(credit.move_type, 'out_refund')
        self.assertEqual(credit.reversed_entry_id, self.invoice)
        self.assertEqual(credit.state, 'draft')
        self.assertEqual(credit.partner_id, self.customer)
        self.assertEqual(credit.company_id, self.company)
        self.assertEqual(credit.currency_id, self.invoice.currency_id)
        self.assertEqual(credit.journal_id, self.invoice.journal_id)
        self.assertEqual(credit.goods_pickup_id, pickup)
        self.assertEqual(credit.goods_pickup_receipt_id, receipt)
        line = credit.invoice_line_ids.filtered(
            lambda line: line.product_id == self.brake_pads)
        self.assertEqual(len(line), 1)
        self.assertEqual(line.price_unit, self.brake_pads.list_price)
        self.assertEqual(line.tax_ids,
                         self._invoice_line(self.invoice, self.brake_pads).tax_ids)
        self.assertEqual(line.goods_pickup_receipt_line_id, receipt.line_ids[0])

    def test_13_stock_receipt_is_linked_to_pickup_and_invoice(self):
        """13. The stock receipt carries the whole chain."""
        pickup = self._pickup(qty_brakes=1)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)

        picking = receipt.picking_id
        self.assertEqual(picking.origin, pickup.name)
        self.assertEqual(picking.partner_id, self.customer)
        self.assertEqual(picking.location_dest_id, self.store_location)
        self.assertEqual(picking.location_id.usage, 'customer')
        self.assertIn(pickup.name, picking.note)
        self.assertEqual(picking.goods_pickup_id, pickup)
        self.assertEqual(receipt.line_ids[0].stock_move_id, picking.move_ids[0])

    def test_14_duplicate_validation_is_prevented(self):
        """14. The same receipt cannot be validated twice, nor over-received."""
        pickup = self._pickup(qty_brakes=4)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        with self.assertRaises(UserError):
            receipt.action_validate_receipt()

        # Presenting the same goods again is refused: nothing is outstanding.
        with self.assertRaises(UserError):
            pickup.action_start_receiving()
        with self.assertRaises(UserError):
            self.env['customer.goods.receipt.receive'].create(
                {'pickup_id': pickup.id})

        # A hand-written over-receipt is caught by the constraint too.
        with self.assertRaises(ValidationError):
            receipt.line_ids[0].write({'qty_received': 9})

    def test_15_duplicate_credits_are_prevented(self):
        """15. The same accepted quantity cannot be credited twice."""
        pickup = self._pickup(qty_brakes=4)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        receipt.with_user(self.accountant).action_approve_credit()
        receipt.with_user(self.accountant).action_create_credit_note()
        first = receipt.move_id
        first.with_user(self.accountant).action_post()
        self.assertEqual(receipt.total_credited_qty, 4.0)

        with self.assertRaises(UserError):
            receipt.action_create_credit_note()
        self.assertEqual(len(receipt.move_ids), 1)
        self.assertEqual(receipt.line_ids[0].qty_to_credit, 0.0)

    def test_16_quantities_cannot_exceed_the_eligible_quantity(self):
        """16. Over-collection is refused when collection is confirmed."""
        pickup = self._pickup(qty_brakes=12)
        with self.assertRaises(ValidationError):
            pickup.action_confirm_collection()

        pickup.write({'line_ids': [Command.update(
            pickup.line_ids[0].id, {'qty_collected': 10})]})
        pickup.action_confirm_collection()
        self.assertEqual(pickup.state, 'collected')

        # A second pickup cannot then take the same ten.
        other = self._pickup(qty_brakes=3)
        self.assertEqual(self._line(other, self.brake_pads).eligible_qty, 0.0)
        with self.assertRaises(ValidationError):
            other.action_confirm_collection()

    def test_17_multiple_pickups_against_one_invoice(self):
        """17. Two trips against one invoice both work and both are bounded."""
        first = self._pickup(qty_brakes=6)
        first.action_confirm_collection()
        second = self._pickup(qty_brakes=4)
        second.action_confirm_collection()

        self.assertEqual(self._line(second, self.brake_pads).eligible_qty, 4.0)
        third = self._pickup(qty_brakes=1)
        with self.assertRaises(ValidationError):
            third.action_confirm_collection()

        receipt_first = self._receive(first)
        receipt_second = self._receive(second)
        self.assertEqual(receipt_first.total_accepted_qty, 6)
        self.assertEqual(receipt_second.total_accepted_qty, 4)
        self.assertEqual(receipt_first.invoice_number, self.invoice.name)
        self.assertEqual(receipt_second.invoice_number, self.invoice.name)

    def test_18_multiple_partial_receipts_on_one_pickup(self):
        """18. Goods can be presented twice; each receipt is bounded."""
        pickup = self._pickup(qty_brakes=10)
        pickup.action_confirm_collection()
        before = self._on_hand(self.brake_pads)

        self._receive_part(pickup, self.brake_pads, received=4, accepted=4)
        self.assertEqual(pickup.state, 'partially_received')

        receipt_two = self._receive(pickup)
        self.assertEqual(receipt_two.total_accepted_qty, 6)
        self.assertEqual(self._on_hand(self.brake_pads), before + 10)
        self.assertEqual(pickup.state, 'received')
        self.assertEqual(pickup.total_collected_qty, 10)
        self.assertEqual(pickup.receipt_count, 2)

    def test_19_partial_credits_across_receipts_stay_within_the_invoice(self):
        """19. Several credit notes never exceed the invoiced quantity."""
        pickup = self._pickup(qty_brakes=10)
        pickup.action_confirm_collection()
        before = self._receivable(self.invoice)

        receipt_one = self._receive_part(
            pickup, self.brake_pads, received=4, accepted=4)
        receipt_two = self._receive(pickup)

        for receipt in (receipt_one, receipt_two):
            receipt.with_user(self.accountant).action_approve_credit()
            receipt.with_user(self.accountant).action_create_credit_note()
        credit_one, credit_two = receipt_one.move_id, receipt_two.move_id
        credit_one.with_user(self.accountant).action_post()
        credit_two.with_user(self.accountant).action_post()

        invoiced = self._invoice_line(self.invoice, self.brake_pads)[0]
        credited = sum(
            line.quantity
            for move in (credit_one, credit_two)
            for line in move.invoice_line_ids
            if line.product_id == self.brake_pads)
        self.assertEqual(credited, 10.0)
        self.assertLessEqual(credited, invoiced.quantity)
        self.assertEqual(receipt_one.total_credited_qty, 4.0)
        self.assertEqual(receipt_two.total_credited_qty, 6.0)
        self.assertEqual(pickup.total_credited_qty, 10.0)
        self.assertLess(self._receivable(self.invoice), before)
        self.assertEqual(pickup.total_credit_amount,
                         -(credit_one.amount_total + credit_two.amount_total))

        # Ten of ten collected and credited: nothing left to collect.
        self.assertEqual(self._line(pickup, self.brake_pads).credited_qty, 10.0)
        third = self._pickup(qty_brakes=1)
        self.assertEqual(self._line(third, self.brake_pads).eligible_qty, 0.0)
        with self.assertRaises(ValidationError):
            third.action_confirm_collection()

    def test_20_uom_conversion_is_respected(self):
        """20. Quantities are converted, never added up blindly."""
        dozen = self.env.ref('uom.product_uom_dozen', raise_if_not_found=False)
        if not dozen:
            dozen = self.env['uom.uom'].create({
                'name': 'Dozens',
                'relative_uom_id': self.air_filter.uom_id.id,
                'relative_factor': 12.0,
                'factor': 12.0,
            })
        invoice = self._post_invoice([(self.air_filter, 24)])
        pickup = self.env['customer.goods.pickup'].create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'sales_rep_id': self.rep.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.store_location.id,
        })
        invoice_line = self._invoice_line(invoice, self.air_filter)[0]
        pickup.write({'line_ids': [Command.create({
            'invoice_line_id': invoice_line.id,
            'product_id': self.air_filter.id,
            'product_uom_id': dozen.id,
            'qty_collected': 2.0,
        })]})
        line = pickup.line_ids[0]
        # Twenty-four units invoiced is two dozen, so two dozen is collectable.
        self.assertEqual(line.invoiced_qty, 2.0)
        self.assertAlmostEqual(line.eligible_qty, 2.0, places=2)
        # Three dozen is more than was ever invoiced and is refused.
        line.qty_collected = 3.0
        with self.assertRaises(ValidationError):
            pickup.action_confirm_collection()
        line.qty_collected = 2.0
        pickup.action_confirm_collection()
        self.assertEqual(pickup.state, 'collected')

        # Two dozen of units is twenty-four units on hand.
        before = self._on_hand(self.air_filter)
        self._receive(pickup)
        self.assertAlmostEqual(self._on_hand(self.air_filter) - before, 24.0,
                               places=2)

    def test_21_cancellation_and_reversal_keep_the_history(self):
        """21. Nothing confirmed is deleted; stock is reversed, not erased."""
        pickup = self._pickup(qty_brakes=4)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        self.assertEqual(self._on_hand(self.brake_pads), 4)

        # A confirmed pickup and a validated receipt cannot be deleted.
        with self.assertRaises(UserError):
            pickup.unlink()
        with self.assertRaises(UserError):
            receipt.unlink()

        # Cancelling first requires the stock receipt to be reversed.
        with self.assertRaises(UserError):
            self.env['customer.goods.pickup.cancel'].create({
                'pickup_id': pickup.id,
                'cancel_reason': 'Customer changed their mind',
                'goods_disposition': 'returned_to_customer',
            })
        receipt.action_reverse_stock_receipt()
        self.assertTrue(receipt.is_stock_reversed)
        self.assertEqual(receipt.reverse_picking_id.state, 'done')
        self.assertEqual(self._on_hand(self.brake_pads), 0)

        wizard = self.env['customer.goods.pickup.cancel'].create({
            'pickup_id': pickup.id,
            'cancel_reason': 'Customer changed their mind',
            'goods_disposition': 'returned_to_customer',
        })
        wizard.action_cancel_pickup()
        self.assertEqual(pickup.state, 'cancel')
        self.assertIn('Customer changed their mind', pickup.cancel_reason)
        self.assertTrue(pickup.cancel_date)
        # The receipts are still on the record.
        self.assertTrue(pickup.receipt_ids)

    def test_22_quarantine_goods_never_become_sellable_stock(self):
        """22/23. Refused goods are real stock — in a location that cannot sell them."""
        pickup = self._pickup(qty_brakes=4)
        pickup.action_confirm_collection()
        receipt = self._draft_receipt(pickup)
        receipt.line_ids[0].write({
            'qty_accepted': 0,
            'rejection_reason': 'Does not qualify for return',
        })
        receipt.write({
            'rejected_disposition': 'held',
            'rejection_reason': 'Does not qualify for return',
        })
        before = self._on_hand(self.brake_pads)
        receipt.action_validate_receipt()

        quarantine = receipt.rejected_location_id
        self.assertTrue(quarantine)
        self.assertEqual(quarantine.usage, 'internal')
        self.assertFalse(quarantine.replenish_location)
        # The whole design hinges on this: outside every warehouse, so the
        # quantity is neither visible to availability nor reservable.
        self.assertFalse(
            quarantine.warehouse_id,
            'refused goods sit inside warehouse %s — they could be sold from it'
            % quarantine.warehouse_id.display_name)
        self.assertTrue(quarantine.cognify_goods_return)
        self.assertTrue(receipt.quarantine_picking_id)
        self.assertEqual(receipt.quarantine_picking_id.state, 'done')
        self.assertTrue(receipt.quarantine_ids)
        self.assertEqual(receipt.quarantine_ids.qty_pending, 4)

        # Physical custody is real: a quant, a move and a value.
        self.assertEqual(
            self.env['stock.quant'].search_count([
                ('product_id', '=', self.brake_pads.id),
                ('location_id', '=', quarantine.id),
            ]), 1)
        self.assertAlmostEqual(self._on_hand(self.brake_pads), before)
        self.assertEqual(
            self.env['stock.quant'].search([
                ('product_id', '=', self.brake_pads.id),
                ('location_id', '=', self.store_location.id),
            ]).mapped('quantity'), [])
        # ... and the shop floor quantity on hand is untouched.
        self.assertEqual(
            self.brake_pads.with_context(
                warehouse_id=self.warehouse.id).qty_available, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_in_quarantine, 4)
        self.assertEqual(receipt.line_ids[0].qty_reinspected_accepted, 0.0)
        self.assertEqual(receipt.line_ids[0].qty_to_credit, 0.0)
        self.assertEqual(receipt.credit_state, 'not_eligible')
        self.assertEqual(receipt.rejected_disposition, 'held')

    def test_23_credit_needs_approval_and_can_be_declined(self):
        """23. The accountant, not the store, decides about the money."""
        pickup = self._pickup(qty_brakes=3)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        self.assertEqual(receipt.credit_state, 'awaiting_approval')

        with self.assertRaises(UserError):
            receipt.action_create_credit_note()

        with self.assertRaises(UserError):
            receipt.with_user(self.accountant).action_decline_credit()
        receipt.with_user(self.accountant).write(
            {'credit_approval_notes': 'Return window has closed.'})
        receipt.with_user(self.accountant).action_decline_credit()
        self.assertEqual(receipt.credit_state, 'declined')
        # Declining the credit does not remove the goods from stock.
        self.assertEqual(self._on_hand(self.brake_pads), 3)
        self.assertEqual(receipt.total_credited_qty, 0.0)

    def test_24_foreign_credit_note_shrinks_what_can_be_collected(self):
        """24. A credit note raised outside the module still counts."""
        external = self.invoice._reverse_moves([{}])
        external.write({'invoice_line_ids': [Command.update(
            external.invoice_line_ids.filtered(
                lambda line: line.product_id == self.brake_pads)[0].id,
            {'quantity': 4})]})
        external.action_post()

        pickup = self._pickup(qty_brakes=1)
        line = self._line(pickup, self.brake_pads)
        self.assertEqual(line.credited_qty, 4.0)
        self.assertEqual(line.eligible_qty, 6.0)
        pickup.action_confirm_collection()
        self.assertEqual(pickup.state, 'collected')

    def test_25_tracked_goods_are_received_with_a_lot(self):
        """25. Serial-tracked products keep their traceability."""
        tracked = self._make_product('Tracked widget', 'TW-1', 12.0,
                                     tracking='serial')
        invoice = self._post_invoice([(tracked, 2)])
        pickup = self.env['customer.goods.pickup'].create({
            'partner_id': invoice.partner_id.id,
            'invoice_id': invoice.id,
            'sales_rep_id': self.rep.id,
            'warehouse_id': self.warehouse.id,
            'dest_location_id': self.store_location.id,
            'line_ids': [Command.create({
                'invoice_line_id': invoice.invoice_line_ids[0].id,
                'product_id': tracked.id,
                'product_uom_id': invoice.invoice_line_ids[0].product_uom_id.id,
                'qty_collected': 2,
            })],
        })
        pickup.action_confirm_collection()
        before = self._on_hand(tracked)

        receipt = self._receive(pickup)

        self.assertEqual(receipt.total_accepted_qty, 2)
        self.assertEqual(self._on_hand(tracked), before + 2)
        operations = self.env['stock.move.line'].search(
            [('move_id', 'in', receipt.picking_id.move_ids.ids)])
        lots = operations.mapped('lot_id')
        # Serial goods get one number per unit.
        self.assertEqual(len(lots), 2)
        self.assertEqual(lots.product_id, tracked)
        self.assertEqual(sorted(operations.mapped('quantity')), [1.0, 1.0])
        self.assertEqual(receipt.line_ids[0].lot_id, lots[0])

    def test_26_receipt_cannot_be_delivered_twice_or_re_edited(self):
        """26. A validated receipt is frozen, and its lines cannot be edited."""
        pickup = self._pickup(qty_brakes=3)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)

        with self.assertRaises(UserError):
            receipt.line_ids[0].write({'qty_accepted': 1})
        with self.assertRaises(UserError):
            receipt.write({'dest_location_id': self.warehouse.view_location_id.id})
        # A manager can, because that is the authorised correction path.
        receipt.with_user(self.env.user).write({
            'inspection_notes': 'Checked against the packing slip'})
        self.assertEqual(
            receipt.inspection_notes, 'Checked against the packing slip')
        self.assertEqual(receipt.state, 'done')
        self.assertEqual(pickup.state, 'received')

    def test_27_sales_rep_is_told_what_the_store_decided(self):
        """27. The representative is notified of the receiving outcome."""
        pickup = self._pickup(qty_brakes=2)
        pickup.action_confirm_collection()
        receipt = self._receive_part(
            pickup, self.brake_pads, received=2, accepted=1, reason='Seized')
        messages = pickup.message_ids
        self.assertTrue(
            any('rejected' in (message.body or '').lower() for message in messages),
            'the sales representative is told what the store turned down')
        self.assertTrue(messages)
        self.assertEqual(receipt.total_accepted_qty, 1.0)

    def test_28_pickup_and_receipt_report_the_invoice_number(self):
        """28. The invoice number is visible without opening another record."""
        pickup = self._pickup(qty_brakes=1)
        self.assertEqual(pickup.invoice_number, self.invoice.name)
        pickup.action_confirm_collection()
        receipt = self._receive(pickup)
        self.assertEqual(receipt.invoice_number, self.invoice.name)
        self.assertEqual(receipt.pickup_id.invoice_number, self.invoice.name)
        self.assertEqual(receipt.line_ids[0].invoice_number, self.invoice.name)