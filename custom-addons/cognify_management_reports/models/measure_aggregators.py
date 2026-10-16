# -*- coding: utf-8 -*-
"""Pivot aggregation for the measures the report pivots group by.

``sale_margin`` declares ``sale.report.margin_percent`` with ``aggregator=None``
on purpose: the field is a share, and summing shares is meaningless, so the SQL
layer computes it with ``MAX`` instead. Odoo's pivot view, however, needs an
aggregator for every measure it displays, and with none declared it refuses to
render the pivot at all rather than guessing.

The reports in this module use margin as a measure on every sales pivot, so the
field gets the aggregation that matches how it is computed upstream: ``max``,
taking the highest share in the group. The other percentage measures are
weighted averages of a per-row share, so they take ``avg``.
"""
from odoo import fields, models


class SaleReportMarginAggregator(models.Model):
    _inherit = "sale.report"

    margin_percent = fields.Float(aggregator="max")


class UtilisationReportAggregators(models.Model):
    _inherit = "cognify.utilisation.report"

    timesheet_coverage = fields.Float(aggregator="avg")
    effective_rate = fields.Float(aggregator="avg")


class ExpenseReportAggregators(models.Model):
    _inherit = "cognify.expense.report"

    expense_ratio = fields.Float(aggregator="avg")


class RepAchievementAggregators(models.Model):
    _inherit = "cognify.rep.achievement"

    attainment = fields.Float(aggregator="avg")
    units_attainment = fields.Float(aggregator="avg")