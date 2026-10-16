# -*- coding: utf-8 -*-
"""Helpers to shift a datetime by a calendar-aware period.

Odoo's stock ``alert_time``/``expiration_time`` fields are plain integers
counted in days, which cannot express "alert me three months before this
expires" without the user doing the calendar maths themselves (and getting it
wrong on 31st-of-the-month dates). These helpers do the calendar arithmetic so
a period can be stored the way a human would say it out loud.
"""
import calendar
import datetime

# Units offered in the alert window dropdown, on product templates.
EXPIRY_ALERT_UNITS = [
    ('days', 'Days'),
    ('weeks', 'Weeks'),
    ('months', 'Months'),
    ('years', 'Years'),
]

# Singular forms, so `period_description` can pluralise "1 Day" / "3 Months"
# without baking the trailing "s" into the selection labels.
_UNIT_SINGULAR = {
    'days': 'Day',
    'weeks': 'Week',
    'months': 'Month',
    'years': 'Year',
}

# How many months each non-day unit is worth. Kept as data so the mapping is
# obvious and testable rather than buried in a branch.
_UNIT_IN_MONTHS = {
    'days': None,  # not a whole-month unit, handled separately
    'weeks': None,
    'months': 1,
    'years': 12,
}


def shift_months(value, months):
    """Return ``value`` moved back/forward by ``months`` calendar months.

    Clamps to the last valid day of the target month so that
    ``shift_months(datetime(2026, 3, 31), -3)`` is ``2025-12-31`` and not an
    invalid ``2025-12-31`` -> February overflow. Handles negatives because
    Python's ``//`` floors towards minus infinity.

    :param datetime.datetime value: the date to shift
    :param int months: number of calendar months, may be negative
    :return: datetime.datetime
    """
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return value.replace(year=year, month=month, day=day)


def alert_date_from_expiration(expiration_date, value, unit):
    """Work out when an alert should fire for a given expiry date.

    The period is subtracted from ``expiration_date``. Weeks are counted in
    sevens, months and years use calendar arithmetic, and days are exact.

    :param datetime.datetime expiration_date: the lot's expiry date
    :param int value: how many units to look ahead, must be >= 0
    :param str unit: one of the keys of :data:`EXPIRY_ALERT_UNITS`
    :return: datetime.datetime or False when no alert is wanted
    """
    if not expiration_date or not value or value <= 0 or not unit:
        return False
    if unit == 'days':
        return expiration_date - datetime.timedelta(days=value)
    if unit == 'weeks':
        return expiration_date - datetime.timedelta(weeks=value)
    months = _UNIT_IN_MONTHS[unit] * value
    return shift_months(expiration_date, -months)


def period_description(value, unit):
    """Human readable rendering of an alert window, e.g. ``3 Months``."""
    if not value or value <= 0 or not unit:
        return ''
    unit_name = _UNIT_SINGULAR.get(unit, unit)
    return '%d %s%s' % (value, unit_name, '' if value == 1 else 's')