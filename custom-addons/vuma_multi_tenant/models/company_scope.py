# -*- coding: utf-8 -*-
"""Company-scope helpers shared by every Vuma cross-company feature.

Odoo's company switcher stores the companies the user is currently working in
as ``allowed_company_ids`` in the request context. Vuma uses exactly that list
as its security boundary:

* with only Trade Rite active, a Trade Rite document may be captured and a
  Siyaya DC document may not;
* with only Siyaya DC active, the reverse;
* with both trading companies active, either company may be captured in and
  the genuinely cross-company flows (mixed basket, cross-company cash
  collection and crediting) are allowed;
* with every company active, every company the user may see is in scope.

The helpers raise a plain ``UserError`` with the scope that is active and the
one the user must switch to, so the message is actionable instead of a raw
company-crossover traceback.
"""
from odoo import _, models
from odoo.exceptions import UserError

TRADE_RITE = "Trade Rite"
SIYAYA_DC = "Siyaya DC"
TRADING_COMPANY_NAMES = (TRADE_RITE, SIYAYA_DC)


class ResUsers(models.Model):
    _inherit = "res.users"

    # ------------------------------------------------------------------
    # Scope inspection
    # ------------------------------------------------------------------
    def _vuma_trading_companies(self):
        """The two trading companies, always in (Trade Rite, Siyaya DC) order."""
        self.ensure_one()
        companies = self.env["res.company"].sudo().search([
            ("name", "in", list(TRADING_COMPANY_NAMES)),
        ])
        return (
            companies.filtered(lambda c: c.name == TRADE_RITE)[:1]
            | companies.filtered(lambda c: c.name == SIYAYA_DC)[:1]
        )

    def _vuma_active_trading_companies(self):
        """The trading companies in the active company scope."""
        self.ensure_one()
        return self._vuma_trading_companies() & self.env.companies

    def _vuma_has_both_trading_companies(self):
        """True when both Trade Rite and Siyaya DC are in the active scope."""
        self.ensure_one()
        trading = self._vuma_trading_companies()
        return bool(trading) and trading <= self.env.companies

    def _vuma_scope_names(self):
        self.ensure_one()
        return ", ".join(self.env.companies.mapped("name")) or _("no company")

    def _vuma_testing_access(self):
        """Testing group members may work across companies without switching."""
        return self.has_group('vuma_multi_tenant.group_vuma_testing_access')

    # ------------------------------------------------------------------
    # Scope enforcement
    # ------------------------------------------------------------------
    def _vuma_raise_scope_error(self, companies, action):
        """Refuse ``action`` unless every company in ``companies`` is active."""
        self.ensure_one()
        if self._vuma_testing_access():
            return
        companies = companies.exists()
        if not companies:
            return

        trading = self._vuma_trading_companies()
        selected_trading = companies & trading
        missing = companies - self.env.companies
        if not missing:
            return

        scope = self._vuma_scope_names()
        if len(selected_trading) > 1:
            raise UserError(_(
                "You are working in %(scope)s. Switch the company selector to "
                "'Trade Rite + Siyaya DC' (both companies) before you %(action)s.",
                scope=scope,
                action=action,
            ))
        raise UserError(_(
            "You are working in %(scope)s. Switch to %(company)s before you "
            "%(action)s.",
            scope=scope,
            company=missing[0].display_name,
            action=action,
        ))

    def _vuma_require_company(self, company, action):
        """Require one company to be inside the active scope."""
        self.ensure_one()
        self._vuma_raise_scope_error(company, action)

    def _vuma_require_companies(self, companies, action):
        """Require every company in ``companies`` to be inside the active scope."""
        self.ensure_one()
        self._vuma_raise_scope_error(companies, action)

    def _vuma_require_both_trading_companies(self, action):
        """Require Trade Rite *and* Siyaya DC, for cross-company captures."""
        self.ensure_one()
        if self._vuma_testing_access():
            return
        trading = self._vuma_trading_companies()
        if not trading:
            raise UserError(_(
                "Trade Rite and Siyaya DC must both exist before you %(action)s.",
                action=action,
            ))
        self._vuma_raise_scope_error(trading, action)
