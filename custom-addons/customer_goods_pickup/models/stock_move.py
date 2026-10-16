"""Keep refused customer goods inside the returns area.

Why a guard is needed at all
----------------------------
Choosing a location outside the warehouse tree is what makes quarantine stock
invisible to availability, but it is not on its own a *control*: nothing stops
a determined user from pointing a picking at the quarantine location, or from
editing an existing move's destination. Without this file a store clerk could
write a stock move straight from quarantine into ``WH/Stock`` and put goods the
store had refused back on the shop floor.

So every write that would put a move into, or take one out of, the returns area
is checked here. Two callers are allowed through:

* ``self.env.su`` — the module's own disposition actions, which run their
  transfers through ``sudo``. ``su`` is an attribute of the environment object,
  not a context key, so an XML-RPC or JSON-RPC caller cannot set it; this is the
  same thing core Odoo relies on.
* a goods pickup **manager**, who is explicitly allowed to move refused goods by
  hand when no modelled disposition fits.

Everybody else is refused, whichever route they arrive by — the UI, a server
action, a data import, a script or a direct ORM call — because they all end up
here.
"""

import logging

from odoo import _, api, models
from odoo.exceptions import AccessError

from ..access import MANAGER

_logger = logging.getLogger(__name__)

class StockMove(models.Model):
    """Refuse hand-made stock movements through the returns area."""

    _inherit = 'stock.move'

    # ------------------------------------------------------------- internals
    @api.model
    def _returns_area_locations(self):
        """Every location the customer-returns structure covers.

        Deliberately across *all* companies rather than the caller's: refusing
        is a safety rule, so a user must not be able to step around it by
        naming a different company.
        """
        return self.env['stock.location'].get_goods_return_locations(company=None)

    @api.model
    def _check_goods_return_guard(self, location_ids, side):
        """Raise unless the caller may move goods through the returns area."""
        if self.env.su:
            return True
        ids = {int(value) for value in location_ids or [] if value}
        if not ids:
            return True
        if self.env.user.has_group(MANAGER):
            return True
        area = self._returns_area_locations()
        if not area:
            return True
        locations = self.env['stock.location'].browse(ids).exists()
        if not locations:
            return True
        # Either end of the move sitting inside the area, or being an ancestor
        # of it (a move *out of* the whole area), is the same concern.
        blocked = locations & area
        if not blocked:
            blocked = area.filtered(
                lambda ret: any(ret._child_of(location) for location in locations))
        if blocked:
            raise AccessError('%s (%s)' % (
                _(
                    'Quarantined customer returns can only be moved by a goods pickup manager, '
                    'or by the disposition workflow in Customer Goods. Goods the store refused '
                    'must not be released by hand: use the Quarantine screen, so that the '
                    'transfer, the approver and the reason are all recorded.'), ', '.join(sorted(
                    blocked.mapped('complete_name') + locations.mapped(
                        'complete_name')))))
        return True

    @api.model
    def _location_ids(self, vals, field):
        """Resolve a location id from a create ``vals_list`` or a ``write`` dict.

        ``create`` receives a list of dicts and ``write`` a single dict, and in
        both cases the field is an id rather than a recordset, so the guard
        normalises both into a set of ids before asking the location model.
        """
        entries = vals if isinstance(vals, list) else [vals]
        ids = set()
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            value = entry.get(field)
            if isinstance(value, (list, tuple)):
                ids.update(int(item) for item in value if item)
            elif value:
                ids.add(int(value))
        return ids

    # ---------------------------------------------------------------- writes
    @api.model_create_multi
    def create(self, vals_list):
        self._check_goods_return_guard(
            self._location_ids(vals_list, 'location_id'), 'source')
        self._check_goods_return_guard(
            self._location_ids(vals_list, 'location_dest_id'), 'destination')
        return super().create(vals_list)

    def write(self, vals):
        if 'location_id' in vals or 'location_dest_id' in vals:
            # Both ends of the move are considered, not only the one being
            # written: re-pointing the destination of a move whose source is
            # quarantine is exactly the same release as writing the source.
            ids = set()
            if 'location_id' in vals:
                ids |= self._location_ids(vals, 'location_id')
            else:
                ids.update(self.mapped('location_id').ids)
            if 'location_dest_id' in vals:
                ids |= self._location_ids(vals, 'location_dest_id')
            else:
                ids.update(self.mapped('location_dest_id').ids)
            self._check_goods_return_guard(ids, 'either end')
        return super().write(vals)