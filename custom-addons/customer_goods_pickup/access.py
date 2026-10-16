"""Server-side permission checks for the goods pickup workflow.

The access rules in ``security/ir.access.csv`` decide who can *see* and
*touch* a record. These helpers decide who may run the individual actions,
because several of them legitimately span groups: the store writes the
receipt but must not approve the credit, the accountant approves the credit
but must not touch the count, and only a manager reverses stock.
"""

from odoo import _
from odoo.exceptions import AccessError

REP = 'customer_goods_pickup.group_goods_pickup_sales_rep'
STORE = 'customer_goods_pickup.group_goods_pickup_store_receiver'
ACCOUNTANT = 'customer_goods_pickup.group_goods_pickup_accountant'
MANAGER = 'customer_goods_pickup.group_goods_pickup_manager'


def require_group(env, groups, message):
    """Raise :class:`AccessError` unless the user holds one of ``groups``.

    ``env.su`` is honoured so that cron jobs, tests and server actions run with
    elevated rights still work.
    """
    if env.su:
        return True
    if isinstance(groups, str):
        groups = (groups,)
    if any(env.user.has_group(group) for group in groups):
        return True
    raise AccessError(message or _(
        'Your user is not allowed to perform this operation.'))