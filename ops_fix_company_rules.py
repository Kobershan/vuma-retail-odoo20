"""Repair company record rules that lost their shared-record branch.

Many ``ir.access`` restriction rules in this database are reduced to

    [('company_id', 'in', company_ids)]

which excludes every shared record (company_id is NULL) and every record of
the other trading company.  Reading such a record raises a misleading
"doesn't have 'read' access" AccessError -- this is what broke
res.config.settings (_compute_replenish_on_order reads the shared MTO route).

The repair widens each broken rule to the pattern already used across this
database:

    ['|', '|', (path, '=', False),
          (path, 'parent_of', company_ids),
          (path, 'in', [466, 467])]

Run with:
    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --no-http < ops_fix_company_rules.py

Set DRY_RUN = False (or VR_APPLY=1) to apply.
"""
import ast
import os
import re

DRY_RUN = os.environ.get("VR_APPLY", "") != "1"
TRADE, SIYAYA = 466, 467
SENTINEL = "__COMPANY_IDS__"


def parse_domain(domain):
    return ast.literal_eval(re.sub(r"\bcompany_ids\b", repr(SENTINEL), domain))


def dump_domain(domain):
    return repr(domain).replace(repr(SENTINEL), "company_ids")


def has_shared_branch(dom):
    # Any explicit ``False`` operand means the rule already accounts for
    # shared records (company_id is NULL) in one form or another.
    return "False" in dom


def company_path(dom):
    """Field path used by the rule's `in company_ids` clause."""
    m = re.search(r"\(\s*['\"]([\w.]+)['\"]\s*,\s*['\"]in['\"]\s*,\s*company_ids\s*\)", dom)
    return m.group(1) if m else None


def main(env):
    Access = env["ir.access"].sudo()
    ModelFields = env["ir.model.fields"].sudo()

    rules = Access.search([
        ("group_id", "=", False),
        ("active", "=", True),
        ("domain", "ilike", "company_ids"),
    ], order="id")

    changed, skipped, already = [], [], 0
    for rule in rules:
        dom = rule.domain or ""
        if has_shared_branch(dom):
            already += 1
            continue
        if str(TRADE) in dom and str(SIYAYA) in dom:
            already += 1
            continue

        path = company_path(dom)
        if not path:
            skipped.append((rule.id, rule.name, rule.model_id.model, dom, "no company path"))
            continue
        root = path.split(".")[0]
        if not ModelFields.search_count([("model", "=", rule.model_id.model), ("name", "=", root)]):
            skipped.append((rule.id, rule.name, rule.model_id.model, dom, "missing field " + root))
            continue

        new_domain = (
            "['|', '|', ('%s', '=', False), ('%s', 'parent_of', company_ids), "
            "('%s', 'in', [%s, %s])]" % (path, path, path, TRADE, SIYAYA)
        )
        if not DRY_RUN:
            rule.domain = new_domain
        changed.append((rule.id, rule.name, rule.model_id.model, dom, new_domain))

    print("\nMODE:", "DRY RUN" if DRY_RUN else "APPLY")
    print("rules with company_ids:", len(rules), "| already ok:", already,
          "| to fix:", len(changed), "| skipped:", len(skipped))
    for rid, name, model, old, new in changed:
        print(f"\n[{rid}] {model} :: {name}")
        print("  old:", old)
        print("  new:", new)
    for rid, name, model, old, reason in skipped:
        print(f"\n[SKIP {rid}] {model} :: {name} ({reason})")
        print("  old:", old)

    if not DRY_RUN:
        env.cr.commit()
        print("\ncommitted", len(changed), "rule(s)")


main(env)
