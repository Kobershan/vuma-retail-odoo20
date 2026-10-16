# -*- coding: utf-8 -*-
"""Staff hours worked and time off, one row per person per month.

Two sources that do not sit together by default:

* ``hr.attendance`` holds check-in and check-out per shift, so hours worked,
  overtime and late arrivals have to be derived from the pairs.
* ``hr.leave`` holds time-off requests, which roll up by work entry type.

Odoo ships reports for each on its own, but neither answers "how many hours
did this person work last month, and how much leave have they got left". This
view does, and it is built one row per employee per month so it can be pivoted
by month or by person.

Leave available is entitlement less what has been taken up to the end of that
month. The entitlement is a setting rather than a hard-coded 25, because it is
a policy, and because with ``requires_allocation`` off there is no allocation
record to read it from.
"""
from odoo import fields, models, tools


class StaffReport(models.Model):
    _name = 'cognify.staff.report'
    _description = 'Staff Hours and Time Off by Month'
    _auto = False
    _log_access = False
    _order = 'month desc, employee_name'

    employee_id = fields.Many2one('hr.employee', string='Employee', readonly=True)
    employee_name = fields.Char('Employee', readonly=True)
    department_id = fields.Many2one('hr.department', string='Department', readonly=True)
    job_id = fields.Many2one('hr.job', string='Job', readonly=True)
    manager_id = fields.Many2one('hr.employee', string='Manager', readonly=True)
    month = fields.Date('Month', readonly=True, help='First day of the month.')
    company_id = fields.Many2one('res.company', string='Company', readonly=True)

    # --- attendance -------------------------------------------------------
    shifts = fields.Integer('Shifts', readonly=True)
    days_present = fields.Integer('Days Present', readonly=True)
    hours_worked = fields.Float('Hours Worked', readonly=True, digits=(8, 2))
    overtime_hours = fields.Float('Overtime', readonly=True, digits=(8, 2))
    late_arrivals = fields.Integer('Late Arrivals', readonly=True)
    earliest_in = fields.Float('Earliest In', readonly=True, digits=(8, 2),
                               help='Earliest clock-in of the month.')
    latest_out = fields.Float('Latest Out', readonly=True, digits=(8, 2),
                              help='Latest clock-out of the month.')
    avg_hours = fields.Float('Average Day', readonly=True, digits=(8, 2),
                             help='Average hours per shift worked that month.')

    # --- time off ---------------------------------------------------------
    annual_entitlement = fields.Float('Annual Entitlement', readonly=True, digits=(8, 2),
                                      help='Days of annual leave allocated to this '
                                           'person for the year, from their time off '
                                           'allocation.')
    annual_leave_taken = fields.Float('Annual Leave Taken', readonly=True, digits=(8, 2))
    sick_days_taken = fields.Float('Sick Days Taken', readonly=True, digits=(8, 2))
    other_leave_taken = fields.Float('Other Time Off', readonly=True, digits=(8, 2))
    short_paid_days = fields.Float('Short Paid Days', readonly=True, digits=(8, 2),
                                   help='Time off booked against a type that costs '
                                        'the employee nothing, which is what a manager '
                                        'does with the days beyond the entitlement.')
    leave_taken = fields.Float('Total Time Off', readonly=True, digits=(8, 2))
    annual_leave_cumulative = fields.Float('Annual Leave To Date', readonly=True, digits=(8, 2),
                                          help='Annual leave taken from the start of the '
                                               'year up to the end of this month.')
    leave_available = fields.Float('Leave Available', readonly=True, digits=(8, 2),
                                   help='Entitlement less annual leave already taken.')
    leave_exhausted = fields.Boolean('Leave Exhausted', readonly=True,
                                     help='No annual leave left for the year. Anything '
                                          'further has to be short paid or refused.')
    leave_booked = fields.Float('Leave Booked', readonly=True, digits=(8, 2),
                                help='Approved time off starting after this month.')
    sick_days_booked = fields.Float('Sick Days Booked', readonly=True, digits=(8, 2))
    leave_shortfall = fields.Float('Leave Shortfall', readonly=True, digits=(8, 2),
                                   help='Annual leave already approved for the rest '
                                        'of the year that this year\'s entitlement '
                                        'cannot cover. It has to be short paid, or '
                                        'refused and rebooked.')

    def init(self):
        tools.drop_view_if_exists(self.env.cr, 'cognify_staff_report')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW cognify_staff_report AS (
                WITH months AS (
                    -- Three years back for history, and a year ahead so that
                    -- leave somebody has already booked for next summer shows
                    -- up against the month it falls in.
                    SELECT DATE_TRUNC('month', d)::date AS month
                    FROM generate_series(
                        DATE_TRUNC('month', CURRENT_DATE) - INTERVAL '3 years',
                        DATE_TRUNC('month', CURRENT_DATE) + INTERVAL '12 months',
                        INTERVAL '1 month') AS d
                ),
                attendance_monthly AS (
                    SELECT
                        a.employee_id,
                        DATE_TRUNC('month', a.check_in)::date AS month,
                        COUNT(*)                                            AS shifts,
                        COUNT(DISTINCT a.check_in::date)                     AS days_present,
                        ROUND(SUM(EXTRACT(EPOCH FROM (
                            COALESCE(a.check_out, a.check_in) - a.check_in)) / 3600.0)::numeric, 2)
                                                                     AS hours_worked,
                        -- A shift is overtime once it runs past a normal
                        -- working day; eight hours is the fallback when the
                        -- employee has no working schedule to compare against.
                        -- Overtime is the part of each shift past the normal
                        -- day, summed shift by shift. Taking the whole month's
                        -- hours minus eight would call a normal week overtime.
                        ROUND((SUM(GREATEST(0,
                            EXTRACT(EPOCH FROM (
                                COALESCE(a.check_out, a.check_in) - a.check_in)) / 3600.0 - 8))
                            )::numeric, 2)                                  AS overtime_hours,
                        COUNT(*) FILTER (
                            WHERE a.check_in::time > TIME '08:30')         AS late_arrivals,
                        ROUND(MIN(EXTRACT(HOUR FROM a.check_in)::numeric
                                  + EXTRACT(MINUTE FROM a.check_in)::numeric / 60.0), 2)
                                                                     AS earliest_in,
                        ROUND(MAX(EXTRACT(HOUR FROM a.check_out)::numeric
                                  + EXTRACT(MINUTE FROM a.check_out)::numeric / 60.0), 2)
                                                                     AS latest_out
                    FROM hr_attendance a
                    WHERE a.check_in IS NOT NULL
                    GROUP BY a.employee_id, DATE_TRUNC('month', a.check_in)
                ),
                -- Time off types that hold an allocation for this person are the ones the
                -- entitlement is counted against, so that is what marks a
                -- request as annual leave. Matching on the type's name or
                -- payroll code instead would only work for a business that
                -- happens to call it "annual leave", and would quietly put
                -- every other allocated type into "other".
                allocated_types AS (
                    SELECT DISTINCT employee_id, work_entry_type_id
                    FROM hr_leave_allocation
                    WHERE state = 'validate'
                ),
                -- hr_work_entry_type.name is translated, so it is stored as
                -- jsonb keyed by language; match on the English text and on the
                -- payroll code, which never moves. Classifying once here keeps
                -- the aggregates below readable.
                --
                -- "short paid" is a time off request booked against a type that
                -- costs the employee nothing (amount_rate 0, counted as an
                -- absence). Odoo lets that through once the allocated balance is
                -- gone, which is what a manager does with the days beyond it.
                leave_classified AS (
                    SELECT
                        l.employee_id,
                        DATE_TRUNC('month', l.date_from)::date AS month,
                        l.number_of_days,
                        l.date_from::date AS start_day,
                        CASE
                            WHEN COALESCE(wt.amount_rate, 1) = 0
                              AND wt.count_as = 'absence'
                            THEN 'unpaid'
                            WHEN at.work_entry_type_id IS NOT NULL THEN 'annual'
                            WHEN wt.code IN ('LEAVE200', 'annual')
                              OR lower(COALESCE(wt.name ->> 'en_US', '')) LIKE '%annual%'
                            THEN 'annual'
                            WHEN wt.code IN ('LEAVE201', 'sick')
                              OR lower(COALESCE(wt.name ->> 'en_US', '')) LIKE '%sick%'
                            THEN 'sick'
                            ELSE 'other'
                        END AS leave_kind
                    FROM hr_leave l
                    JOIN hr_work_entry_type wt ON wt.id = l.work_entry_type_id
                    LEFT JOIN allocated_types at
                           ON at.employee_id = l.employee_id
                          AND at.work_entry_type_id = l.work_entry_type_id
                    WHERE l.state = 'validate'
                ),
                leave_monthly AS (
                    SELECT
                        employee_id,
                        month,
                        COALESCE(SUM(number_of_days) FILTER (WHERE leave_kind = 'annual'), 0)
                            AS annual_leave,
                        COALESCE(SUM(number_of_days) FILTER (WHERE leave_kind = 'sick'), 0)
                            AS sick_days,
                        COALESCE(SUM(number_of_days) FILTER (WHERE leave_kind = 'unpaid'), 0)
                            AS unpaid_days,
                        COALESCE(SUM(number_of_days) FILTER (WHERE leave_kind = 'other'), 0)
                            AS other_leave
                    FROM leave_classified
                    GROUP BY employee_id, month
                ),
                -- Running annual-leave balance. This has to be built over the full
                -- employee-by-month grid, not over the months that happen to
                -- have leave: windowing only those rows would restart the
                -- balance in any month nobody booked time off, which is most
                -- of them.
                leave_cumulative AS (
                    SELECT
                        grid.employee_id,
                        grid.month,
                        COALESCE(SUM(COALESCE(lm.annual_leave, 0)) OVER (
                            PARTITION BY grid.employee_id
                            ORDER BY grid.month
                            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW), 0)
                            AS annual_to_date
                    FROM (
                        SELECT m.month, e.id AS employee_id
                        FROM months m CROSS JOIN hr_employee e
                    ) grid
                    LEFT JOIN leave_monthly lm
                           ON lm.employee_id = grid.employee_id
                          AND lm.month = grid.month
                ),
                -- Time off that has been approved but not started yet, shown
                -- against the month it falls in.
                leave_booked AS (
                    SELECT
                        employee_id,
                        bucket AS month,
                        COALESCE(SUM(number_of_days) FILTER (WHERE leave_kind = 'annual'), 0)
                            AS annual_booked,
                        COALESCE(SUM(number_of_days) FILTER (WHERE leave_kind = 'sick'), 0)
                            AS sick_booked
                    FROM (
                        SELECT
                            lc.*,
                            (DATE_TRUNC('month', start_day)::date + INTERVAL '1 month')::date AS bucket
                        FROM leave_classified lc
                        WHERE start_day > CURRENT_DATE
                    ) future_leave
                    GROUP BY employee_id, bucket
                ),
                -- Entitlement comes from the allocations HR actually granted, not from a
                -- setting: an accrual plan hands out days over the year, and
                -- this sums what has been allocated to each person in the year
                -- the month falls in. An allocation on a plan also accrues
                -- through its milestones, which is why the days granted here
                -- are the floor rather than the whole story.
                entitlement_by_year AS (
                    SELECT
                        a.employee_id,
                        EXTRACT(YEAR FROM a.date_from)::int AS year,
                        SUM(a.number_of_days) AS days
                    FROM hr_leave_allocation a
                    WHERE a.state = 'validate'
                    GROUP BY a.employee_id, EXTRACT(YEAR FROM a.date_from)
                ),
                entitlement AS (
                    SELECT g.employee_id, g.month,
                           COALESCE((
                               SELECT eb.days
                               FROM entitlement_by_year eb
                               WHERE eb.employee_id = g.employee_id
                                 AND eb.year = EXTRACT(YEAR FROM g.month)::int
                           ), 0) AS days
                    FROM (SELECT m.month, e.id AS employee_id
                          FROM months m CROSS JOIN hr_employee e) g
                )
                SELECT
                    -- One row per employee per month, so the row id has to be
                    -- unique: the employee id repeats across every month and
                    -- reusing it makes the ORM read one month for all of them.
                    ROW_NUMBER() OVER (ORDER BY e.id, m.month) AS id,
                    e.id                                             AS employee_id,
                    e.name                                           AS employee_name,
                    -- Department and job are related to the employee's current
                    -- version record in Odoo 20, not stored on hr_employee.
                    v.department_id                                  AS department_id,
                    v.job_id                                         AS job_id,
                    e.parent_id                                      AS manager_id,
                    m.month                                          AS month,
                    e.company_id                                     AS company_id,
                    COALESCE(am.shifts, 0)                           AS shifts,
                    COALESCE(am.days_present, 0)                     AS days_present,
                    COALESCE(am.hours_worked, 0)                     AS hours_worked,
                    COALESCE(am.overtime_hours, 0)                   AS overtime_hours,
                    COALESCE(am.late_arrivals, 0)                    AS late_arrivals,
                    COALESCE(am.earliest_in, 0)                      AS earliest_in,
                    COALESCE(am.latest_out, 0)                       AS latest_out,
                    CASE WHEN COALESCE(am.shifts, 0) > 0
                         THEN ROUND((am.hours_worked / am.shifts)::numeric, 2)
                         ELSE 0 END                                   AS avg_hours,
                    ent.days                                         AS annual_entitlement,
                    COALESCE(lm.annual_leave, 0)                     AS annual_leave_taken,
                    COALESCE(lm.sick_days, 0)                        AS sick_days_taken,
                    COALESCE(lm.unpaid_days, 0)                      AS short_paid_days,
                    COALESCE(lm.other_leave, 0)                      AS other_leave_taken,
                    COALESCE(lm.annual_leave, 0) + COALESCE(lm.sick_days, 0)
                        + COALESCE(lm.other_leave, 0) + COALESCE(lm.unpaid_days, 0)
                                                                    AS leave_taken,
                    COALESCE(lr.annual_to_date, 0)                   AS annual_leave_cumulative,
                    ROUND((ent.days - COALESCE(lr.annual_to_date, 0))::numeric, 2)
                                                                    AS leave_available,
                    -- Nothing left to book against: further time off has to be
                    -- booked as short paid (unpaid), or Odoo refuses it. A year
                    -- with no allocation at all is not "exhausted", it was
                    -- never granted, and saying otherwise hides the difference
                    -- between a person out of leave and a person never given it.
                    ent.days > 0
                        AND (ent.days - COALESCE(lr.annual_to_date, 0)) <= 0
                                                                    AS leave_exhausted,
                    COALESCE(lb.annual_booked, 0)                    AS leave_booked,
                    COALESCE(lb.sick_booked, 0)                      AS sick_days_booked,
                    -- Time off already approved for the future that the rest of
                    -- this year's entitlement cannot cover. Those days are the
                    -- ones a manager will have to make short paid, and seeing it
                    -- here beats finding out when the request is refused.
                    GREATEST(
                        COALESCE(lb.annual_booked, 0)
                        - (ent.days - COALESCE(lr.annual_to_date, 0)), 0
                    )                                                      AS leave_shortfall
                FROM months m
                -- hr.employee.date_start/date_end are computed, not stored, so
                -- they cannot filter this view. Every employee is listed for
                -- every month instead: a month with no attendance and no leave
                -- simply shows zeros, which keeps leavers in the history.
                JOIN hr_employee e ON e.id IS NOT NULL
                JOIN entitlement ent
                       ON ent.employee_id = e.id AND ent.month = m.month
                LEFT JOIN hr_version v ON v.id = e.current_version_id
                LEFT JOIN attendance_monthly am
                       ON am.employee_id = e.id AND am.month = m.month
                LEFT JOIN leave_monthly lm
                       ON lm.employee_id = e.id AND lm.month = m.month
                LEFT JOIN leave_cumulative lr
                       ON lr.employee_id = e.id AND lr.month = m.month
                LEFT JOIN leave_booked lb
                       ON lb.employee_id = e.id AND lb.month = m.month
            )
        """)

    def name_get(self):
        return [(row.id, f'{row.employee_name} ({row.month:%b %Y})') for row in self]