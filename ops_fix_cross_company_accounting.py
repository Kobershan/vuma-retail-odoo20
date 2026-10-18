"""Widen the cross-company record rules that make invoices unpreviewable.

Run it with:

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --no-http < ops_fix_cross_company_accounting.py

Why this exists
---------------
Creating an invoice from a Sale Order worked, but opening/previewing it raised
an access error. The cause is an *inconsistent* set of company record rules.

Earlier cross-company work widened five rules so a rep can work across both
trading companies whatever the company switcher has active. It added this
clause to each:

    ('company_id', 'in', [466, 467])      # Trade Rite, Siyaya DC

Only `sale.order`, `product.template`, `account.move`, `account.move.line` and
`account.payment` got it. Every other accounting model kept Odoo's stock rule:

    ['|', ('company_id', '=', False),
          ('company_id', 'parent_of', company_ids)]

`company_ids` there is `env.companies` -- the companies the *switcher* has
active, not the ones on the user. A fresh login starts with only the default
company, so `env.companies == [1]` and a company-466 record matches nothing.

The result is a half-open document: the invoice row is readable (widened rule)
while its journal and taxes are not (stock rule), which is exactly the access
error seen on preview.

Widening has to happen on the same rule, never by adding a second one: global
restrictions (``group_id`` IS NULL) are AND-ed, so an extra rule can only make
things stricter.

This widens every still-stock company rule on the accounting models, plus
``res.partner`` -- the company partner is read by the invoice form and the
portal preview, and it was being denied too. The existing five are left
untouched, so the script is idempotent.
"""
import ast
import re

from odoo import models

# Sentinel so the bare name ``company_ids`` survives a literal round-trip.
SENTINEL = "__COMPANY_IDS__"

MODELS = [
    "account.account",
    "account.analytic.account",
    "account.analytic.applicability",
    "account.analytic.distribution.model",
    "account.analytic.line",
    "account.bank.statement",
    "account.bank.statement.line",
    "account.cash.rounding",
    "account.fiscal.position",
    "account.invoice.report",
    "account.journal",
    "account.move",
    "account.move.line",
    "account.payment",
    "account.payment.term",
    "account.reconcile.model",
    "account.reconcile.model.line",
    "account.report.external.value",
    "account.tax",
    "account.tax.group",
    "account.tax.repartition.line",
    "res.partner",
]


def parse_domain(domain):
    """``company_ids`` is a bare name, so literal_eval needs it stubbed out."""
    return ast.literal_eval(re.sub(r"\bcompany_ids\b", repr(SENTINEL), domain))


def dump_domain(domain):
    return repr(domain).replace(repr(SENTINEL), "company_ids")


def trading_ids(env):
    """Trade Rite and Siyaya DC, whatever they end up being called."""
    names = ("Trade Rite", "Siyaya DC")
    found = env["res.company"].sudo().search([("name", "in", names)], order="id")
    return [c.id for c in found]


def widen(domain, ids):
    """OR a ``company_id in ids`` clause onto an existing company domain."""
    parsed = parse_domain(domain)
    # A domain list mixes prefix operators (&, |, !) with operands; only the
    # operands are counted when working out how many `|` to emit.
    operands = [
        item for item in parsed
        if not (isinstance(item, str) and item in ("&", "|", "!"))
    ]
    for operand in operands:
        if (
            isinstance(operand, tuple)
            and len(operand) == 3
            and operand[:2] == ("company_id", "in")
            and isinstance(operand[2], (list, tuple))
            and set(operand[2]) >= set(ids)
        ):
            return None  # already widened
    # Prefix notation: OR-ing n+1 operands together needs n `|`.
    return dump_domain(
        ["|"] * len(operands) + operands + [("company_id", "in", ids)]
    )


def main(env):
    ids = trading_ids(env)
    print("trading company ids:", ids)
    if len(ids) < 2:
        raise SystemExit("could not find both trading companies - nothing applied")

    Access = env["ir.access"].sudo()
    Model = env["ir.model"].sudo()

    print(f"\n{'model':<34} {'rule':>5}  {'action':<9} domain")
    print("-" * 96)
    changed = 0
    for model_name in MODELS:
        model = Model.search([("model", "=", model_name)], limit=1)
        if not model:
            print(f"{model_name:<34} {'-':>5}  {'MISSING':<9} (model not found)")
            continue
        rules = Access.search(
            [
                ("model_id", "=", model.id),
                ("group_id", "=", False),
                ("active", "=", True),
                ("domain", "ilike", "company_ids"),
            ],
            order="id",
        )
        if not rules:
            continue
        for rule in rules:
            new_domain = widen(rule.domain or "", ids)
            if new_domain is None:
                print(f"{model_name:<34} {rule.id:>5}  {'kept':<9} already widened")
                continue
            print(f"{model_name:<34} {rule.id:>5}  {'WIDEN':<9} {new_domain}")
            rule.domain = new_domain
            changed += 1

    print(f"\n{changed} rule(s) widened")
    return changed


main(env)
env.cr.commit()
print("committed")
