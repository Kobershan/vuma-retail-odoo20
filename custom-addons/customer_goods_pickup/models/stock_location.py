"""Where refused customer goods physically live, and how that is proved.

Why this file exists
--------------------
Rejected goods used to be a *label*: the receipt carried the name of a location
and nothing else, so nothing in Odoo could answer "is the stock actually there?".
Custody has to be real, so the module now books refused goods through a genuine
validated ``stock.move`` into a real internal location. This file is the part
that decides *which* location, and the part that stops anybody quietly turning
quarantine stock back into sellable stock.

The measurement that drove the choice
-------------------------------------
``product.product._get_domain_locations`` collects the ``view_location_id`` of
every warehouse of the active companies and hands the **whole recursive
descendant set** of those nodes to the quant domain. ``stock.quant._gather``
(non-strict) does the same with ``child_of`` against the source location of a
move. Neither filters on ``usage``. So, measured against the installed Odoo 20
source with a real quant of 6 units in each candidate and 4 units of genuinely
sellable stock in ``WH/Stock``:

========================================  ================  ==================
location                                  qty_available     reservable
========================================  ================  ==================
``WH/Stock/Quarantine``                   **10** (of 4)    **yes, all 6**
``WH/Quarantine`` (under the view node)   **10** (of 4)    no
``Customer Returns/Quarantine``           4 (correct)       no
========================================  ================  ==================

The first row is the trap this design exists to avoid and the second is only
half a fix. The third is the only one that keeps a genuine physical location
*and* keeps the goods off the shop floor. Hence the shared, warehouse-less
``Customer Returns`` root declared in ``data/stock_location.xml``.

Valuation
---------
``stock_account`` decides what counts towards stock value with
``stock.location._should_be_valued()`` — "has a company, and is internal or
transit" — *not* with the warehouse tree. So a company-owned internal quarantine
location keeps refused goods on the balance sheet at the price they were
received for, while staying invisible to the shop floor. Scrapping them posts
the ordinary inventory-loss entry; handing them back to the customer relieves
the value. Nothing here fabricates a valuation.
"""

import logging

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

_logger = logging.getLogger(__name__)

#: ``complete_name`` of the shared virtual root, used only as a safety net when
#: the module data has not been loaded (a partially migrated database).
RETURNS_ROOT_NAME = 'Customer Returns'

#: The child locations the module maintains, per company.
QUARANTINE = 'Quarantine'
AWAITING_CUSTOMER = 'Awaiting Customer'


class StockLocation(models.Model):
    """Resolve, create and police the customer-returns location structure."""

    _inherit = 'stock.location'

    # ------------------------------------------------------------- resolving
    @api.model
    def _returns_root(self):
        """The shared virtual location every quarantine location hangs under."""
        root = self.env.ref(
            'customer_goods_pickup.stock_location_customer_returns',
            raise_if_not_found=False)
        if root and root.exists():
            return root
        # The module data has not been loaded (a partial install, or a database
        # whose data file was skipped). Rebuild it rather than fall back to a
        # sellable location: a silent fallback to warehouse stock is exactly
        # the failure this design exists to prevent.
        return self.sudo().create({
            'name': RETURNS_ROOT_NAME,
            'usage': 'view',
            'company_id': False,
        })

    @api.model
    def _get_goods_return_location(self, keyword, company, create=True):
        """Return (and by default create) the company location for ``keyword``.

        Idempotent on purpose: the lookup is by parent and name, so upgrading a
        database that already has the location reuses it instead of creating a
        second one, and two receipts validated in the same minute share it.
        """
        Location = self.env['stock.location']
        root = self._returns_root()
        domain = [('location_id', '=', root.id), ('name', '=', keyword)]
        company = company or self.env.company
        existing = Location.sudo().search(
            domain + [('company_id', '=', company.id)], limit=1)
        if existing:
            return existing
        shared = Location.sudo().search(
            domain + [('company_id', '=', False)], limit=1)
        if shared:
            return shared
        if not create:
            return Location
        return Location.sudo().create({
            'name': keyword,
            'usage': 'internal',
            'location_id': root.id,
            'company_id': company.id,
            # Replenishment is the suggestion that this location feeds the
            # warehouse. Goods awaiting an inspection decision must never be
            # proposed as a supply of sellable stock.
            'replenish_location': False,
        })

    @api.model
    def _get_quarantine_location(self, company=None, create=True):
        """The location refused goods are received into."""
        return self._get_goods_return_location(
            QUARANTINE, company or self.env.company, create=create)

    @api.model
    def _get_awaiting_customer_location(self, company=None, create=True):
        """Where refused goods sit while the customer collects them."""
        return self._get_goods_return_location(
            AWAITING_CUSTOMER, company or self.env.company, create=create)

    @api.model
    def get_goods_return_locations(self, company=None):
        """Every location this module treats as part of the returns structure.

        Pass ``company`` for one company's structure, or ``None`` for all of
        them — the stock-move guard uses ``None``, because refusing to move
        refused goods is a safety rule and must not be avoidable by naming a
        different company.

        Includes the children of each location, so a sub-location a shop carves
        out later is covered by exactly the same rules as its parent.
        """
        domain = [('id', 'child_of', self._returns_root().id)]
        if company is not None:
            company = company or self.env.company
            roots = (self._get_quarantine_location(company, create=False)
                     | self._get_awaiting_customer_location(company, create=False))
            if not roots:
                return self.browse()
            domain = [('id', 'child_of', roots.ids)]
        return self.sudo().search(domain)

    # -------------------------------------------------------------- policing
    def _is_goods_return_location(self):
        """Whether this record is (or sits under) the returns structure."""
        self.ensure_one()
        if not self:
            return False
        root = self.env.ref(
            'customer_goods_pickup.stock_location_customer_returns',
            raise_if_not_found=False)
        if root and root.exists():
            return self._child_of(root)
        return (self.complete_name or '').startswith(RETURNS_ROOT_NAME)

    def _check_goods_return_location_suitability(self):
        """Refuse a location that would put refused goods back on the shop floor.

        Called from the settings screen before a location is accepted, and from
        the quarantine custody records whenever their physical location changes,
        so a misconfiguration is reported instead of quietly producing stock the
        shop can sell.

        The dangerous case is a location inside a warehouse: stock there counts
        towards the quantity on hand and can be reserved for a customer order.
        Handover destinations (customer locations) and disposal destinations
        (inventory loss) are equally legitimate, because by then the goods are
        no longer held by the store at all.
        """
        self.ensure_one()
        if not self:
            return True
        if self.usage not in ('internal', 'transit', 'customer', 'inventory'):
            raise ValidationError(
                _('%(location)s is not a stock location, so it cannot hold '
                  'customer returns.',
                  location=self.display_name))
        if self.warehouse_id:
            raise ValidationError(
                _('%(location)s sits inside warehouse %(warehouse)s. Goods in a '
                  'warehouse count towards the shop floor quantity on hand and '
                  'can be reserved for a customer order, which is exactly what '
                  'refused customer goods must never be. Use a location outside '
                  'the warehouse, such as "%(root)s / %(name)s".',
                  location=self.display_name,
                  warehouse=self.warehouse_id.display_name,
                  root=RETURNS_ROOT_NAME, name=self.name))
        if self.usage == 'internal' and not self.company_id:
            raise ValidationError(
                _('%(location)s is shared between companies. Give the location a '
                  'company so the stock in it is valued in the right books.',
                  location=self.display_name))
        return True

    # ---------------------------------------------------------------- fields
    cognify_goods_return = fields.Boolean(
        string='Customer Returns Area',
        compute='_compute_cognify_goods_return',
        help='This location, or one of its parents, holds goods the store '
             'refused. Stock here is real and valued, but it sits outside every '
             'warehouse, so it can never be sold or reserved by accident.',
    )

    @api.depends_context('company')
    def _compute_cognify_goods_return(self):
        for location in self:
            location.cognify_goods_return = location._is_goods_return_location()

    def action_open_goods_return_locations(self):
        self.ensure_one()
        locations = self.get_goods_return_locations(
            self.company_id or self.env.company)
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Returns Locations'),
            'res_model': 'stock.location',
            'view_mode': 'list,form',
            'domain': [('id', 'in', locations.ids)],
            'context': {'create': False},
        }