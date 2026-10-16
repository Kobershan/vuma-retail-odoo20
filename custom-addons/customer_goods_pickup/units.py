"""Unit-of-measure helpers that fit the installed Odoo version.

Odoo 20 dropped ``uom.uom.category_id``: a unit's family is its own hierarchy
through ``relative_uom_id``, so "Dozens" and "Units" are the same family while
"kg" and "Units" are not. ``_compute_quantity`` still converts and still
raises when it cannot, so these helpers lean on it rather than re-deriving
factors.

Every quantity test below is made at the **Product Unit** precision the shop
itself configured. ``odoo.tools.float_is_zero``/``float_compare`` accept either
a digit count or a rounding step, and that digit count is a *decimal* count:
``precision_digits=0`` means an epsilon of ``1``, not the configured scale. It
would make 0.4 "zero" and 1.4 equal to 1.45, so the helpers take the
environment and ask for the rounding step instead.
"""

from odoo.tools import float_compare, float_is_zero


def unit_rounding(env):
    """The rounding step the shop uses for product quantities."""
    return 10 ** -env['decimal.precision'].precision_get('Product Unit')


def root_uom(uom):
    """Walk a unit up its ``relative_uom_id`` chain to the family root."""
    seen, current = {uom.id}, uom
    while current.relative_uom_id and current.relative_uom_id.id not in seen:
        current = current.relative_uom_id
        seen.add(current.id)
    return current


def are_compatible(uom_a, uom_b):
    """Whether a quantity can sensibly be converted between two units."""
    if not uom_a or not uom_b or uom_a == uom_b:
        return True
    return root_uom(uom_a) == root_uom(uom_b)


def convert(qty, from_uom, to_uom, round_result=False):
    """Convert a quantity, returning it unchanged when that is not possible."""
    if not from_uom or not to_uom or from_uom == to_uom:
        return qty or 0.0
    converted = from_uom._compute_quantity(
        qty or 0.0, to_uom, round=round_result, raise_if_failure=False)
    # ``raise_if_failure=False`` quietly returns the input on a mismatch, so a
    # conversion that changed the number without sharing a family is treated
    # as a failure rather than silently accepted.
    if not are_compatible(from_uom, to_uom):
        return qty or 0.0
    return converted


def is_zero(env, qty):
    """Whether a quantity is zero *at the Product Unit precision*.

    The precision is taken from the environment rather than hard-coded: see the
    module docstring for why ``precision_digits=0`` is not the same thing.
    """
    return float_is_zero(qty, precision_rounding=unit_rounding(env))


def compare(env, qty_a, qty_b):
    return float_compare(qty_a, qty_b, precision_rounding=unit_rounding(env))


def greater(env, qty_a, qty_b):
    return compare(env, qty_a, qty_b) > 0