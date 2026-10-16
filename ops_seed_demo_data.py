"""Seed six months of demo trading data so the management reports have something
to show.

Everything it creates is marked: product and partner names carry a DEMO tag and
a ``ir.config_parameter`` records the run, so ``ops_cleanup_demo_data.py`` can
take it all back out again. It is written for a demo/dev database, never for a
live one.

What it creates, over the last N months (default 6):
  * 5 product categories and ~24 storable products with cost and retail price
  * 3 salespeople (real users) in 2 sales teams, plus a sales manager
  * ~14 customers, some with a credit limit, some owing money
  * sale orders: quotations, confirmations and cancellations
  * goods received into stock, then delivered out against the orders
  * customer invoices, ~70% settled by real payments, the rest overdue or open
  * 6 employees with weekday attendance and overtime
  * annual leave taken and remaining, plus sick days

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \\
        --http-port=8199 --log-level=error < ops_seed_demo_data.py
"""
import random
from datetime import date, datetime, time, timedelta

from dateutil.relativedelta import relativedelta

env = env(user=1)  # noqa: F821
random.seed(20261014)

DEMO_TAG = 'DEMO'
MONTHS = 6
from odoo import fields  # noqa: E402
TODAY = datetime.combine(fields.Date.today(), time.min)
START = TODAY - relativedelta(months=MONTHS)
company = env.company
currency = company.currency_id

# ---------------------------------------------------------------------------
# Reference data
# ---------------------------------------------------------------------------
CATEGORIES = {
    'Beverages': ['Cola 2L', 'Orange Juice 1L', 'Mineral Water 1.5L', 'Coffee Beans 500g',
                  'Tea Box 100 bags', 'Energy Drink 500ml'],
    'Snacks': ['Crisps 150g', 'Chocolates Bar', 'Peanuts 250g', 'Biscuits 300g',
               'Dried Fruit 200g'],
    'Household': ['Dish Soap 1L', 'Detergent 2L', 'Kitchen Roll 4pk', 'Bin Liners 30ct',
                  'Glass Cleaner 750ml', 'Laundry Powder 3kg'],
    'Personal Care': ['Shampoo 400ml', 'Body Soap 250ml', 'Toothpaste 100ml',
                      'Deodorant 150ml', 'Hand Cream 75ml'],
    'Electronics': ['USB-C Cable 1m', 'Phone Charger 20W', 'Wireless Mouse',
                    'Bluetooth Earbuds', 'Power Bank 10000mAh'],
}

CUSTOMERS = [
    ('Sunrise Supermarket', 'wholesale'), ('Harbour Fresh Foods', 'wholesale'),
    ('Corner Store Alpha', 'retail'), ('City Centre Supermarket', 'wholesale'),
    ('Green Leaf Grocers', 'retail'), ('QuickMart Express', 'wholesale'),
    ('Family Shop Beta', 'retail'), ('Bulk Buy Depot', 'wholesale'),
    ('Uptown Mini Mart', 'retail'), ('Valley Traders', 'wholesale'),
    ('Hilltop Stores', 'retail'), ('Central Wholesale Co', 'wholesale'),
    ('Riverside Market', 'retail'), ('Grand Avenue Retail', 'wholesale'),
]

STREETS = ['1 Main Road', '14 Church Street', '7 Market Square', '22 Depot Lane',
           '5 Station Road', '31 Harare Street', '9 Industrial Way', '18 Valley Road']


def tag(name):
    return f'{DEMO_TAG} {name}'


def rand_time(day, hour=None):
    hour = random.randint(6, 17) if hour is None else hour
    return datetime.combine(day, time(hour, random.choice([0, 15, 30, 45])))


def log(message):
    print(f'  {message}', flush=True)


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------
existing = env['product.category'].search([('name', 'like', f'{DEMO_TAG}%')])
if existing:
    raise SystemExit(
        'Demo data already present. Run ops_cleanup_demo_data.py first.')

categories = {}
products = []
for cat_name, product_names in CATEGORIES.items():
    category = env['product.category'].create({
        'name': tag(cat_name),
        'parent_id': env['product.category'].search(
            [('name', '=', 'All')], limit=1).id or False,
    })
    categories[cat_name] = category
    for product_name in product_names:
        wholesale = cat_name in ('Beverages', 'Snacks')
        cost = round(random.uniform(1.2, 9.5), 2)
        retail = round(cost * (random.uniform(1.25, 1.6) if wholesale
                               else random.uniform(1.5, 2.4)), 2)
        product = env['product.product'].create({
            'name': tag(product_name),
            'categ_id': category.id,
            'type': 'consu',
            'is_storable': True,
            'standard_price': cost,
            'list_price': retail,
            'uom_id': env.ref('uom.product_uom_unit').id,
            'categ_id': category.id,
        })
        products.append((product, cost, retail))
log(f'{len(products)} products in {len(categories)} categories')

# ---------------------------------------------------------------------------
# Salespeople and teams
# ---------------------------------------------------------------------------
team_sales = env['crm.team'].search([('name', '=', 'Sales')], limit=1) or \
    env['crm.team'].create({'name': 'Sales'})
team_field = env['crm.team'].create({'name': tag('Field Sales')})

group_sales_all = env.ref('sales_team.group_sale_salesman_all_leads')
salespeople = []
for first, last in [('Tariro', 'Moyo'), ('Blessing', 'Ncube'), ('Farai', 'Dube')]:
    login = f'demo.sales.{last.lower()}'
    user = env['res.users'].search([('login', '=', login)], limit=1)
    if not user:
        user = env['res.users'].create({
            'name': tag(f'{first} {last}'),
            'login': login,
            'password': 'DemoSales-2026',
            'email': f'{login}@example.com',
            'group_ids': [(6, 0, [group_sales_all.id])],
        })
    user.write({'group_ids': [(4, group_sales_all.id)]})
    env['crm.team.member'].create({
        'crm_team_id': team_sales.id, 'user_id': user.id,
    })
    salespeople.append(user)

field_rep = None
login = 'demo.sales.kamau'
user = env['res.users'].search([('login', '=', login)], limit=1)
if not user:
    field_rep = env['res.users'].create({
        'name': tag('Rudo Kamau'),
        'login': login,
        'password': 'DemoSales-2026',
        'email': 'demo.sales.kamau@example.com',
        'group_ids': [(6, 0, [group_sales_all.id])],
    })
env['crm.team.member'].create({
    'crm_team_id': team_field.id, 'user_id': field_rep.id,
})
salespeople.append(field_rep)

# ---------------------------------------------------------------------------
# Customers
# ---------------------------------------------------------------------------
customers = []
for name, kind in CUSTOMERS:
    partner = env['res.partner'].create({
        'name': tag(name),
        'street': random.choice(STREETS),
        'city': 'Harare',
        'country_id': env.ref('base.zw').id if env.ref('base.zw', raise_if_not_found=False) else False,
        'email': 'accounts@%s.example.com' % name.split()[0].lower(),
        'customer_rank': 1,
        'use_partner_credit_limit': kind == 'wholesale',
        'credit_limit': random.choice([0, 500, 2000, 5000, 10000]) if kind == 'wholesale' else 0,
    })
    customers.append(partner)
log(f'{len(customers)} customers, {len(salespeople)} salespeople')

# Credit reports only mean something if credit limits are switched on for the
# company. This is a setting an accountant would otherwise have to find.
if not company.account_use_credit_limit:
    company.account_use_credit_limit = True
    log('credit limits switched on for the company')

# --------------------------------------------------------------------------
# Stock: receive everything first so deliveries are real
# --------------------------------------------------------------------------
stock_location = env.ref('stock.stock_location_stock')
vendor = env['res.partner'].create({
    'name': tag('Consolidated Suppliers Ltd'),
    'is_company': True, 'street': '2 Industrial Way', 'city': 'Harare',
    'supplier_rank': 1,
})
supplier_location = env.ref('stock.stock_location_suppliers')
incoming_type = env.ref('stock.picking_type_in')

# Stock has to arrive through a real receipt rather than being written straight
# onto the quants. Setting the quantity directly puts stock on hand without any
# valuation behind it, which leaves cost, margin and stock value reporting
# nothing to work from; a receipt gives the moves a cost and makes the figures
# mean what they say.
RECEIPT_QTY = 2000
RECEIPT_DATE = datetime.combine(START, time(8, 0))
receipt = env['stock.picking'].create({
    'picking_type_id': incoming_type.id,
    'location_id': supplier_location.id,
    'location_dest_id': stock_location.id,
    'scheduled_date': RECEIPT_DATE,
    'origin': tag('Opening stock'),
})
env['stock.move'].create([{
    # stock.move has no name of its own in Odoo 20; the description comes from
    # the product, so there is nothing to pass here.
    'product_id': product.id,
    'product_uom_qty': RECEIPT_QTY,
    'uom_id': product.uom_id.id,
    'picking_id': receipt.id,
    'location_id': supplier_location.id,
    'location_dest_id': stock_location.id,
    # The cost the catalogue was built with, so the valuation layer and the
    # product cost agree and the margin on the receipt is the real one.
    'price_unit': cost,
} for product, cost, _retail in products])
receipt.action_confirm()
receipt.button_validate()
if receipt.state != 'done':
    # Generous: six months of trading will eat into this, and an order that
    # cannot be reserved leaves a hole in the stock-on-hand figures.
    for product, cost, _retail in products:
        env['stock.quant']._update_available_quantity(product, stock_location, RECEIPT_QTY)
    log('receipt did not validate; stock written to quants directly '
        '(no valuation, so stock value will read zero)')
else:
    log(f'{RECEIPT_QTY} of each of {len(products)} products received into WH/Stock')

# --------------------------------------------------------------------------
# Sale orders, deliveries and invoices, month by month
# --------------------------------------------------------------------------
journal = env['account.journal'].search(
    [('type', '=', 'sale'), ('company_id', '=', company.id)], limit=1)

invoices_paid = 0
invoices_unpaid = 0
invoices_overdue = 0
orders_confirmed = 0
orders_quoted = 0
cancelled = 0

cursor = START
while cursor < TODAY:
    month_end = min(cursor + relativedelta(months=1) - timedelta(days=1), TODAY)
    # Retail gets busier as the half progresses.
    orders_this_month = random.randint(8, 14)
    for _ in range(orders_this_month):
        order_date = cursor + timedelta(days=random.randint(0, (month_end - cursor).days))
        if order_date >= TODAY:
            break
        customer = random.choice(customers)
        salesperson = random.choice(salespeople)
        lines = random.sample(products, random.randint(3, 8))
        is_wholesale = customer.use_partner_credit_limit

        order = env['sale.order'].create({
            'partner_id': customer.id,
            'user_id': salesperson.id,
            'team_id': (team_sales if salesperson != field_rep else team_field).id,
            'date_order': order_date,
            'company_id': company.id,
            'order_line': [(0, 0, {
                'product_id': product.id,
                'name': product.name,
                'product_uom_qty': random.randint(2, 40) if is_wholesale
                else random.randint(1, 6),
                'product_uom_id': product.uom_id.id,
                'price_unit': retail * (0.8 if is_wholesale else 1.0),
            }) for product, _cost, retail in lines],
        })

        roll = random.random()
        if roll < 0.12:
            order.action_cancel()
            cancelled += 1
            continue
        if roll < 0.32:
            # Left as a quotation: shows up as pipeline, never becomes revenue.
            if random.random() < 0.5:
                order.action_quotation_sent()
            orders_quoted += 1
            continue

        order.action_confirm()
        # Odoo treats date_order as the confirmation date and resets it to the
        # current time inside _prepare_confirmation_values, so the historical
        # date passed at creation is thrown away the moment the order is
        # confirmed. Writing it back afterwards is what actually spreads the
        # demo sales over the months; without this every confirmed order lands
        # on today and every month-by-month report reads as a single spike.
        order.date_order = order_date
        orders_confirmed += 1
        try:
            # action_confirm() already assigns when the goods are on hand, and
            # action_assign() raises even when handed an empty recordset, so
            # each step is guarded on there actually being something to do.
            to_assign = order.picking_ids.filtered(
                lambda p: p.state in ('draft', 'confirmed', 'waiting'))
            if to_assign:
                to_assign.action_assign()
            for picking in order.picking_ids.filtered(lambda p: p.state == 'assigned'):
                for move in picking.move_ids:
                    move.quantity = move.product_uom_qty
                    move.picked = True
                picking.button_validate()
        except Exception as exc:  # noqa: BLE001
            log(f'  delivery problem on {order.name}: {exc}')

        # Invoice it, unless it is still on order.
        if random.random() < 0.88:
            posting_date = min(order_date + timedelta(days=random.randint(1, 12)), TODAY)
            invoice = env['account.move'].create({
                'move_type': 'out_invoice',
                'partner_id': customer.id,
                'invoice_date': posting_date,
                'invoice_date_due': posting_date + timedelta(days=random.choice([15, 30, 45])),
                'invoice_origin': order.name,
                'journal_id': journal.id,
                'company_id': company.id,
                'invoice_line_ids': [(0, 0, {
                    'product_id': line.product_id.id,
                    'name': line.name,
                    'quantity': line.product_uom_qty,
                    'price_unit': line.price_unit,
                    'discount': line.discount or 0.0,
                    'tax_ids': [(6, 0, line.product_id.taxes_id.ids)],
                }) for line in order.order_line if not line.display_type],
            })
            try:
                invoice.action_post()
            except Exception as exc:  # noqa: BLE001
                log(f'  invoice problem on {order.name}: {exc}')
                continue

            age = (TODAY - posting_date).days
            if age > 20 and random.random() < 0.18:
                invoices_overdue += 1          # old and still unpaid
            elif random.random() < 0.78:
                wizard = env['account.payment.register'].with_context(
                    active_model='account.move', active_ids=invoice.ids).create({
                        'payment_date': posting_date + timedelta(days=random.randint(2, 25)),
                    })
                wizard._create_payments()
                invoices_paid += 1
            else:
                invoices_unpaid += 1
    cursor = cursor + relativedelta(months=1)

log(f'{orders_confirmed} orders confirmed, {orders_quoted} quotations, {cancelled} cancelled')
log(f'invoices: {invoices_paid} settled, {invoices_unpaid} open, {invoices_overdue} overdue')

# Six months of trading against a uniform starting quantity leaves every line
# healthy, which would make the stock health report look like a report where
# nothing is ever wrong. A handful of lines are adjusted so the low, slow and
# out of stock states are all represented: two are run down almost to nothing,
# one is emptied completely, and one barely sells at all so it reads as dead.
if products:
    # How much is actually being sold decides how much is left, so the
    # adjustment is relative to that rather than an arbitrary number. A plain
    # query is used here because read_group returns bare tuples in Odoo 20,
    # which is not what the grouped rows look like.
    env.cr.execute("""
        SELECT sol.product_id, SUM(sol.product_uom_qty)
        FROM sale_order_line sol
        JOIN sale_order so ON so.id = sol.order_id
        WHERE so.state = 'sale' AND sol.display_type IS NULL
        GROUP BY sol.product_id
    """)
    sold_by_product = {row[0]: row[1] for row in env.cr.fetchall()}

    # products holds (product, cost, retail) tuples, so the record is the
    # first element.
    variants = [entry[0] for entry in products]

    def set_stock(product, target):
        """Put a product on hand at an exact quantity.

        _update_available_quantity adds or subtracts; it does not set. So the
        current level is read first and the difference applied.
        """
        current = env['stock.quant'].search([
            ('product_id', '=', product.id),
            ('location_id', '=', stock_location.id),
        ]).mapped('quantity')
        current = sum(current) if current else 0.0
        delta = target - current
        if delta:
            env['stock.quant']._update_available_quantity(
                product, stock_location, delta)

    # Two lines left almost empty, one emptied completely, and the slowest
    # mover left holding the most: between them the low, out and dead states
    # all appear on the stock health report.
    low_stock, out_of_stock = variants[:2], variants[2:3]
    slow_mover = min(variants, key=lambda v: sold_by_product.get(v.id, 0))
    for product in low_stock:
        set_stock(product, 12)
    for product in out_of_stock:
        set_stock(product, 0)
    set_stock(slow_mover, 1800)
    log(f'stock adjusted: {len(low_stock)} lines running low, '
        f'{len(out_of_stock)} out of stock, 1 slow mover holding stock')


# --------------------------------------------------------------------------
# Employees, attendance and leave
# --------------------------------------------------------------------------
employee_group = env.ref('hr.group_hr_user')
staff = [('Tendai', 'Banda'), ('Rudo', 'Chari'), ('Blessing', 'Gumbo'),
         ('Nyasha', 'Kaseke'), ('Tafara', 'Mupfumi'), ('Chipo', 'Mutsvanga')]
employees = []
for first, last in staff:
    login = f'demo.staff.{last.lower()}'
    user = env['res.users'].search([('login', '=', login)], limit=1)
    if not user:
        user = env['res.users'].create({
            'name': tag(f'{first} {last}'),
            'login': login,
            'password': 'DemoStaff-2026',
            'email': f'{login}@example.com',
            'group_ids': [(6, 0, [employee_group.id])],
        })
    else:
        user.write({'group_ids': [(4, employee_group.id)]})
    employee = env['hr.employee'].search([('user_id', '=', user.id)], limit=1)
    if not employee:
        employee = env['hr.employee'].create({
            'name': tag(f'{first} {last}'),
            'user_id': user.id,
            'department_id': env.ref('hr.department_department_001').id
            if env.ref('hr.department_department_001', raise_if_not_found=False) else False,
        })
    # An hourly rate is what turns hours into money. Without one the
    # utilisation report can say how many hours were worked but not what they
    # cost, which is the half of the question anyone actually acts on.
    employee.hourly_cost = round(random.uniform(4.5, 14.0), 2)
    employees.append(employee)

# Attendance: every weekday, 8-9 hours, with the odd late start and overtime.
attendance_days = (TODAY - START).days
records = 0
for employee in employees:
    # Roughly a quarter of people work Saturdays in retail.
    works_saturday = random.random() < 0.3
    for day_offset in range(attendance_days):
        day = START + timedelta(days=day_offset)
        if day.weekday() == 5 and not works_saturday:
            continue
        if day.weekday() == 6:
            continue
        if random.random() < 0.06:      # a day off
            continue
        start_hour = 8
        if random.random() < 0.12:
            start_hour = 9              # late
        check_in = datetime.combine(day, time(start_hour, random.choice([0, 5, 10])))
        hours = random.choice([7.5, 8, 8, 8.5, 9])
        if random.random() < 0.15:
            hours += random.choice([1, 1.5, 2])   # overtime
        check_out = check_in + timedelta(hours=hours)
        env['hr.attendance'].create({
            'employee_id': employee.id,
            'check_in': check_in,
            'check_out': check_out,
        })
        records += 1
log(f'{records} attendance records for {len(employees)} employees')

# Leave: annual and sick, some taken and some still to come.
# Leave: Odoo 20 has no hr.leave.type; time off is booked against a work entry
# type. Annual leave is the one that needs an allocation, which is what makes
# "no entitlement left" a real thing rather than a guess: Odoo refuses a
# request the allocation does not cover, and the manager books the days beyond
# it as short paid against "Leave without pay".
work_entry_type = env['hr.work.entry.type']
annual = work_entry_type.create({
    'name': tag('Annual Leave'), 'code': 'LEAVE200',
    'count_as': 'absence', 'requires_allocation': True,
    'leave_validation_type': 'hr', 'amount_rate': 1.0,
})
sick = work_entry_type.create({
    'name': tag('Sick Leave'), 'code': 'LEAVE201',
    'count_as': 'absence', 'requires_allocation': False,
    'leave_validation_type': 'hr', 'amount_rate': 1.0,
})
unpaid = env['hr.work.entry.type'].search([('code', '=', '158.00')], limit=1)
if not unpaid:
    unpaid = work_entry_type.create({
        'name': tag('Short Paid Leave'), 'code': 'SHORTPD',
        'count_as': 'absence', 'requires_allocation': False,
        'leave_validation_type': 'manager', 'amount_rate': 0.0,
    })

# An accrual plan hands out days over the year rather than in one lump, which
# is how leave is normally granted.
accrual_plan = env['hr.leave.accrual.plan'].create({
    'name': tag('Annual Leave Accrual'),
    'work_entry_type_id': annual.id,
    'company_id': company.id,
})
env['hr.leave.accrual.level'].create({
    'accrual_plan_id': accrual_plan.id,
    'start_count': 0,
    'start_type': 'day',
    'milestone_date': 'creation',
    'added_value': 2.08,
    'added_value_type': 'day',
    'frequency': 'monthly',
})
log(f'annual leave type, accrual plan and short-paid leave type created')

# One person is given a deliberately small allocation so the demo actually shows
# leave running out and the extra days going short paid.
SMALL_ENTITLEMENT_EMPLOYEE = 1
SMALL_ALLOCATION_DAYS = 5
ANNUAL_DAYS = 25

leave_created = 0
# Odoo refuses time off the allocation does not cover, so each person is
# granted their days first.
allocations = 0
for index, employee in enumerate(employees):
    days = SMALL_ALLOCATION_DAYS if index == SMALL_ENTITLEMENT_EMPLOYEE else ANNUAL_DAYS
    allocation = env['hr.leave.allocation'].create({
        'name': tag('Annual leave allocation'),
        'employee_id': employee.id,
        'work_entry_type_id': annual.id,
        'accrual_plan_id': accrual_plan.id,
        'date_from': date(TODAY.year, 1, 1),
        'number_of_days': days,
    })
    try:
        allocation.action_approve()
    except Exception:  # noqa: BLE001
        pass
    allocations += 1
log(f'{allocations} leave allocations granted ({ANNUAL_DAYS} days, one person on 5)')

# Odoo refuses time off the allocation does not cover, so keep a diary and
# only book days that are free and affordable.
diary = {employee.id: [] for employee in employees}


def book(employee, day, days, name, leave_type=None):
    """Create a time-off request if the employee is free that week.

    The overlap rule is a constraint, and Odoo evaluates those lazily: create()
    can return a record that only fails on a later read. So the create, the
    validation of the dates and the approval all happen inside one savepoint,
    and the day is only added to the diary once the lot of it has stuck.
    """
    global leave_created
    leave_type = leave_type or annual
    end = day + timedelta(days=days - 1)
    for busy_from, busy_to in diary[employee.id]:
        if day <= busy_to and end >= busy_from:
            return False
    try:
        with env.cr.savepoint():
            # In Odoo 20 date_from/date_to are computed from request_date_*;
            # writing them directly is silently ignored and everything collapses
            # onto today.
            leave = env['hr.leave'].create({
                'employee_id': employee.id,
                'work_entry_type_id': leave_type.id,
                'request_date_from': day,
                'request_date_to': day + timedelta(days=days - 1),
                'name': tag(name),
            })
            # Force the deferred date constraint to run now rather than later.
            leave.read(['number_of_days', 'state'])
            try:
                leave.action_validate()
            except Exception:  # noqa: BLE001 - approval rules are optional here
                pass
    except Exception:  # noqa: BLE001 - overlap, or a policy we did not expect
        return False
    diary[employee.id].append((day, end))
    leave_created += 1
    return True


for index, employee in enumerate(employees):
    is_small = index == SMALL_ENTITLEMENT_EMPLOYEE
    # Annual leave already taken. The person on the short allocation gets this
    # below, day by day, so that the balance is actually used up.
    if not is_small:
        for _ in range(random.randint(2, 4)):
            day = START + timedelta(days=random.randint(0, max(1, attendance_days - 10)))
            book(employee, day, random.randint(1, 5), 'Annual leave')
    # Sick days scattered around
    for _ in range(random.randint(0, 3)):
        day = START + timedelta(days=random.randint(0, max(1, attendance_days - 2)))
        book(employee, day, 1, 'Sick day', leave_type=sick)
    # Time off still ahead of them, so "available" is not simply zero
    for _ in range(random.randint(1, 2)):
        future = TODAY + timedelta(days=random.randint(10, 150))
        book(employee, future, random.randint(2, 6), 'Annual leave booked')

# One person is deliberately put on a short allocation so the demo shows leave
# genuinely running out. Odoo refuses a request the allocation does not cover,
# so the days beyond it are booked against the unpaid type instead - which is
# the short paid arrangement a manager actually makes.
exhausted = employees[SMALL_ENTITLEMENT_EMPLOYEE] if employees else None
if exhausted is not None:
    # Booked one day at a time on purpose. A multi-day booking can be rejected
    # as an overlap partway through, and then the running total drifts out of
    # step with what was actually booked - which would leave the person with
    # leave still in hand while the demo claimed they had run out of it.
    used = 0
    last_day = START
    attempts = 0
    while used < SMALL_ALLOCATION_DAYS and attempts < 200:
        attempts += 1
        day = START + timedelta(days=random.randint(0, max(1, attendance_days - 10)))
        if book(exhausted, day, 1, 'Annual leave'):
            used += 1
            last_day = max(last_day, day)
    # Everything from after the entitlement ran out is short paid.
    short_paid = 0
    for offset in (7, 17, 27):
        day = last_day + timedelta(days=offset)
        if book(exhausted, day, 1, 'Short paid leave', leave_type=unpaid):
            short_paid += 1
    log(f'{exhausted.name} used {used} of {SMALL_ALLOCATION_DAYS} allocated days, '
        f'then took {short_paid} further days short paid')

log(f'{leave_created} leave requests')

# --------------------------------------------------------------------------
# Sales targets
# --------------------------------------------------------------------------
# Community Odoo has nowhere to put a target, so the achievement report has
# nothing to measure against without them. Each rep gets a monthly revenue
# goal, set close to what they have actually been selling so the report shows a
# realistic spread of people on target and people short of it.
# Base the goal on the rep's own run rate: their total sales over the whole
# seeded period divided by the number of months. Averaging over a short
# lookback would be far noisier here, because a rep only makes a handful of
# orders a month and one quiet month would halve their goal.
#
# Averaging over the number of rows rather than the number of months would be
# worse still: that gives the average per order line, which is a hundredth of
# the right figure and makes attainment read in the thousands of percent.
month_cursor = date(TODAY.year, TODAY.month, 1)
period_start = month_cursor - relativedelta(months=MONTHS - 1)
run_rate = {}
for rep in salespeople:
    past = env['sale.order'].search([
        ('user_id', '=', rep.id),
        ('state', '=', 'sale'),
        ('date_order', '>=', period_start),
        ('date_order', '<', month_cursor),
    ])
    total = sum(past.mapped('amount_total'))
    run_rate[rep.id] = (total / MONTHS) if total else 8000.0

targets_created = 0
for _ in range(MONTHS):
    for rep in salespeople:
        typical = run_rate[rep.id]
        # Nudged either side so the report shows a spread of people on target
        # and people short of it rather than everyone landing on 100%.
        env['cognify.sales.target'].create({
            'date': month_cursor,
            'user_id': rep.id,
            'target_revenue': round(typical * random.uniform(0.7, 1.1), 2),
            'target_units': round(typical / 12, 1),
            'target_margin': round(typical * random.uniform(0.2, 0.35), 2),
        })
        targets_created += 1
    month_cursor -= relativedelta(months=1)
log(f'{targets_created} sales targets across {len(salespeople)} reps')

# --------------------------------------------------------------------------
# Expenses
# --------------------------------------------------------------------------
# Claims against the expense products the business already carries, spread
# over the same months. Approved only, which is what the report counts: a
# claim still waiting on a manager is not yet a cost.
expense_products = {
    'travel': 'Travel & Accommodation',
    'meals': 'Meals',
    'communication': 'Communication',
    'gifts': 'Gifts',
}
# The product name is translated and stored as jsonb, which the ORM will not
# take in a domain, so the names are matched here and browsed back into a
# recordset. The name lives on the template, not on the variant.
expense_products = {
    kind: product
    for kind, wanted in expense_products.items()
    for product in env['product.product'].search(
        [('product_tmpl_id.name', '=', wanted)], limit=1)
}
expense_total = 0
expense_count = 0
month_cursor = date(TODAY.year, TODAY.month, 1)
for _ in range(MONTHS):
    for employee in employees:
        for _ in range(random.randint(2, 7)):
            kind = random.choice(list(expense_products))
            product = expense_products[kind]
            claim = env['hr.expense'].create({
                'name': tag(f'{kind.title()} claim'),
                'employee_id': employee.id,
                'product_id': product.id,
                'total_amount': round(random.uniform(12, 260), 2),
                'date': month_cursor + timedelta(days=random.randint(0, 27)),
            })
            try:
                # Odoo 20 has no expense sheets: a claim is submitted and
                # approved on its own. Approval runs the company policy, so it
                # is wrapped because a missing category would otherwise stop
                # the whole seed.
                claim.action_submit()
                claim.action_approve()
                expense_total += claim.total_amount
                expense_count += 1
            except Exception:  # noqa: BLE001
                pass
    month_cursor -= relativedelta(months=1)
log(f'{expense_count} approved expense claims totalling {expense_total:,.2f}')

# --------------------------------------------------------------------------
# Timesheets
# --------------------------------------------------------------------------
# Hours against a project, some of it billable. A "Billable" analytic plan is
# what separates chargeable work from internal work in Odoo, and the
# utilisation report reads that plan to work out the billable share. Without
# the plan there is nothing to tell the two apart, so one is created.
# Plan names are translated jsonb, which the ORM will not accept in a domain,
# so the plan is looked up here instead.
billable_plan = next(
    (plan for plan in env['account.analytic.plan'].search([])
     if plan.name == 'Billable'), None)
if not billable_plan:
    # account.analytic.plan has no company of its own in Odoo 20; it belongs
    # to the companies through the accounts that use it.
    billable_plan = env['account.analytic.plan'].create({
        'name': tag('Billable'),
    })
project_account = env['account.analytic.account'].search(
    [('name', '=', tag('Rollout Project'))], limit=1)
if not project_account:
    project_account = env['account.analytic.account'].create({
        'name': tag('Rollout Project'),
        'plan_id': billable_plan.id,
        'company_id': company.id,
    })

timesheet_lines = 0
month_cursor = date(TODAY.year, TODAY.month, 1)
for _ in range(MONTHS):
    day = month_cursor
    # TODAY is a datetime because Odoo's date helper returns one; the month
    # walk below is in plain dates, so both sides are compared as dates.
    while day < min(month_cursor + relativedelta(months=1), TODAY.date()):
        if day.weekday() < 5:
            for employee in employees:
                billable = random.random() < 0.65
                env['account.analytic.line'].create({
                    'name': tag('Rollout work'),
                    'account_id': project_account.id,
                    'employee_id': employee.id,
                    'date': day,
                    'unit_amount': round(random.uniform(2, 9), 1),
                })
                timesheet_lines += 1
        day += timedelta(days=1)
    month_cursor -= relativedelta(months=1)
log(f'{timesheet_lines} timesheet entries against {project_account.name}')

# --------------------------------------------------------------------------
params = env['ir.config_parameter'].sudo()
params.set_str('cognify_demo_data.seeded_at', datetime.now().isoformat())
params.set_int('cognify_demo_data.months', MONTHS)
env.cr.commit()

print('\n' + '=' * 60)
print(f'  demo data seeded: {START:%d %b %Y} to {TODAY:%d %b %Y}')
print(f'  {len(products)} products, {len(customers)} customers, '
      f'{len(salespeople)} salespeople')
print(f'  {orders_confirmed} confirmed orders, {orders_quoted} quotations, '
      f'{cancelled} cancelled')
print(f'  {invoices_paid + invoices_unpaid + invoices_overdue} invoices '
      f'({invoices_paid} settled, {invoices_unpaid} open, {invoices_overdue} overdue)')
print(f'  {len(employees)} employees, {records} attendances, {leave_created} leave requests')
print(f'  {targets_created} sales targets, {expense_count} expense claims, '
      f'{timesheet_lines} timesheet entries')
print('=' * 60)
print('committed. Remove with ops_cleanup_demo_data.py')