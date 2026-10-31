# -*- coding: utf-8 -*-
"""Tests for the per-user Vuma function access table.

The feature has two promises to keep: every internal user starts with every
function granted, and revoking one actually changes what the person can do.
Both are checked here against the real approve, price, credit-limit and delete
paths rather than against the table alone.
"""
from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged

APPROVE = 'action.approve'
PRICE = 'action.price_edit'
CREDIT = 'action.credit_limit'
DELETE = 'action.delete'
REPORTS = 'section.reports'


@tagged('post_install', '-at_install')
class VumaAccessControlTest(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ops_group = cls.env.ref('vuma_retail_ops.group_vuma_ops_manager')
        cls.sales_group = cls.env.ref('sales_team.group_sale_salesman')
        cls.invoice_group = cls.env.ref('account.group_account_invoice')
        cls.user = cls.env['res.users'].create({
            'name': 'Access Tester',
            'login': 'vuma_access_test',
            'email': 'vuma_access_test@example.com',
            'group_ids': [Command.set([
                cls.env.ref('base.group_user').id,
                cls.ops_group.id,
                cls.sales_group.id,
                cls.invoice_group.id,
                # Needed so the guards, rather than Odoo's own ACLs, are what
                # the price and credit-limit tests hit.
                cls.env.ref('product.group_product_manager').id,
                cls.env.ref('base.group_partner_manager').id,
            ])],
        })

    # ------------------------------------------------------------------ helpers

    def _function(self, code):
        function = self.env['vuma.function'].search(
            [('code', '=', code)], limit=1)
        self.assertTrue(function, 'Missing function %s' % code)
        return function

    def _set(self, code, allowed):
        function = self._function(code)
        row = self.env['vuma.function.access'].search([
            ('user_id', '=', self.user.id),
            ('function_id', '=', function.id),
        ], limit=1)
        if not row:
            row = self.env['vuma.function.access'].create({
                'user_id': self.user.id,
                'function_id': function.id,
            })
        row.allowed = allowed
        return row

    def _fresh_user(self):
        return self.env['res.users'].browse(self.user.id)

    def _diagnose(self, menu, code):
        """Why ``menu`` is still visible, for a failing assertion."""
        user = self._fresh_user()
        function = self._function(code)
        row = self.env['vuma.function.access'].search([
            ('user_id', '=', user.id), ('function_id', '=', function.id),
        ], limit=1)
        menu_env = self.env['ir.ui.menu'].with_user(user)
        return (
            'user=%s env_user=%s env_su=%s internal=%s system=%s superuser=%s '
            'function=%s row=%s allowed=%s blocked=%s' % (
                user.id, menu_env.env.user.id, menu_env.env.su,
                user._is_internal(), user._is_system(), user._is_superuser(),
                function.id, row.id, row.allowed,
                sorted(menu_env.env['vuma.function']._blocked_menu_ids()),
            ))

    # ------------------------------------------------------------- the baseline

    def test_every_internal_user_starts_with_every_function(self):
        functions = self.env['vuma.function'].search([])
        self.assertTrue(functions)
        rows = self._fresh_user().vuma_function_access_ids
        self.assertEqual(len(rows), len(functions))
        self.assertFalse(rows.filtered(lambda row: not row.allowed))

    def test_new_users_are_seeded_when_they_are_created(self):
        user = self.env['res.users'].create({
            'name': 'Second Access Tester',
            'login': 'vuma_access_test_two',
            'email': 'vuma_access_test_two@example.com',
            'group_ids': [Command.set([self.env.ref('base.group_user').id])],
        })
        self.assertEqual(
            len(user.vuma_function_access_ids),
            len(self.env['vuma.function'].search([])))

    def test_portal_users_are_never_seeded_or_granted(self):
        portal = self.env['res.users'].create({
            'name': 'Portal Tester',
            'login': 'vuma_access_portal',
            'email': 'vuma_access_portal@example.com',
            'group_ids': [Command.set([self.env.ref('base.group_portal').id])],
        })
        self.assertTrue(portal.share)
        self.assertFalse(portal.has_vuma_function(REPORTS))
        self.assertFalse(portal.vuma_function_access_ids)

    def test_a_missing_row_falls_back_to_the_function_default(self):
        function = self._function('action.write_off')
        row = self._fresh_user().vuma_function_access_ids.filtered(
            lambda line: line.function_id == function)
        self.assertEqual(len(row), 1)
        row.unlink()
        # Absence means "use the default", which ships as allowed.
        self.assertTrue(self._fresh_user().has_vuma_function('action.write_off'))
        function.default_allowed = False
        self.assertFalse(self._fresh_user().has_vuma_function('action.write_off'))

    def test_an_unknown_code_never_locks_anyone_out(self):
        self.assertTrue(self._fresh_user().has_vuma_function('action.not.a.code'))

    def test_settings_administrators_always_pass(self):
        self.user.action_vuma_revoke_all_functions()
        self.assertFalse(self._fresh_user().has_vuma_function(APPROVE))
        # The test environment runs as the administrator.
        self.assertTrue(self.env.user.has_vuma_function(APPROVE))

    def test_grant_all_and_revoke_all_buttons(self):
        self.user.action_vuma_revoke_all_functions()
        self.assertFalse(self._fresh_user().has_vuma_function(APPROVE))
        self.assertFalse(self._fresh_user().has_vuma_function(PRICE))
        self.user.action_vuma_grant_all_functions()
        self.assertTrue(self._fresh_user().has_vuma_function(APPROVE))
        self.assertTrue(self._fresh_user().has_vuma_function(PRICE))

    def test_sync_creates_only_the_missing_rows(self):
        user = self._fresh_user()
        user.vuma_function_access_ids.unlink()
        self.assertFalse(self._fresh_user().vuma_function_access_ids)
        functions = self.env['vuma.function'].search([])
        created = self.env['vuma.function']._vuma_sync_access(self._fresh_user())
        self.assertEqual(created, len(functions))
        # A second run adds nothing.
        self.assertEqual(
            self.env['vuma.function']._vuma_sync_access(self._fresh_user()), 0)

    # ------------------------------------------------------------ screens/menus

    def test_revoking_a_section_hides_its_menu(self):
        menu = self.env.ref('vuma_retail_ops.menu_vuma_reports')
        visible = self.env['ir.ui.menu'].with_user(
            self._fresh_user())._visible_menu_ids()
        self.assertIn(menu.id, visible)
        self._set(REPORTS, False)
        visible = self.env['ir.ui.menu'].with_user(
            self._fresh_user())._visible_menu_ids()
        self.assertNotIn(
            menu.id, visible, self._diagnose(menu, REPORTS))

    def test_a_child_screen_hides_only_its_own_menu(self):
        parent = self.env.ref('vuma_retail_ops.menu_vuma_debtors')
        child = self.env.ref('vuma_retail_ops.menu_vuma_credit_limits')
        self._set('section.credit_limits', False)
        visible = self.env['ir.ui.menu'].with_user(
            self._fresh_user())._visible_menu_ids()
        self.assertNotIn(
            child.id, visible, self._diagnose(child, 'section.credit_limits'))
        self.assertIn(parent.id, visible)

    # ------------------------------------------------------------ the guards

    def test_revoking_approve_blocks_the_approver_check(self):
        Uplift = self.env['vuma.uplift.request'].with_user(self._fresh_user())
        # The group check passes; only the function switch is under test.
        Uplift._check_approver()
        self._set(APPROVE, False)
        with self.assertRaises(AccessError):
            Uplift._check_approver()

    def test_revoking_price_edit_blocks_a_selling_price_write(self):
        product = self.env['product.template'].create({
            'name': 'Access Control Price Product',
            'list_price': 10.0,
        })
        product.with_user(self._fresh_user()).write({'list_price': 12.0})
        self._set(PRICE, False)
        with self.assertRaises(AccessError):
            product.with_user(self._fresh_user()).write({'list_price': 20.0})
        self._set(PRICE, True)
        product.with_user(self._fresh_user()).write({'list_price': 20.0})
        self.assertEqual(product.list_price, 20.0)

    def test_revoking_credit_limit_blocks_the_customer_write(self):
        partner = self.env['res.partner'].create({
            'name': 'Access Control Customer',
            'customer_rank': 1,
        })
        partner.with_user(self._fresh_user()).write({'credit_limit': 500.0})
        self._set(CREDIT, False)
        with self.assertRaises(AccessError):
            partner.with_user(self._fresh_user()).write({'credit_limit': 900.0})

    def test_revoking_delete_blocks_unlink(self):
        record = self.env['vuma.customer.change.request'].create({
            'requested_by_id': self.user.id,
            'request_type': 'update_details',
        })
        self._set(DELETE, False)
        with self.assertRaises(AccessError):
            record.with_user(self._fresh_user()).unlink()
        self._set(DELETE, True)
        record.with_user(self._fresh_user()).unlink()
        self.assertFalse(record.exists())

    # ------------------------------------------------------------ the audit trail

    def test_changing_access_is_written_to_the_audit_log(self):
        before = self.env['vuma.audit.log'].search_count([
            ('model', '=', 'res.users'),
            ('res_id', '=', self.user.id),
        ])
        self._set(APPROVE, False)
        after = self.env['vuma.audit.log'].search_count([
            ('model', '=', 'res.users'),
            ('res_id', '=', self.user.id),
        ])
        self.assertEqual(after, before + 1)
        entry = self.env['vuma.audit.log'].search([
            ('model', '=', 'res.users'),
            ('res_id', '=', self.user.id),
        ], limit=1)
        self.assertIn('Revoked', entry.note)

    def test_only_an_administrator_may_change_access(self):
        with self.assertRaises(AccessError):
            self._fresh_user().with_user(
                self._fresh_user()).action_vuma_revoke_all_functions()
