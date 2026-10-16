{
    'name': 'Cognify Management Reports',
    'version': '1.0.3',
    'category': 'Sales/Reporting',
    'summary': 'Management dashboards and reports: sales by day, month, channel '
               'and category, cost of sales, salesperson performance, product '
               'turnover, customer balances and credit, and staff hours and leave.',
    'description': """
Management Reports
==================

The numbers a manager actually asks for, in one place, without building a
pivot by hand every time.

Where the numbers come from
---------------------------
Money is read from **posted invoices**, because that is what the business has
actually been paid for and what the accountant will agree with. Volume is read
from **orders**, because an order that has not been invoiced yet still means
something. The split matters: revenue, cost, customer balances and turnover all
follow the invoice; pipeline and order counts follow the order.

Every report is an ordinary Odoo view, so it drills down from the number to the
row that made it: click a month, get the orders; click a customer, get their
invoices.

Reports
-------
* **Sales** by day, by month, by channel, by category and by salesperson
* **Cost of sales** and gross margin, from the same lines
* **Product turnover**, highest and lowest, as a ranked table
* **Debt ageing**: what each customer owes, split by how far past due
* **Cash and bank**: what moved through the accounts, month by month
* **Stock health**: value at cost and retail, dead stock, stock running out
* **Targets vs actual** per salesperson, per month
* **Expenses** claimed, per person, as a share of revenue
* **Timesheet utilisation**: billable share, cost against revenue
* **Staff hours** worked, overtime and late arrivals, by month
* **Time off** taken, booked and still available, by month and by type

Time off is read from the allocations HR actually granted, so the entitlement
is a real number rather than a setting. Once it is used up, Odoo refuses
further requests against it, and the days beyond it are booked as short paid
against an unpaid time type - which the report shows as Short Paid Days, with
a flag for anyone whose entitlement has run out and a shortfall showing
approved leave the rest of the year cannot cover.

Dashboards
----------
The same figures as live dashboards, which read straight from Odoo rather than
from a nightly snapshot, so they cannot drift from the books.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': [
        'sale_management', 'sale_margin', 'sales_team', 'account',
        'purchase_stock', 'stock',
        'hr_attendance', 'hr_holidays',
        'hr_expense', 'hr_timesheet', 'purchase',
        'spreadsheet_dashboard',
    ],
    'data': [
        'security/ir.access.csv',
        'views/customer_credit_views.xml',
        'views/staff_report_views.xml',
        'views/finance_stock_views.xml',
        'views/sale_report_views.xml',
        'views/res_config_settings_views.xml',
        'data/dashboard_data.xml',
    ],
    'installable': True,
    'application': True,
}