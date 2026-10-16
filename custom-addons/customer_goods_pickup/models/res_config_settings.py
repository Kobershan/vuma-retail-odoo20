from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class ResConfigSettings(models.TransientModel):
    """Where refused customer goods are physically held.

    Left empty, the module maintains the structure itself: a shared virtual
    ``Customer Returns`` root with a company-owned ``Quarantine`` location and
    an ``Awaiting Customer`` staging location underneath it, created the first
    time they are needed and reused on every upgrade.

    Setting the quarantine location by hand is for shops that already have a
    returns area of their own. It is validated on save, because a location
    inside a warehouse would put refused goods back into sellable stock — the
    one outcome this whole design exists to prevent.
    """

    _inherit = 'res.config.settings'

    cognify_goods_quarantine_location_id = fields.Many2one(
        'stock.location', string='Quarantine Location', check_company=True,
        help='Where goods the store refused are physically received. It must be '
             'an internal location outside every warehouse: stock inside a '
             'warehouse counts towards the quantity on hand and can be '
             'reserved for a customer order.',
    )
    cognify_goods_quarantine_location_state = fields.Char(
        string='Quarantine Area', compute='_compute_quarantine_state',
        help='Where refused goods are actually held right now.',
    )
    cognify_goods_pickup_notify_rep = fields.Boolean(
        string='Notify The Sales Representative', default=True,
        help='Post a message on the pickup, mentioning the representative, '
             'every time the store finishes receiving it.',
    )

    # --------------------------------------------------------------- compute
    @api.depends_context('company')
    def _compute_quarantine_state(self):
        """Show the real structure, not just what is configured.

        An administrator opening this screen needs to see where the goods
        physically go, and the answer is usually "the module's own location",
        which is created lazily and therefore not in the configuration
        parameter at all.
        """
        Location = self.env['stock.location']
        for settings in self:
            configured = settings.cognify_goods_quarantine_location_id
            quarantine = configured or Location._get_quarantine_location(
                settings.company_id or self.env.company, create=False)
            awaiting = Location._get_awaiting_customer_location(
                settings.company_id or self.env.company, create=False)
            parts = []
            if quarantine:
                parts.append(_('Quarantine: %s', quarantine.complete_name))
            if awaiting:
                parts.append(_('Awaiting customer: %s', awaiting.complete_name))
            settings.cognify_goods_quarantine_location_state = ' — '.join(parts)

    # ----------------------------------------------------------------- write
    def set_values(self):
        res = super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        # Odoo 20 dropped ``set_param``: the typed setters are ``set_bool``,
        # ``set_int``, ``set_float`` and ``set_str``. Calling the removed
        # method made every *save* of the settings form raise an
        # ``AttributeError``, which took the whole form down with it.
        location = self.cognify_goods_quarantine_location_id
        if location:
            location._check_goods_return_location_suitability()
        params.set_int('customer_goods_pickup.quarantine_location_id',
                       location.id or 0)
        params.set_bool('customer_goods_pickup.notify_sales_rep',
                        self.cognify_goods_pickup_notify_rep)
        return res

    @api.model
    def get_values(self):
        res = super().get_values()
        params = self.env['ir.config_parameter'].sudo()
        res.update({
            'cognify_goods_quarantine_location_id':
                params.get_int('customer_goods_pickup.quarantine_location_id') or False,
            'cognify_goods_pickup_notify_rep': params.get_bool(
                'customer_goods_pickup.notify_sales_rep', True),
        })
        return res

    # ---------------------------------------------------------------- button
    def action_open_quarantine_setup(self):
        """Make the structure now, so an administrator can inspect it."""
        self.ensure_one()
        quarantine = self.env['stock.location']._get_quarantine_location(
            self.company_id or self.env.company)
        return quarantine.action_open_goods_return_locations()