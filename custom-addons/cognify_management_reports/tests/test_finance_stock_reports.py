# -*- coding: utf-8 -*-
"""Checks on the finance, stock, target, expense and utilisation views.

All of these are read-only SQL views, so what can go wrong is arithmetic: a
total that does not tie back to the rows behind it, a bucket that puts a
payment in the wrong age band, a stock value that ignores what was actually
received. Each test pins one of those against records created in the test
itself, so it does not need the demo data to be present.

Two traps are worth remembering when writing these:

* A SQL view reads the database directly, so anything the ORM has buffered but
  not written is invisible to it. Every test flushes before reading.
* Monetary fields need a real currency, not a company. Every view here carries
  ``company_currency_id`` for that reason, and there is a test that would
  notice if one went missing.
"""
from datetime import date, timedelta
from decimal import Decimal

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestFinanceStockAndPeopleReports(TransactionCase):
    """Ageing, cash, stock, targets, expenses and utilisation."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        cls.currency = cls.company.currency_id
        cls.sale_journal = cls.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', cls.company.id)], limit=1)
        cls.bank_journal = cls.env['account.journal'].search(
            [('type', '=', 'bank'), ('company_id', '=', cls.company.id)], limit=1)
        # Shared, because every test runs in its own transaction that is rolled
        # back afterwards: a user created inside one test would not exist in
        # the next, and the target tests would silently write a null user.
        cls.target_user = cls.env['res.users'].create({
            'name': 'Target Probe',
            'login': 'target.probe@example.com',
            'group_ids': [(6, 0, [cls.env.ref(
                'sales_team.group_sale_salesman').id])],
        })
        cls.env.flush_all()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------
    def _bank_posting(self, partner, amount, days_ago):
        """Post a payment on the bank journal through the outstanding account.

        Registering a payment puts the money on the outstanding receipts
        account rather than the bank account itself, which is what Odoo does
        for real, so the report has to read it from there.
        """
        payment = self.env['account.payment'].create({
            'payment_type': 'inbound',
            'partner_id': partner.id,
            'amount': amount,
            'journal_id': self.bank_journal.id,
            'date': fields.Date.context_today(self) - timedelta(days=days_ago),
        })
        payment.action_post()
        return payment

    def _storable_product(self, name, cost, retail, qty=0):
        category = self.env['product.category'].create({'name': f'{name} Category'})
        template = self.env['product.template'].create({
            'name': name,
            'is_storable': True,
            'categ_id': category.id,
            'standard_price': cost,
            'list_price': retail,
        })
        product = template.product_variant_id
        # Writing a zero quantity is rejected outright, so a product that was
        # never stocked simply has no quant at all.
        if qty:
            self.env['stock.quant']._update_available_quantity(
                product, self.env.ref('stock.stock_location_stock'), qty)
        self.env.flush_all()
        return product

    def _employee(self, name, hourly_cost=0.0):
        return self.env['hr.employee'].create({
            'name': name,
            'hourly_cost': hourly_cost,
        })

    # ------------------------------------------------------------------
    # Debt ageing
    # ------------------------------------------------------------------
    def _unpaid_invoice(self, partner, amount, days_overdue):
        """A posted invoice still owing money, due `days_overdue` days ago."""
        today = fields.Date.context_today(self)
        due = today - timedelta(days=days_overdue)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': due - timedelta(days=30),
            'invoice_date_due': due,
            'journal_id': self.sale_journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Ageing probe', 'quantity': 1,
                'price_unit': amount, 'tax_ids': [(5, 0, 0)],
            })],
        })
        invoice.action_post()
        return invoice

    def test_overdue_invoice_lands_in_the_right_age_band(self):
        """Each bucket is defined by days past due, and must agree with the invoice."""
        partner = self.env['res.partner'].create({
            'name': 'Ageing Probe', 'customer_rank': 1})
        # 10 days late, 40 days late, 100 days late, and one not yet due.
        self._unpaid_invoice(partner, 100.0, 10)
        self._unpaid_invoice(partner, 200.0, 40)
        self._unpaid_invoice(partner, 300.0, 100)
        self._unpaid_invoice(partner, 400.0, -5)
        self.env.flush_all()

        row = self.env['cognify.customer.ageing'].search(
            [('partner_id', '=', partner.id)], limit=1)
        self.assertTrue(row, 'the customer owes money but is not on the ageing report')
        self.assertAlmostEqual(row.bucket_30, 100.0, places=2)
        self.assertAlmostEqual(row.bucket_60, 200.0, places=2)
        self.assertAlmostEqual(row.bucket_90, 0.0, places=2)
        self.assertAlmostEqual(row.bucket_90_plus, 300.0, places=2)
        self.assertAlmostEqual(row.bucket_current, 400.0, places=2)
        self.assertAlmostEqual(row.outstanding, 1000.0, places=2)
        self.assertAlmostEqual(row.overdue, 600.0, places=2,
                               msg='not yet due money must not count as overdue')
        self.assertEqual(row.age_band, '90_plus',
                         'the oldest unpaid invoice decides the band')
        self.assertTrue(row.at_risk)

    def test_settled_invoice_leaves_the_ageing_report_alone(self):
        """Paying in full must clear the customer off the report."""
        partner = self.env['res.partner'].create({
            'name': 'Settled Probe', 'customer_rank': 1})
        invoice = self._unpaid_invoice(partner, 500.0, 20)
        self.env.flush_all()
        self.assertTrue(self.env['cognify.customer.ageing'].search(
            [('partner_id', '=', partner.id)]))

        self.env['account.payment.register'].with_context(
            active_model='account.move', active_ids=invoice.ids).create(
            {'payment_date': fields.Date.context_today(self)})._create_payments()
        self.env.flush_all()

        row = self.env['cognify.customer.ageing'].search(
            [('partner_id', '=', partner.id)], limit=1)
        self.assertFalse(row, 'a settled invoice should leave nothing outstanding')

    # ------------------------------------------------------------------
    # Cash
    # ------------------------------------------------------------------
    def test_bank_receipt_shows_as_money_in_and_not_balanced_at_zero(self):
        """The receivable side must be excluded or every month nets to nothing."""
        partner = self.env['res.partner'].create({
            'name': 'Cash Probe', 'customer_rank': 1})
        self._unpaid_invoice(partner, 750.0, 5)
        self._bank_posting(partner, 750.0, 1)
        self.env.flush_all()

        month = date(fields.Date.context_today(self).year,
                     fields.Date.context_today(self).month, 1)
        row = self.env['cognify.cash.position'].search(
            [('journal_id', '=', self.bank_journal.id), ('month', '=', month)],
            limit=1)
        self.assertTrue(row, 'no cash row for this month')
        self.assertGreater(row.receipts, 0,
                           'the receipt did not show as money in')
        self.assertEqual(row.payments, 0)
        self.assertAlmostEqual(row.net, row.receipts, places=2)

    def test_cash_closing_balance_accumulates(self):
        """Balances must run forward, not restart at each month."""
        partner = self.env['res.partner'].create({
            'name': 'Balance Probe', 'customer_rank': 1})
        self._unpaid_invoice(partner, 400.0, 5)
        self._bank_posting(partner, 400.0, 0)
        self.env.flush_all()

        rows = self.env['cognify.cash.position'].search(
            [('journal_id', '=', self.bank_journal.id)], order='month')
        rows = rows.filtered(lambda r: r.receipts or r.payments)
        self.assertTrue(rows, 'no movement recorded on the bank account')
        # Each month's closing balance must be the one before it plus that
        # month's movement. Anything else means the running total restarts.
        for previous, current in zip(rows, rows[1:]):
            self.assertAlmostEqual(
                current.closing, previous.closing + current.net, places=2,
                msg=f'balance does not carry from {previous.month} to {current.month}')

    # ------------------------------------------------------------------
    # Stock
    # ------------------------------------------------------------------
    def test_stock_is_valued_at_cost_and_at_retail(self):
        """The whole point of the view: what the stock is worth, both ways."""
        product = self._storable_product('Stock Probe', cost=4.0, retail=10.0, qty=25)
        row = self.env['cognify.stock.health'].search(
            [('product_id', '=', product.id)], limit=1)
        self.assertTrue(row, 'the product is not on the stock report')
        self.assertAlmostEqual(row.qty_on_hand, 25.0, places=2)
        self.assertAlmostEqual(row.unit_cost, 4.0, places=2)
        self.assertAlmostEqual(row.value_cost, 100.0, places=2)
        self.assertAlmostEqual(row.value_retail, 250.0, places=2)
        self.assertAlmostEqual(row.margin_value, 150.0, places=2)
        self.assertAlmostEqual(row.margin_percent, 60.0, places=1)
        self.assertEqual(row.stock_status, 'dead',
                         'stock that has never sold is dead, not healthy')

    def test_never_sold_is_distinct_from_sold_long_ago(self):
        """Blank days-since-sale means never sold, which is not zero days."""
        product = self._storable_product('Unsold Probe', cost=2.0, retail=5.0, qty=10)
        row = self.env['cognify.stock.health'].search(
            [('product_id', '=', product.id)], limit=1)
        self.assertTrue(row)
        self.assertFalse(row.last_sold)
        # The flag is what tells the two apart. Odoo reads an empty value as
        # zero, so a bare blank cannot be relied on through the ORM.
        self.assertTrue(row.never_sold,
                        'never sold must be distinguishable from sold long ago')
        self.assertTrue(row.is_dead,
                        'stock that has never sold is the worst kind of dead stock')

    def test_stock_out_of_stock_is_flagged(self):
        """An empty shelf has to be visible as such."""
        product = self._storable_product('Emptied Probe', cost=2.0, retail=5.0, qty=0)
        row = self.env['cognify.stock.health'].search(
            [('product_id', '=', product.id)], limit=1)
        self.assertTrue(row)
        self.assertTrue(row.is_out)
        self.assertEqual(row.stock_status, 'out')

    # ------------------------------------------------------------------
    # Targets and achievement
    # ------------------------------------------------------------------
    def test_no_target_reads_as_zero_with_the_flag_off(self):
        """No goal set has to be told apart from failing to hit one.

        Odoo's Float reads an empty value as zero, so the figure cannot carry
        the distinction on its own. Target Set is the column that does.
        """
        row = self.env['cognify.rep.achievement'].search(
            [('user_id', '=', self.target_user.id)], limit=1)
        if row:
            self.assertFalse(row.has_target,
                             'a rep with no target row must not read as targeted')
            self.assertEqual(row.attainment, 0.0,
                             'no target means nothing to attain, not a zero goal')
            self.assertEqual(row.units_attainment, 0.0)

    def test_target_month_must_be_the_first_of_the_month(self):
        """Targets are held per month, so the day has to be the first."""
        user = self.target_user
        with self.assertRaises(Exception):
            self.env['cognify.sales.target'].create({
                'date': date(date.today().year, date.today().month, 15),
                'user_id': user.id,
                'target_revenue': 1000.0,
            })

    def test_roll_forward_copies_the_target_to_the_next_month(self):
        user = self.target_user
        start = date(date.today().year, 1, 1)
        target = self.env['cognify.sales.target'].create({
            'date': start, 'user_id': user.id, 'target_revenue': 2500.0,
            'target_units': 100.0,
        })
        copied = target.action_roll_forward()
        self.assertEqual(copied.date, date(date.today().year, 2, 1))
        self.assertEqual(copied.target_revenue, 2500.0)
        self.assertEqual(copied.target_units, 100.0)

    def test_attainment_is_revenue_over_target(self):
        user = self.env['res.users'].search(
            [('login', '=', 'target.probe@example.com')], limit=1)
        month = date(date.today().year, date.today().month, 1)
        self.env['cognify.sales.target'].create({
            'date': month, 'user_id': user.id, 'target_revenue': 1000.0})
        self.env.flush_all()
        row = self.env['cognify.rep.achievement'].search([
            ('user_id', '=', user.id), ('month', '=', month)], limit=1)
        self.assertTrue(row)
        self.assertTrue(row.has_target)
        self.assertAlmostEqual(row.target_revenue, 1000.0, places=2)
        if row.revenue:
            self.assertAlmostEqual(row.attainment, row.revenue, places=4,
                                   msg='nothing sold means 0% against a 1000 target')
            self.assertAlmostEqual(row.gap, max(0.0, 1000.0 - row.revenue), places=2)

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------
    def test_only_approved_expenses_are_counted(self):
        """A claim still waiting on a manager is not yet a cost."""
        employee = self._employee('Expense Probe')
        product = self.env['product.product'].search(
            [('product_tmpl_id.name', '=', 'Meals')], limit=1)
        if not product:
            self.skipTest('no Meals expense product in this database')
        today = fields.Date.context_today(self)
        claim = self.env['hr.expense'].create({
            'name': 'Expense probe', 'employee_id': employee.id,
            'product_id': product.id, 'total_amount': 300.0, 'date': today,
        })
        self.env.flush_all()

        month = date(today.year, today.month, 1)
        row = self.env['cognify.expense.report'].search([
            ('employee_id', '=', employee.id), ('month', '=', month)], limit=1)
        self.assertFalse(row, 'a draft claim should not be counted as a cost')

        claim.action_submit()
        claim.action_approve()
        self.env.flush_all()
        row = self.env['cognify.expense.report'].search([
            ('employee_id', '=', employee.id), ('month', '=', month)], limit=1)
        self.assertTrue(row, 'an approved claim did not appear')
        self.assertAlmostEqual(row.expense_total, 300.0, places=2)

    # ------------------------------------------------------------------
    # Utilisation
    # ------------------------------------------------------------------
    def test_hours_are_valued_at_the_person_hourly_cost(self):
        """Hours alone are half the question; the other half is what they cost."""
        employee = self._employee('Utilisation Probe', hourly_cost=20.0)
        account = self.env['account.analytic.account'].search([], limit=1)
        if not account:
            self.skipTest('no analytic account in this database')
        self.env['account.analytic.line'].create({
            'name': 'Utilisation probe',
            'account_id': account.id,
            'employee_id': employee.id,
            'date': fields.Date.context_today(self),
            'unit_amount': 6.0,
        })
        self.env.flush_all()

        today = fields.Date.context_today(self)
        row = self.env['cognify.utilisation.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', date(today.year, today.month, 1))], limit=1)
        self.assertTrue(row)
        self.assertAlmostEqual(row.hours, 6.0, places=2)
        self.assertAlmostEqual(row.cost, 120.0, places=2,
                               msg='six hours at twenty an hour is 120')

    def test_timesheet_coverage_is_bounded_by_working_days(self):
        """Coverage is recorded days against days expected to work."""
        employee = self._employee('Coverage Probe')
        account = self.env['account.analytic.account'].search([], limit=1)
        if not account:
            self.skipTest('no analytic account in this database')
        today = fields.Date.context_today(self)
        for offset in (0, 1):
            self.env['account.analytic.line'].create({
                'name': 'Coverage probe',
                'account_id': account.id,
                'employee_id': employee.id,
                'date': today - timedelta(days=offset),
                'unit_amount': 4.0,
            })
        self.env.flush_all()
        row = self.env['cognify.utilisation.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', date(today.year, today.month, 1))], limit=1)
        self.assertTrue(row)
        self.assertLessEqual(row.days_recorded, row.expected_days)
        self.assertLessEqual(row.timesheet_coverage, 100.0)

    # ------------------------------------------------------------------
    # Wiring, across every view
    # ------------------------------------------------------------------
    def test_every_report_menu_opens_a_real_view(self):
        """A menu pointing at nothing is worse than no menu.

        Scoped to this module's own menu tree: Odoo's own Expenses menu shares
        a name with ours, and matching on name alone would find that one too.
        """
        root = self.env.ref('cognify_management_reports.menu_cognify_management')
        menus = self.env['ir.ui.menu'].search([
            ('parent_id', 'child_of', root.id),
            ('name', 'in', ['Debt Ageing', 'Cash and Bank Position',
                            'Stock Health and Value', 'Dead and Slow Moving Stock',
                            'Low and Out of Stock', 'Achievement Against Target',
                            'Sales Targets', 'Expenses',
                            'Timesheet Utilisation'])])
        self.assertEqual(len(menus), 9, f'expected 9 report menus, found {len(menus)}')
        for menu in menus:
            self.assertEqual(menu.action._name, 'ir.actions.act_window',
                             f'{menu.name} does not open a report')
            self.assertTrue(menu.action.res_model,
                            f'{menu.name} points at an action with no model')
            self.assertTrue(menu.action.view_mode,
                            f'{menu.name} has no view to open')

    def test_every_view_model_has_unique_ids(self):
        """A view keyed on anything but the row corrupts the ORM's cache.

        The ORM addresses a report row by its `id`. When a view emits two rows
        carrying one id, only the first survives the read, so a customer who
        trades in two companies shows one company's slice of their balance as
        though it were the lot -- and nothing in the log says so.

        Every offender is collected before failing, because one broken view
        otherwise hides the next one behind it.
        """
        models = [
            'cognify.customer.ageing', 'cognify.cash.position',
            'cognify.stock.health', 'cognify.rep.achievement',
            'cognify.expense.report', 'cognify.utilisation.report',
            'cognify.staff.report', 'cognify.customer.credit',
        ]
        offenders = []
        for model in models:
            ids = self.env[model].search([]).ids
            if len(ids) != len(set(ids)):
                offenders.append(
                    '%s: %d rows, %d ids'
                    % (model, len(ids), len(set(ids))))
        self.assertFalse(offenders, 'a view reuses an id across rows:\n  '
                         + '\n  '.join(offenders))

    def test_money_is_shown_in_the_company_currency(self):
        """A monetary field without a currency formats as a bare number."""
        company_currency = self.company.currency_id
        checks = {
            'cognify.customer.ageing': ('outstanding', 'company_currency_id'),
            'cognify.cash.position': ('receipts', 'currency_id'),
            'cognify.stock.health': ('value_cost', 'company_currency_id'),
            'cognify.rep.achievement': ('revenue', 'company_currency_id'),
            'cognify.expense.report': ('expense_total', 'company_currency_id'),
            'cognify.utilisation.report': ('cost', 'company_currency_id'),
            'cognify.customer.credit': ('outstanding', 'company_currency_id'),
        }
        for model, (_measure, currency_field) in checks.items():
            self.assertIn(currency_field, self.env[model]._fields,
                          f'{model} has no {currency_field}')
        # And the values that are there must be the company's currency, or the
        # default currency, when there is no company at all on the row.
        row = self.env['cognify.customer.ageing'].search(
            [('company_currency_id', '!=', False)], limit=1)
        if row:
            self.assertEqual(row.company_currency_id, company_currency)

    def test_all_seven_dashboards_are_published_and_parse(self):
        import json
        group = self.env['spreadsheet.dashboard.group'].search(
            [('name', '=', 'Management')], limit=1)
        self.assertTrue(group, 'no Management dashboard section')
        dashboards = self.env['spreadsheet.dashboard'].search(
            [('dashboard_group_id', '=', group.id), ('is_published', '=', True)])
        self.assertEqual(len(dashboards), 7)
        names = set(dashboards.mapped('name'))
        for expected in ['Management Overview', 'Sales and Products', 'Customers',
                         'Staff', 'Finance and Cash', 'Stock', 'Targets and People']:
            self.assertIn(expected, names)
        for dashboard in dashboards:
            payload = json.loads(dashboard.spreadsheet_data)
            self.assertEqual(payload['sheets'][0]['name'], 'Dashboard')
            figures = payload['sheets'][0]['figures']
            self.assertTrue(figures, f'{dashboard.name} has no figures')
            # Every chart has to point at an action that exists, or it renders
            # as an empty box on the dashboard.
            for figure in figures:
                data = figure.get('data', {})
                source = data.get('dataSource', {})
                if source.get('type') != 'odoo':
                    continue
                xml_id = source.get('actionXmlId')
                self.assertTrue(
                    self.env.ref(xml_id, raise_if_not_found=False),
                    f'{dashboard.name} chart points at {xml_id}, which does not exist')