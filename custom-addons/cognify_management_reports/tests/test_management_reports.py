# -*- coding: utf-8 -*-
"""Checks on the management report views.

These are read-only SQL views, so what can go wrong is arithmetic: a total that
does not tie back to the rows behind it, a running balance that restarts, a
monetary field with no currency. Each test pins one of those against records
created in the test itself, so it does not need the demo data to be present.
"""
from datetime import date, timedelta
from decimal import Decimal

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestManagementReports(TransactionCase):
    """Customer balances and staff hours."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.ref('base.main_company')
        # An approved time off allocation cannot be deleted, so anything the
        # tests allocate has to be refused again on the way out.
        cls.allocations_to_refuse = []

    # ------------------------------------------------------------------
    # Customer credit
    # ------------------------------------------------------------------
    def _customer_with_invoice(self, amount, due_date, paid=False):
        partner = self.env['res.partner'].create({
            'name': 'Balance Probe Customer',
            'customer_rank': 1,
            'credit_limit': 1000.0,
        })
        journal = self.env['account.journal'].search(
            [('type', '=', 'sale'), ('company_id', '=', self.company.id)], limit=1)
        invoice = self.env['account.move'].create({
            'move_type': 'out_invoice',
            'partner_id': partner.id,
            'invoice_date': due_date - timedelta(days=30),
            'invoice_date_due': due_date,
            'journal_id': journal.id,
            'invoice_line_ids': [(0, 0, {
                'name': 'Probe line', 'quantity': 1,
                'price_unit': amount, 'tax_ids': [(5, 0, 0)],
            })],
        })
        invoice.action_post()
        if paid:
            self.env['account.payment.register'].with_context(
                active_model='account.move', active_ids=invoice.ids).create({
                    'payment_date': due_date - timedelta(days=5)})._create_payments()
            # A SQL view reads the database directly, so anything the ORM has
            # buffered but not written yet is invisible to it. Flush before
            # reading the report, or it still shows the old figures.
            self.env.flush_all()
        return partner, invoice

    def test_outstanding_and_overdue_split(self):
        """Money owed, money late, and the two must agree with the invoices."""
        partner, invoice = self._customer_with_invoice(
            400.0, due_date=date.today() - timedelta(days=10))
        row = self.env['cognify.customer.credit'].search(
            [('partner_id', '=', partner.id)], limit=1)
        self.assertTrue(row, 'customer missing from the credit report')
        self.assertAlmostEqual(row.billed, 400.0, places=2)
        self.assertAlmostEqual(row.outstanding, 400.0, places=2)
        self.assertAlmostEqual(row.overdue, 400.0, places=2, msg='due date has passed')
        self.assertGreaterEqual(row.overdue_days, 10)
        self.assertAlmostEqual(row.credit_limit, 1000.0, places=2)
        self.assertAlmostEqual(row.credit_available, 600.0, places=2)

    def test_paid_invoice_leaves_nothing_outstanding(self):
        partner, invoice = self._customer_with_invoice(
            250.0, due_date=date.today() + timedelta(days=15), paid=True)
        row = self.env['cognify.customer.credit'].search(
            [('partner_id', '=', partner.id)], limit=1)
        self.assertEqual(invoice.payment_state, 'paid',
                         'the invoice was not settled: '
                         f'state={invoice.state} residual={invoice.amount_residual}')
        self.assertAlmostEqual(
            row.paid, 250.0, places=2,
            msg=f'billed={row.billed} paid={row.paid} outstanding={row.outstanding} '
                f'invoice_total={invoice.amount_total} residual={invoice.amount_residual} '
                f'company={invoice.company_id.id}')
        self.assertAlmostEqual(row.outstanding, 0.0, places=2)
        self.assertAlmostEqual(row.overdue, 0.0, places=2)

    def test_over_limit_customer_is_flagged(self):
        partner, _invoice = self._customer_with_invoice(
            1500.0, due_date=date.today() + timedelta(days=5))
        row = self.env['cognify.customer.credit'].search(
            [('partner_id', '=', partner.id)], limit=1)
        self.assertTrue(row.over_limit, '1500 owed against a 1000 limit')
        self.assertLess(row.credit_available, 0)

    def test_totals_tie_back_to_the_invoices(self):
        """The report total has to equal the invoices behind it."""
        partner, _invoice = self._customer_with_invoice(
            333.33, due_date=date.today() + timedelta(days=1))
        rows = self.env['cognify.customer.credit'].search([('partner_id', '=', partner.id)])
        expected = self.env['account.move'].search([
            ('partner_id', '=', partner.id), ('state', '=', 'posted'),
        ]).mapped('amount_residual')
        self.assertAlmostEqual(sum(rows.mapped('outstanding')),
                               sum(expected), places=2)

    # ------------------------------------------------------------------
    # Staff hours and time off
    # ------------------------------------------------------------------
    def _employee_with_attendance(self, shifts):
        user = self.env['res.users'].create({
            'name': 'Hours Probe', 'login': 'probe.hours',
            'group_ids': [(6, 0, [self.env.ref('hr.group_hr_user').id])],
        })
        employee = self.env['hr.employee'].create({
            'name': 'Hours Probe', 'user_id': user.id})
        for day, worked in shifts:
            check_in = datetime_of(day, 8)  # every shift starts on time
            self.env['hr.attendance'].create({
                'employee_id': employee.id,
                'check_in': check_in,
                'check_out': check_in + timedelta(hours=worked),
            })
        return employee

    def test_hours_and_overtime_are_counted_per_shift(self):
        """Overtime is the part of a shift past eight hours, not the month."""
        employee = self._employee_with_attendance([
            (date.today() - timedelta(days=3), 8.0),    # normal
            (date.today() - timedelta(days=2), 10.0),   # 2 over
            (date.today() - timedelta(days=1), 9.0),    # 1 over
        ])
        row = self.env['cognify.staff.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', date(date.today().year, date.today().month, 1)),
        ], limit=1)
        self.assertTrue(row, 'no row for this month')
        self.assertEqual(row.shifts, 3)
        self.assertAlmostEqual(row.hours_worked, 27.0, places=2)
        self.assertAlmostEqual(row.overtime_hours, 3.0, places=2,
                               msg='8h + 10h + 9h is 27h, 3h of it past eight a day')
        self.assertAlmostEqual(row.avg_hours, 9.0, places=2)

    def test_late_arrival_is_counted(self):
        """One shift on time and one at 09:15, on different days.

        A second shift on the same day for the same person is refused by
        Odoo, so the two have to be on separate days.
        """
        employee = self._employee_with_attendance([
            (date.today() - timedelta(days=1), 8.0),
        ])
        self.env['hr.attendance'].create({
            'employee_id': employee.id,
            'check_in': datetime_of(date.today(), 9, 15),
            'check_out': datetime_of(date.today(), 17, 15),
        })
        row = self.env['cognify.staff.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', date(date.today().year, date.today().month, 1)),
        ], limit=1)
        self.assertEqual(row.late_arrivals, 1)

    def _recent_weekday(self, days_ago):
        """A weekday `days_ago` days back, so time off can be booked on it.

        Time off on a day the person is not scheduled to work is refused by
        Odoo, so a probe that picks its dates at random fails on roughly a
        third of runs depending on the day of the week.
        """
        day = fields.Date.context_today(self) - timedelta(days=days_ago)
        while day.weekday() >= 5:
            day -= timedelta(days=1)
        return day

    def _employee_with_allocation(self, days):
        """An employee granted `days` of annual leave through a real allocation.

        Entitlement comes from what HR allocated, not from a setting, so the
        test has to allocate it properly or there is nothing for the report to
        read. An approved allocation cannot be deleted, so the helper also
        leaves behind the means to refuse it again.
        """
        employee = self._employee_with_attendance([(date.today(), 8.0)])
        work_type = self.env['hr.work.entry.type'].create({
            'name': 'Probe Allocated Leave', 'code': 'PROBEALLOC',
            'count_as': 'absence', 'requires_allocation': True,
            'leave_validation_type': 'hr', 'amount_rate': 1.0,
        })
        allocation = self.env['hr.leave.allocation'].create({
            'name': 'Probe allocation',
            'employee_id': employee.id,
            'work_entry_type_id': work_type.id,
            'date_from': date(date.today().year, 1, 1),
            'number_of_days': days,
        })
        try:
            allocation.action_approve()
        except Exception:  # noqa: BLE001 - approval rules are optional here
            pass
        # The entitlement only counts once the allocation is approved, so a
        # silent failure here would look like a bug in the report.
        allocation.invalidate_recordset()
        self.assertEqual(allocation.state, 'validate',
                         f'the probe allocation was not approved: {allocation.state}')
        # The report reads the database directly, so anything the ORM has
        # buffered but not written is invisible to it. Flush before the test
        # reads the entitlement back, or it sees nothing.
        self.env.flush_all()
        self.allocations_to_refuse.append(allocation)
        return employee, work_type

    def test_leave_balance_carries_across_quiet_months(self):
        """Available leave must not reset in a month nobody books any."""
        entitlement = 25.0
        employee, leave_type = self._employee_with_allocation(entitlement)
        first = date(date.today().year, date.today().month, 10)
        last = date(date.today().year, date.today().month, 14)
        leave = self.env['hr.leave'].create({
            'employee_id': employee.id,
            'work_entry_type_id': leave_type.id,
            'request_date_from': first, 'request_date_to': last,
            'name': 'Probe leave',
        })
        leave.action_approve()
        self.env.flush_all()

        # Pinned to exact months. The view orders by month descending, so
        # asking for "this month or later" with a limit of one hands back the
        # last month in the window - a year the allocation does not cover.
        month_start = date(date.today().year, date.today().month, 1)
        before = self.env['cognify.staff.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', date(date.today().year, date.today().month - 1 or 12, 1))
            if date.today().month > 1 else
            date(date.today().year - 1, 12, 1)], limit=1)
        after = self.env['cognify.staff.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', month_start)], limit=1)
        self.assertTrue(after, 'no row for this month')
        taken = Decimal(str(leave.number_of_days))
        self.assertGreater(taken, 0)
        self.assertAlmostEqual(after.annual_entitlement, entitlement, places=1,
                               msg='the entitlement must come from the allocation')
        self.assertAlmostEqual(after.annual_leave_cumulative, float(taken), places=1)
        self.assertAlmostEqual(
            after.leave_available, entitlement - float(taken), delta=0.5)
        self.assertAlmostEqual(
            float(before.leave_available), entitlement, places=1,
            msg='a month with no leave should still show the full balance')

    def test_time_off_beyond_the_allocation_is_short_paid(self):
        """Once the entitlement is gone the extra days cost the employee nothing.

        This is the arrangement Odoo itself supports: a type requiring an
        allocation blocks requests the allocation does not cover, and the days
        beyond it are booked against an unpaid time type. The report has to
        show those days separately, and flag that the entitlement is spent.
        """
        employee, leave_type = self._employee_with_allocation(2.0)
        unpaid = self.env['hr.work.entry.type'].search(
            [('code', '=', '158.00')], limit=1)
        if not unpaid:
            unpaid = self.env['hr.work.entry.type'].create({
                'name': 'Probe Short Paid', 'code': 'PROBESHORT',
                'count_as': 'absence', 'requires_allocation': False,
                'leave_validation_type': 'manager', 'amount_rate': 0.0,
            })
        # Time off can only be booked on days the person is scheduled to work,
        # so the probe days have to be weekdays.
        first = self._recent_weekday(9)
        second = self._recent_weekday(6)
        third = self._recent_weekday(3)
        for day in (first, second):
            booked = self.env['hr.leave'].create({
                'employee_id': employee.id,
                'work_entry_type_id': leave_type.id,
                'request_date_from': day, 'request_date_to': day,
                'name': 'Probe allocated leave',
            })
            booked.action_approve()
        short_paid = self.env['hr.leave'].create({
            'employee_id': employee.id,
            'work_entry_type_id': unpaid.id,
            'request_date_from': third, 'request_date_to': third,
            'name': 'Probe short paid leave',
        })
        short_paid.action_approve()
        self.env.flush_all()

        # Read the month off the day the short paid leave was actually booked,
        # which is where the report will have recorded it.
        month = date(third.year, third.month, 1)
        row = self.env['cognify.staff.report'].search([
            ('employee_id', '=', employee.id), ('month', '=', month)], limit=1)
        self.assertTrue(row)
        self.assertGreater(float(short_paid.number_of_days), 0)
        self.assertAlmostEqual(row.short_paid_days, float(short_paid.number_of_days),
                               places=1,
                               msg='days booked on an unpaid type are short paid')
        self.assertAlmostEqual(row.annual_leave_cumulative, 2.0, places=1)
        self.assertLessEqual(row.leave_available, 0.0)
        self.assertTrue(row.leave_exhausted,
                        'the entitlement is used up, so the flag must be set')

    def test_unallocated_year_is_not_reported_as_exhausted(self):
        """Never being given leave is a different thing from running out of it.

        Saying a person is out of leave when no allocation was ever made for
        them would hide the actual problem, which is that nobody set one up.
        """
        employee = self._employee_with_attendance([(date.today(), 8.0)])
        self.env.flush_all()
        row = self.env['cognify.staff.report'].search([
            ('employee_id', '=', employee.id),
            ('month', '=', date(date.today().year, date.today().month, 1))], limit=1)
        self.assertTrue(row)
        self.assertEqual(row.annual_entitlement, 0.0)
        self.assertFalse(row.leave_exhausted)

    def test_every_row_has_a_unique_id(self):
        """A view keyed on something other than the row breaks the ORM cache."""
        rows = self.env['cognify.staff.report'].search([])
        if len(rows) < 2:
            self.skipTest('not enough rows to prove anything')
        self.assertEqual(len(rows), len(set(rows.ids)),
                         'the report reuses an id across months')

    # ------------------------------------------------------------------
    # Wiring
    # ------------------------------------------------------------------
    def test_menus_point_at_actions_that_exist(self):
        menus = self.env['ir.ui.menu'].search([
            ('name', 'in', ['Sales by Day', 'Sales by Month', 'Sales by Channel',
                            'Sales by Category', 'Cost of Sales',
                            'Product Turnover', 'Slowest Moving Products',
                            'Outstanding Balances', 'Credit Available',
                            'Salesperson by Month', 'Hours Worked',
                            'Leave Taken and Available'])])
        self.assertTrue(menus, 'no report menus found')
        for menu in menus:
            self.assertEqual(menu.action._name, 'ir.actions.act_window',
                             f'{menu.name} does not open a report')
            self.assertTrue(menu.action.res_model,
                            f'{menu.name} points at an action with no model')
            self.assertTrue(menu.action.view_mode,
                            f'{menu.name} has no view to open')

    def test_dashboards_are_published_and_parse(self):
        group = self.env['spreadsheet.dashboard.group'].search(
            [('name', '=', 'Management')], limit=1)
        self.assertTrue(group, 'no Management dashboard section')
        dashboards = self.env['spreadsheet.dashboard'].search(
            [('dashboard_group_id', '=', group.id), ('is_published', '=', True)])
        self.assertEqual(len(dashboards), 7)
        import json
        for dashboard in dashboards:
            payload = json.loads(dashboard.spreadsheet_data)
            self.assertEqual(payload['sheets'][0]['name'], 'Dashboard')
            self.assertTrue(payload['sheets'][0]['figures'],
                            f'{dashboard.name} has no figures')


def datetime_of(day, hour, minute=0):
    from datetime import datetime, time
    return datetime.combine(day, time(hour, minute))