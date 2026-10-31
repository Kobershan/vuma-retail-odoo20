"""Guards for the unfiltered cross-company actions used by the admin sidebar.

The regular Odoo actions hide records in ways that are right for a single-user
desktop but wrong for this shared back office:

* Quotations defaults to ``search_default_my_quotation`` (only my records).
* Sales Orders defaults to ``search_default_sales`` (confirmed only).
* Invoices defaults to ``search_default_out_invoice`` / ``out_receipt``.
* Journal Entries defaults to ``search_default_posted``.

These tests keep the Vuma replacements unfiltered so a record created by
another user cannot silently disappear from the menu again.
"""
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval

MODULE = "vuma_multi_tenant"

MENU_ACTIONS = {
    "menu_vuma_link_quote": "action_vuma_all_quotations",
    "menu_vuma_link_orders": "action_vuma_all_sales_orders",
    "menu_vuma_link_invoices": "action_vuma_all_customer_invoices",
    "menu_vuma_link_payments": "action_vuma_all_payments",
    "menu_vuma_all_vendor_bills": "action_vuma_all_vendor_bills",
    "menu_vuma_all_journal_entries": "action_vuma_all_journal_entries",
    "menu_vuma_all_journal_items": "action_vuma_all_journal_items",
}

ACTIONS = tuple(MENU_ACTIONS.values())


@tagged("post_install", "-at_install")
class TestVisibilityActions(TransactionCase):
    def test_menus_point_to_the_unfiltered_actions(self):
        for menu_xmlid, action_xmlid in MENU_ACTIONS.items():
            menu = self.env.ref(f"{MODULE}.{menu_xmlid}")
            action = self.env.ref(f"{MODULE}.{action_xmlid}")
            self.assertEqual(
                menu.action,
                action,
                f"{menu_xmlid} still opens {menu.action.name!r} instead of {action.name!r}",
            )

    def test_actions_have_no_default_search_filters(self):
        for action_xmlid in ACTIONS:
            action = self.env.ref(f"{MODULE}.{action_xmlid}")
            context = safe_eval(action.context or "{}")
            hidden = sorted(key for key in context if key.startswith("search_default"))
            self.assertFalse(
                hidden,
                f"{action_xmlid} still hides records with {hidden}",
            )

    def test_actions_have_a_route_and_a_default_view(self):
        """A menu action without a path or default view reloads the dashboard."""
        for action_xmlid in ACTIONS:
            action = self.env.ref(f"{MODULE}.{action_xmlid}")
            self.assertTrue(
                action.path,
                f"{action_xmlid} has no path, so the web client cannot route to it",
            )
            self.assertTrue(
                action.view_id,
                f"{action_xmlid} has no default view, so the client falls back",
            )

    def test_user_rule_lets_staff_read_each_other(self):
        """Odoo's res.users multi-company rule must keep its shipped domain.

        It had been narrowed to [('company_id', 'in', company_ids)] in the
        database: with the company selector on one trading company, every
        user whose home company is the other one (the administrator's own
        record included, plus the salesperson column on quotations, sale
        orders and invoices) became unreadable and the web client raised
        "doesn't have 'read' access to: User (res.users)".
        """
        rule = self.env.ref("base.res_users_rule")
        self.assertIn(
            "share",
            rule.domain or "",
            "res.users rule lost the ('share', '=', False) branch; internal "
            "users must stay readable across both trading companies",
        )

        # And prove it end to end: admin (home company Trade Rite) reading
        # their own user record while sitting in the other company only.
        admin = self.env.ref("base.user_admin")
        other_company = self.env["res.company"].search(
            [("id", "!=", admin.company_id.id)], limit=1
        )
        admin_env = self.env(
            user=admin, context={"allowed_company_ids": other_company.ids}
        )
        admin_env["res.users"].browse(admin.id).check_access("read")

    def test_action_domains_cover_shared_records(self):
        expected = {
            "action_vuma_all_quotations": [("state", "in", ["draft", "sent"])],
            "action_vuma_all_sales_orders": [("state", "!=", "cancel")],
            "action_vuma_all_customer_invoices": [
                ("move_type", "in", ["out_invoice", "out_refund", "out_receipt"])
            ],
            "action_vuma_all_payments": [],
            "action_vuma_all_vendor_bills": [
                ("move_type", "in", ["in_invoice", "in_refund", "in_receipt"])
            ],
            "action_vuma_all_journal_entries": [("move_type", "=", "entry")],
            "action_vuma_all_journal_items": [
                (
                    "display_type",
                    "not in",
                    ("line_section", "line_subsection", "line_note"),
                ),
                ("parent_state", "!=", "cancel"),
            ],
        }
        for action_xmlid, domain in expected.items():
            action = self.env.ref(f"{MODULE}.{action_xmlid}")
            self.assertEqual(
                safe_eval(action.domain or "[]"),
                domain,
                f"{action_xmlid} has an unexpected domain",
            )
