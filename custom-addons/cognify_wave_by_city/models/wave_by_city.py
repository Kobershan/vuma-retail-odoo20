# -*- coding: utf-8 -*-
"""Group delivery waves by the customer's delivery city.

Odoo can auto-batch and auto-wave transfers grouped by contact, destination
*country*, carrier or location, but not by city. A retailer that delivers to
several towns needs the wave to split by town, so that the whole route for
Harare is picked together, then Bulawayo, then Mutare.

This adds a "City" grouping alongside the built-in ones. It plugs into the
extension hooks Odoo provides for exactly this purpose rather than editing the
wave-matching chains in ``stock.move.line``:

* ``_get_possible_pickings_domain`` / ``_get_possible_batches_domain``
  — narrow the candidates a transfer may join.
* ``_get_auto_batch_description``
  — name the wave after the city.
* ``_get_potential_existing_waves_extra_domain`` / ``_is_potential_existing_wave_extra``
* ``_get_potential_new_waves_extra_domain`` / ``_is_new_potential_line_extra``
  — the wave matching in ``stock.move.line``.

A partner with no city set is treated as its own group, so those transfers
never merge with a named city's wave and never merge with each other by
accident.
"""
from odoo import fields, models
from odoo.fields import Domain


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    batch_group_by_city = fields.Boolean(
        string="City",
        help="Automatically group batches and waves by the customer's delivery city.",
    )

    def _get_batch_group_by_keys(self):
        return super()._get_batch_group_by_keys() + ["batch_group_by_city"]

    def _get_batch_grouping_options(self):
        return super()._get_batch_grouping_options() + ["batch_group_by_city"]


class StockPicking(models.Model):
    _inherit = "stock.picking"

    def _get_possible_pickings_domain(self):
        domain = super()._get_possible_pickings_domain()
        if self.picking_type_id.batch_group_by_city:
            domain &= Domain([("partner_id.city", "=", self.partner_id.city or False)])
        return domain

    def _get_possible_batches_domain(self):
        domain = super()._get_possible_batches_domain()
        if self.picking_type_id.batch_group_by_city:
            city = self.partner_id.city or False
            domain &= Domain([("picking_ids.partner_id.city", "=", city)])
        return domain

    def _get_auto_batch_description(self):
        description = super()._get_auto_batch_description()
        if self.picking_type_id.batch_group_by_city and self.partner_id.city:
            city = self.partner_id.city
            return f"{city}, {description}" if description else city
        return description


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    def _get_potential_existing_waves_extra_domain(self, domain_list, picking_type):
        domain_list = super()._get_potential_existing_waves_extra_domain(
            domain_list, picking_type
        )
        if picking_type.batch_group_by_city:
            domain_list.append(
                Domain("picking_ids.partner_id.city", "in", self.move_id.partner_id.city.ids or [False])
            )
        return domain_list

    def _is_potential_existing_wave_extra(self, wave):
        if super()._is_potential_existing_wave_extra(wave):
            if wave.picking_type_id.batch_group_by_city:
                # Every transfer already in the wave must be for the same city.
                return set(wave.picking_ids.partner_id.city) == {
                    self.move_id.partner_id.city
                }
        return False

    def _get_potential_new_waves_extra_domain(self, domain_list, picking_type):
        domain_list = super()._get_potential_new_waves_extra_domain(
            domain_list, picking_type
        )
        if picking_type.batch_group_by_city:
            domain_list.append(
                Domain("move_id.partner_id.city", "in", self.move_id.partner_id.city.ids or [False])
            )
        return domain_list

    def _is_new_potential_line_extra(self, potential_line, picking_type):
        if super()._is_new_potential_line_extra(potential_line, picking_type):
            if picking_type.batch_group_by_city:
                return potential_line.move_id.partner_id.city == self.move_id.partner_id.city
        return False