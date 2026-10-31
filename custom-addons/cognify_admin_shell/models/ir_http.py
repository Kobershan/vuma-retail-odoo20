# -*- coding: utf-8 -*-
"""Expose company colours through the web session.

The Odoo user service already ships every allowed company to the browser in
``session_info``. Adding the colour there means the shell can paint company
colours on first render without another RPC, and both company switchers (the
standard Odoo one and the Vuma one) receive the same value.
"""

from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def _vuma_add_company_colours(self, session_info):
        user_companies = (session_info or {}).get("user_companies") or {}
        allowed_companies = user_companies.get("allowed_companies") or {}
        if allowed_companies:
            companies = self.env["res.company"].sudo().browse(list(allowed_companies))
            for company in companies:
                company_info = allowed_companies.get(company.id)
                if company_info is not None:
                    company_info["vuma_background_color"] = (
                        company.vuma_background_color or False
                    )
        return session_info

    def session_info(self):
        return self._vuma_add_company_colours(super().session_info())
