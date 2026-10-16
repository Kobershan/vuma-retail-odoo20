# -*- coding: utf-8 -*-
from odoo import api, models


class StockQuant(models.Model):
    _inherit = 'stock.quant'

    def _get_removal_strategy_order(self, removal_strategy):
        """Keep FEFO sorting by expiry date when the product asks for it.

        ``product_expiry`` sorts FEFO on ``removal_date``, which is the expiry
        date pulled back by the product's ``removal_time``. For goods where "must
        be off the shelf" and "must not be consumed" are the same day — the
        common case for food and medicine — those two dates should be identical,
        which is what the product's *Pick by Closest Expiry* flag arranges via
        ``action_apply_fefo``.

        The fallback here covers quants read while no product context is
        available, so a bare FEFO sort never silently degenerates to insertion
        order.
        """
        if removal_strategy == 'fefo':
            return 'removal_date, expiration_date, in_date, id'
        return super()._get_removal_strategy_order(removal_strategy)

    @api.model
    def _get_fefo_quant_order(self, product_id):
        """Order key for picking this product's quants by closest expiry."""
        return 'removal_date, expiration_date, in_date, id'