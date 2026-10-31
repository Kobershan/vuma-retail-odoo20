# -*- coding: utf-8 -*-
"""Keep payment capture inside the active company scope.

A payment belongs to one company. Capturing it while that company is not in
the active scope would create a record the user cannot then see or reconcile,
so the capture is refused with a message telling the user which company to
switch to. Payments posted for both companies through the Vuma cash-collection
session are created under ``with_company`` and are therefore in scope for the
company being posted.
"""
from odoo import _, api, models


class AccountPayment(models.Model):
    _inherit = 'account.payment'

    @api.model
    def _vuma_scope_companies_from_vals(self, vals):
        """Companies a payment will belong to, including its journal's one.

        A payment may be created with only ``journal_id``; Odoo then computes
        ``company_id`` from the journal. The scope check must follow that
        implicit company too, otherwise an RPC could take a Siyaya DC payment
        while Trade Rite is the active scope.
        """
        companies = self.env['res.company']
        if vals.get('company_id'):
            companies |= self.env['res.company'].browse(vals['company_id'])
        if vals.get('journal_id'):
            companies |= self.env['account.journal'].browse(
                vals['journal_id']).company_id
        return companies

    @api.model_create_multi
    def create(self, vals_list):
        companies = self.env['res.company']
        for vals in vals_list:
            companies |= self._vuma_scope_companies_from_vals(vals)
        if companies:
            self.env.user._vuma_require_companies(
                companies,
                _('capture this payment'),
            )
        return super().create(vals_list)

    def write(self, vals):
        if vals.get('company_id'):
            self.env.user._vuma_require_company(
                self.env['res.company'].browse(vals['company_id']),
                _('move this payment to another company'),
            )
        return super().write(vals)

    def action_post(self):
        self.env.user._vuma_require_companies(
            self.mapped('company_id'),
            _('post this payment'),
        )
        return super().action_post()
