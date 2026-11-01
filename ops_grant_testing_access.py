# -*- coding: utf-8 -*-
"""Give every current internal user full testing access.

Run with the Odoo shell:

    ./venv/bin/python odoo/odoo-bin shell -c odoo.conf -d vuma_retail \
        --no-http < ops_grant_testing_access.py

What it does
------------
* adds every non-portal group to every active internal user, so every app,
  menu, action and record rule is available to them;
* puts every user in both trading companies;
* adds the ``Testing Access (all companies)`` group, which bypasses the
  active-company capture guards in vuma_multi_tenant;
* allows every Vuma Function Access row (and seeds the missing ones, and
  sets every function's default_allowed), because since 16c013de1 the
  function table is enforced even for Settings administrators, so a
  revoked row alone can still produce "Access Denied";
* leaves portal/public users untouched.

This is deliberately a one-shot data script, not a module hook: production
databases should not silently grant everything on every upgrade.
"""

from odoo import Command

users = env["res.users"].with_context(active_test=False).search([
    ("active", "=", True),
    ("share", "=", False),
    ("login", "!=", "__system__"),
])
# ``share`` is computed from the groups, so filter the result in memory as
# well: a portal login must never be promoted to an internal user.
users = users.filtered(lambda user: not user.share)
exclusive_roles = (
    env.ref("base.group_portal", raise_if_not_found=False)
    | env.ref("base.group_public", raise_if_not_found=False)
)
groups = env["res.groups"].search([
    ("share", "=", False),
    ("id", "not in", exclusive_roles.ids),
])
companies = env["res.company"].search([])
testing_group = env.ref(
    "vuma_multi_tenant.group_vuma_testing_access", raise_if_not_found=False)

if not companies:
    raise RuntimeError("No companies found; refusing to change user access.")
if not testing_group:
    raise RuntimeError(
        "Testing group not found. Upgrade vuma_multi_tenant first.")

print(
    "Granting %s groups and %s companies to %s internal users"
    % (len(groups), len(companies), len(users))
)

for user in users:
    vals = {
        "group_ids": [Command.set(groups.ids)],
        "company_ids": [Command.set(companies.ids)],
        "vuma_mixed_basket_access": True,
    }
    if user.company_id not in companies:
        vals["company_id"] = companies[0].id
    user.write(vals)
    print(
        "  %-28s %-24s groups=%s companies=%s"
        % (user.login, user.name, len(user.group_ids), len(user.company_ids))
    )

# Second layer: the Vuma Function Access switchboard.  Missing rows already
# fall back to default_allowed, but explicit revocations are enforced for
# everyone except the built-in superuser, so every row must be allowed and
# every function's default must be True for nobody to hit "Access Denied".
functions = env["vuma.function"].sudo().with_context(
    active_test=False).search([])
functions.write({"default_allowed": True})
created = functions.sudo()._vuma_sync_access(users)
revoked = env["vuma.function.access"].sudo().search([
    ("user_id", "in", users.ids),
    ("allowed", "=", False),
])
revoked.write({"allowed": True})
print(
    "Function access: %s functions, %s rows allowed, %s rows seeded"
    % (len(functions), len(revoked), created)
)

env.cr.commit()
print("Done. Testing access granted to %s users." % len(users))
