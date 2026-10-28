# -*- coding: utf-8 -*-
"""Report values and print bookkeeping for product labels."""
from odoo import api, fields, models


class ReportVumaProductLabel(models.AbstractModel):
    _name = 'report.vuma_retail_ops.report_vuma_product_label'
    _description = 'Product Label Report'

    @api.model
    def _get_report_values(self, docids, data=None):
        labels = self.env['vuma.product.label'].browse(docids)
        now = fields.Datetime.now()
        for label in labels:
            label.write({
                'last_printed': now,
                'print_count': (label.print_count or 0) + 1,
            })
        return {
            'docids': docids,
            'docs': labels,
            'data': data or {},
        }
