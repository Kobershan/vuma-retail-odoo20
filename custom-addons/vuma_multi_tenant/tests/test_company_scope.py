# -*- coding: utf-8 -*-
"""Regression tests for the company switcher as a capture boundary.

Vuma supports four scopes:

* All Companies
* Trade Rite + Siyaya DC (both)
* Trade Rite only
* Siyaya DC only

A document may only be captured for a company that is in the active scope.
Cross-company work (mixed baskets, cash collections and crediting) needs both
trading companies active. These tests pin that behaviour down at the model
level, because the views hide buttons but the ORM is the real boundary.
"""
import unittest

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase

MODULE = "vuma_multi_tenant"


@tagged("post_install", "-at_install", MODULE)
class TestCompanyScope(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Company = cls.env["res.company"]
        cls.trade = Company.search([("name", "=", "Trade Rite")], limit=1)
        cls.siyaya = Company.search([("name", "=", "Siyaya DC")], limit=1)
        if not (cls.trade and cls.siyaya):
            raise unittest.SkipTest(
                "Trade Rite and Siyaya DC must exist to test the Vuma scope"
            )

        # The mixed-basket flows are only offered to this group. The test
        # database already grants it to admin; grant it explicitly so a fresh
        # database behaves the same way.
        mixed_group = cls.env.ref(
            "vuma_multi_tenant.group_vuma_mixed_basket_user",
            raise_if_not_found=False,
        )
        if mixed_group and mixed_group not in cls.env.user.group_ids:
            cls.env.user.group_ids |= mixed_group

        # The test database may have the testing bypass granted to admin. The
        # whole point of these tests is to prove the guard still works, so drop
        # it for the duration of the test transaction.
        testing_group = cls.env.ref(
            "vuma_multi_tenant.group_vuma_testing_access",
            raise_if_not_found=False,
        )
        if testing_group and testing_group in cls.env.user.group_ids:
            cls.env.user.group_ids -= testing_group

        cls.partner = cls.env["res.partner"].create({
            "name": "Vuma Company Scope Test Customer",
            "customer_rank": 1,
        })
        cls.product_trade = cls._product_for(cls.trade)
        cls.product_siyaya = cls._product_for(cls.siyaya)
        cls.shared_product = cls._shared_product()

    @classmethod
    def _product_for(cls, company):
        """A saleable product owned by ``company`` (reuse seeded data)."""
        product = cls.env["product.product"].search([
            ("company_id", "=", company.id),
            ("sale_ok", "=", True),
            ("type", "=", "consu"),
        ], limit=1)
        if not product:
            product = cls.env["product.product"].create({
                "name": "Vuma Scope Test Product (%s)" % company.name,
                "type": "consu",
                "sale_ok": True,
                "company_id": company.id,
            })
        return product

    @classmethod
    def _shared_product(cls):
        """A saleable product held in both companies' warehouses.

        Shared products are the interesting case for the source-company rule:
        Odoo's own product-company constraint cannot catch them, so only the
        Vuma scope check prevents a Trade Rite order from drawing Siyaya DC
        stock.
        """
        quant = cls.env["stock.quant"].sudo().search([
            ("product_id.company_id", "=", False),
            ("product_id.sale_ok", "=", True),
            ("product_id.type", "=", "consu"),
            ("location_id.company_id", "=", cls.siyaya.id),
            ("quantity", ">", 0),
        ], limit=1)
        if quant:
            return quant.product_id

        product = cls.env["product.product"].create({
            "name": "Vuma Scope Shared Test Product",
            "type": "consu",
            "sale_ok": True,
            "company_id": False,
        })
        for company in (cls.trade, cls.siyaya):
            warehouse = cls.env["stock.warehouse"].sudo().search([
                ("company_id", "=", company.id),
            ], limit=1)
            if not warehouse:
                continue
            cls.env["stock.quant"].sudo().create({
                "product_id": product.id,
                "location_id": warehouse.lot_stock_id.id,
                "quantity": 10.0,
            })
        return product

    def _scope(self, *companies):
        """An environment whose active company scope is exactly ``companies``."""
        return self.env(
            context=dict(
                self.env.context,
                allowed_company_ids=[company.id for company in companies],
            )
        )

    def _assert_refused(self, callback, *expected_fragments):
        with self.assertRaises(UserError) as caught:
            callback()
        message = str(caught.exception)
        for fragment in expected_fragments:
            self.assertIn(fragment, message)
        return message

    def _income_account(self, company):
        account = self.env["account.account"].sudo().search([
            ("company_ids", "in", company.id),
            ("account_type", "in", ("income", "income_other")),
            ("active", "=", True),
        ], order="id", limit=1)
        self.assertTrue(
            account, "no income account configured for %s" % company.name
        )
        return account

    def _make_invoice(self, company, env=None):
        env = env or self._scope(company)
        return env["account.move"].create({
            "move_type": "out_invoice",
            "partner_id": self.partner.id,
            "company_id": company.id,
            "invoice_date": fields.Date.today(),
            "invoice_line_ids": [(0, 0, {
                "name": "Vuma company scope test line",
                "quantity": 1.0,
                "price_unit": 100.0,
                "account_id": self._income_account(company).id,
            })],
        })

    def _journal(self, company, env=None):
        env = env or self._scope(company)
        journal = env["account.journal"].sudo().search([
            ("company_id", "=", company.id),
            ("type", "in", ("cash", "bank")),
        ], order="type desc, id", limit=1)
        self.assertTrue(journal, "no cash/bank journal for %s" % company.name)
        return journal

    def _make_payment(self, company, env=None):
        env = env or self._scope(company)
        journal = self._journal(company, env)
        return env["account.payment"].create({
            "payment_type": "inbound",
            "partner_type": "customer",
            "partner_id": self.partner.id,
            "amount": 25.0,
            "journal_id": journal.id,
            "company_id": company.id,
        })

    def _make_order(self, company, env=None, mixed=False, product=None,
                    source_company=None):
        env = env or self._scope(company)
        product = product or (
            self.product_trade if company == self.trade else self.product_siyaya
        )
        source = source_company or product.company_id or company
        return env["sale.order"].create({
            "partner_id": self.partner.id,
            "company_id": company.id,
            "vuma_is_mixed_basket": mixed,
            "order_line": [(0, 0, {
                "product_id": product.id,
                "product_uom_qty": 1.0,
                "vuma_source_company_id": source.id,
            })],
        })

    # ------------------------------------------------------------------
    # Scope helpers
    # ------------------------------------------------------------------
    def test_trading_companies_are_always_trade_rite_then_siyaya(self):
        trading = self.env.user._vuma_trading_companies()
        self.assertEqual(trading.ids, [self.trade.id, self.siyaya.id])

    def test_both_trading_companies_are_detected_from_the_active_scope(self):
        self.assertFalse(
            self._scope(self.trade).user._vuma_has_both_trading_companies()
        )
        self.assertFalse(
            self._scope(self.siyaya).user._vuma_has_both_trading_companies()
        )
        self.assertTrue(
            self._scope(
                self.trade, self.siyaya
            ).user._vuma_has_both_trading_companies()
        )
        # "All Companies" also contains both trading companies.
        self.assertTrue(
            self._scope(
                self.env.ref("base.main_company"), self.trade, self.siyaya
            ).user._vuma_has_both_trading_companies()
        )

    def test_require_company_refuses_and_names_the_company_to_switch_to(self):
        trade_only = self._scope(self.trade)
        # The active company itself is always allowed.
        trade_only.user._vuma_require_company(self.trade, "capture this document")
        self._assert_refused(
            lambda: trade_only.user._vuma_require_company(
                self.siyaya, "capture this document"
            ),
            "Switch to Siyaya DC",
            "capture this document",
        )
        # Both active means either company can be captured.
        both = self._scope(self.trade, self.siyaya)
        both.user._vuma_require_company(self.trade, "capture this document")
        both.user._vuma_require_company(self.siyaya, "capture this document")

    def test_cross_company_helpers_need_both_trading_companies(self):
        both = self._scope(self.trade, self.siyaya)
        both.user._vuma_require_both_trading_companies("split this basket")
        both.user._vuma_require_companies(
            self.trade | self.siyaya, "post this collection"
        )

        trade_only = self._scope(self.trade)
        self._assert_refused(
            lambda: trade_only.user._vuma_require_both_trading_companies(
                "split this basket"
            ),
            "Trade Rite + Siyaya DC",
            "both companies",
        )
        self._assert_refused(
            lambda: trade_only.user._vuma_require_companies(
                self.trade | self.siyaya, "post this collection"
            ),
            "Trade Rite + Siyaya DC",
            "both companies",
        )

    # ------------------------------------------------------------------
    # Mixed basket gating
    # ------------------------------------------------------------------
    def test_mixed_basket_is_only_offered_with_both_companies_active(self):
        order = self.env["sale.order"].new({
            "partner_id": self.partner.id,
            "company_id": self.trade.id,
        })
        self.assertFalse(
            order.with_env(self._scope(self.trade)).vuma_mixed_basket_available
        )
        self.assertFalse(
            order.with_env(self._scope(self.siyaya)).vuma_mixed_basket_available
        )
        self.assertTrue(
            order.with_env(
                self._scope(self.trade, self.siyaya)
            ).vuma_mixed_basket_available
        )

    def test_mixed_basket_default_follows_the_active_scope(self):
        single_defaults = self._scope(self.trade)["sale.order"].default_get(
            ["vuma_is_mixed_basket"]
        )
        self.assertFalse(single_defaults.get("vuma_is_mixed_basket"))

        both_defaults = self._scope(
            self.trade, self.siyaya
        )["sale.order"].default_get(["vuma_is_mixed_basket"])
        self.assertTrue(both_defaults.get("vuma_is_mixed_basket"))

    def test_new_mixed_basket_button_is_gated_on_the_scope(self):
        order = self.env["sale.order"].new({})
        self._assert_refused(
            lambda: order.with_env(
                self._scope(self.trade)
            ).action_new_mixed_basket(),
            "Trade Rite + Siyaya DC",
        )
        action = order.with_env(
            self._scope(self.trade, self.siyaya)
        ).action_new_mixed_basket()
        self.assertTrue(action["context"].get("default_vuma_is_mixed_basket"))

    def test_mixed_quote_cannot_be_confirmed_on_a_single_company_scope(self):
        order = self._make_order(
            self.trade,
            env=self._scope(self.trade, self.siyaya),
            mixed=True,
            product=self.product_trade,
        )
        self._assert_refused(
            lambda: order.with_env(
                self._scope(self.siyaya)
            ).action_confirm(),
            "Trade Rite + Siyaya DC",
            "both companies",
        )

    # ------------------------------------------------------------------
    # Sale order lines
    # ------------------------------------------------------------------
    def test_sale_line_cannot_save_a_source_outside_the_scope(self):
        line = self._scope(self.trade, self.siyaya)["sale.order.line"].create({
            "order_id": self._make_order(
                self.trade, env=self._scope(self.trade, self.siyaya)
            ).id,
            "product_id": self.product_siyaya.id,
            "product_uom_qty": 1.0,
            "vuma_source_company_id": self.siyaya.id,
        })
        self._assert_refused(
            lambda: line.with_env(
                self._scope(self.trade)
            ).write({"vuma_source_company_id": self.siyaya.id}),
            "Switch to Siyaya DC",
            "stock source",
        )

    def test_normal_order_confirm_rejects_another_company_stock(self):
        both = self._scope(self.trade, self.siyaya)
        order = self._make_order(
            self.trade,
            env=both,
            product=self.shared_product,
            source_company=self.siyaya,
        )
        self._assert_refused(
            lambda: order.with_env(self._scope(self.trade)).action_confirm(),
            "another company's stock",
            self.siyaya.display_name,
        )

    # ------------------------------------------------------------------
    # Invoices
    # ------------------------------------------------------------------
    def test_invoice_capture_requires_the_invoice_company(self):
        self._assert_refused(
            lambda: self._make_invoice(self.siyaya, self._scope(self.trade)),
            "Switch to Siyaya DC",
            "capture this invoice",
        )
        invoice = self._make_invoice(self.siyaya, self._scope(self.siyaya))
        self.assertEqual(invoice.company_id, self.siyaya)

    def test_invoice_capture_checks_the_company_implied_by_the_journal(self):
        """A journal-only create must not smuggle in another company."""
        self._assert_refused(
            lambda: self._scope(self.trade)["account.move"].create({
                "move_type": "out_invoice",
                "partner_id": self.partner.id,
                # No explicit company_id: the journal decides the company.
                "journal_id": self._journal(self.siyaya).id,
            }),
            "Switch to Siyaya DC",
            "capture this invoice",
        )

    def test_invoice_posting_requires_every_invoice_company(self):
        invoice = self._make_invoice(self.siyaya, self._scope(self.siyaya))
        self._assert_refused(
            lambda: invoice.with_env(
                self._scope(self.trade)
            ).action_post(),
            "Switch to Siyaya DC",
            "post this invoice",
        )
        # The same invoice posts while its company is active.
        invoice.action_post()
        self.assertEqual(invoice.state, "posted")

    def test_moving_an_invoice_to_another_company_requires_that_company(self):
        invoice = self._make_invoice(self.siyaya, self._scope(self.siyaya))
        self._assert_refused(
            lambda: invoice.with_env(
                self._scope(self.trade)
            ).write({"company_id": self.siyaya.id}),
            "Switch to Siyaya DC",
            "move this invoice",
        )

    # ------------------------------------------------------------------
    # Payments
    # ------------------------------------------------------------------
    def test_payment_capture_requires_the_payment_company(self):
        self._assert_refused(
            lambda: self._make_payment(self.siyaya, self._scope(self.trade)),
            "Switch to Siyaya DC",
            "capture this payment",
        )
        payment = self._make_payment(self.siyaya, self._scope(self.siyaya))
        self.assertEqual(payment.company_id, self.siyaya)

    def test_payment_capture_checks_the_company_implied_by_the_journal(self):
        """A payment created with only a journal still carries its company."""
        self._assert_refused(
            lambda: self._scope(self.trade)["account.payment"].create({
                "payment_type": "inbound",
                "partner_type": "customer",
                "partner_id": self.partner.id,
                "amount": 25.0,
                # No explicit company_id: the journal decides the company.
                "journal_id": self._journal(self.siyaya).id,
            }),
            "Switch to Siyaya DC",
            "capture this payment",
        )

    def test_payment_posting_requires_every_payment_company(self):
        payment = self._make_payment(self.siyaya, self._scope(self.siyaya))
        self._assert_refused(
            lambda: payment.with_env(
                self._scope(self.trade)
            ).action_post(),
            "Switch to Siyaya DC",
            "post this payment",
        )
        payment.action_post()
        self.assertEqual(payment.move_id.state, "posted")

    # ------------------------------------------------------------------
    # Cross-company cash collection
    # ------------------------------------------------------------------
    def test_cash_collection_row_requires_its_company_in_scope(self):
        session = self._scope(self.trade)["vuma.payment.session"].create({
            "partner_id": self.partner.id,
        })
        self._assert_refused(
            lambda: self._scope(self.trade)["vuma.payment.session.line"].create({
                "session_id": session.id,
                "company_id": self.siyaya.id,
                "amount": 10.0,
            }),
            "Switch to Siyaya DC",
            "capture a payment for Siyaya DC",
        )

    def test_cross_company_collection_posts_only_with_both_active(self):
        both = self._scope(self.trade, self.siyaya)
        session = both["vuma.payment.session"].create({
            "partner_id": self.partner.id,
            "line_ids": [
                (0, 0, {"company_id": self.trade.id, "amount": 10.0}),
                (0, 0, {"company_id": self.siyaya.id, "amount": 15.0}),
            ],
        })
        self._assert_refused(
            lambda: session.with_env(self._scope(self.trade)).action_post(),
            "Trade Rite + Siyaya DC",
            "both companies",
        )
        session.action_post()
        self.assertEqual(session.state, "posted")
        self.assertEqual(
            set(session.payment_ids.mapped("company_id").ids),
            {self.trade.id, self.siyaya.id},
        )
        self.assertTrue(
            all(p.move_id.state == "posted" for p in session.payment_ids)
        )

    def test_cross_company_credit_button_needs_both_companies(self):
        session = self._scope(self.trade, self.siyaya)["vuma.payment.session"].create({
            "partner_id": self.partner.id,
        })
        self._assert_refused(
            lambda: session.with_env(
                self._scope(self.trade)
            ).action_open_credit_wizard(),
            "Trade Rite + Siyaya DC",
            "both companies",
        )
        action = session.action_open_credit_wizard()
        self.assertEqual(action["res_model"], "vuma.partner.credit.wizard")

    # ------------------------------------------------------------------
    # Crediting wizard
    # ------------------------------------------------------------------
    def test_credit_wizard_lists_only_invoices_in_the_active_scope(self):
        trade_invoice = self._make_invoice(self.trade, self._scope(self.trade))
        trade_invoice.action_post()
        siyaya_invoice = self._make_invoice(
            self.siyaya, self._scope(self.siyaya)
        )
        siyaya_invoice.action_post()

        trade_wizard = self._scope(self.trade)[
            "vuma.partner.credit.wizard"
        ].create({
            "partner_id": self.partner.id,
            "reason": "scope test",
        })
        trade_wizard._onchange_partner_id()
        self.assertIn(trade_invoice, trade_wizard.invoice_ids)
        self.assertNotIn(siyaya_invoice, trade_wizard.invoice_ids)

        both_wizard = self._scope(self.trade, self.siyaya)[
            "vuma.partner.credit.wizard"
        ].create({
            "partner_id": self.partner.id,
            "reason": "scope test",
        })
        both_wizard._onchange_partner_id()
        self.assertIn(trade_invoice, both_wizard.invoice_ids)
        self.assertIn(siyaya_invoice, both_wizard.invoice_ids)

    def test_credit_wizard_rechecks_scope_before_creating_notes(self):
        trade_invoice = self._make_invoice(self.trade, self._scope(self.trade))
        trade_invoice.action_post()
        siyaya_invoice = self._make_invoice(
            self.siyaya, self._scope(self.siyaya)
        )
        siyaya_invoice.action_post()

        wizard = self._scope(self.trade, self.siyaya)[
            "vuma.partner.credit.wizard"
        ].create({
            "partner_id": self.partner.id,
            "reason": "scope test",
            "invoice_ids": [(6, 0, (trade_invoice | siyaya_invoice).ids)],
        })
        self._assert_refused(
            lambda: wizard.with_env(
                self._scope(self.trade)
            ).action_create_credit_notes(),
            "Trade Rite + Siyaya DC",
            "both companies",
            "credit these invoices",
        )

        wizard.action_create_credit_notes()
        self.assertEqual(
            wizard.created_move_ids.mapped("company_id").ids,
            [self.trade.id, self.siyaya.id],
        )
