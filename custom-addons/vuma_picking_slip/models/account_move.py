# -*- coding: utf-8 -*-
"""Build a consolidated picking list from one or more customer invoices.

The invoices are the source of truth: every storable product line on them is
collected, quantities are converted to the product's base unit of measure and
then added together per product (and per source company, when the lines come
from a mixed-company basket). Nothing here creates or reserves stock. The
result is only the data the QWeb picking slip needs.
"""
from collections import OrderedDict
from types import SimpleNamespace

from odoo import Command, _, fields, models
from odoo.exceptions import UserError


def _format_quantity(quantity):
    """A stable, human-readable quantity for the printed slip.

    Odoo floats carry six decimals; the printed sheet should not show
    ``5.000000`` or scientific notation for a warehouse quantity.
    """
    text = f'{quantity:.6f}'.rstrip('0').rstrip('.')
    return text or '0'


class AccountMove(models.Model):
    _inherit = 'account.move'

    def _get_picking_slip_invoices(self):
        """The selected records that can legitimately feed a picking slip."""
        return self.filtered(
            lambda move: move.move_type == 'out_invoice'
            and move.state != 'cancel'
        )

    def get_consolidated_picking_data(self):
        """Return the invoices, consolidated lines and header data for the slip.

        A line is one product per source company. The same product sold in a
        different unit of measure is converted to its base UoM first, so the
        picker sees one quantity instead of several packaging variants.

        Raises ``UserError`` when the selection contains no customer invoice
        or no storable product, rather than printing an empty sheet.
        """
        invoices = self._get_picking_slip_invoices()
        if not invoices:
            raise UserError(_(
                'Select at least one customer invoice to create a picking slip.'))

        groups = OrderedDict()
        for line in invoices.invoice_line_ids:
            product = line.product_id
            if line.display_type != 'product' or not product:
                continue
            if not product.is_storable or line.quantity <= 0:
                continue

            invoice_uom = line.product_uom_id or product.uom_id
            quantity = invoice_uom._compute_quantity(line.quantity, product.uom_id)

            # Mixed-basket invoice lines may say which company's stock and
            # which location the goods came from. Use that when the module
            # that supplies it is installed; otherwise fall back to the
            # invoice's own company.
            source_company = getattr(line, 'vuma_source_company_id', False)
            company = source_company or line.move_id.company_id
            key = (company.id, product.id)

            group = groups.get(key)
            if group is None:
                group = groups[key] = {
                    'product': product,
                    'uom': product.uom_id,
                    'company': company,
                    'quantity': 0.0,
                    'locations': OrderedDict(),
                }
            group['quantity'] += quantity

            location = getattr(line, 'vuma_source_location_id', False)
            if location:
                location_group = group['locations'].setdefault(
                    location.id, {'location': location, 'quantity': 0.0})
                location_group['quantity'] += quantity

        if not groups:
            raise UserError(_(
                'The selected invoices contain no storable products to pick. '
                'Services, notes and zero-quantity lines are left off the '
                'picking slip.'))

        lines = []
        for group in groups.values():
            location_parts = [
                '%s (%s)' % (entry['location'].display_name,
                             _format_quantity(entry['quantity']))
                for entry in group['locations'].values()
            ]
            lines.append(SimpleNamespace(
                product=group['product'],
                uom=group['uom'],
                company=group['company'],
                quantity=group['quantity'],
                quantity_display=_format_quantity(group['quantity']),
                source_label=', '.join(location_parts),
            ))

        lines.sort(key=lambda line: (
            line.company.name or '',
            line.product.display_name or '',
        ))
        companies = self.env['res.company']
        for line in lines:
            companies |= line.company

        return SimpleNamespace(
            invoices=invoices.sorted(
                lambda invoice: (invoice.invoice_date or invoice.date, invoice.id)),
            lines=lines,
            companies=companies,
            multi_company=len(companies) > 1,
            has_barcode=any(line.product.barcode for line in lines),
            prepared_by=self.env.user.name,
            today=fields.Date.to_string(fields.Date.context_today(self)),
        )

    def action_create_consolidated_picking_slip(self):
        """Create a saved slip and open the tablet picking screen.

        The slip is a real record: the picker can put the tablet down, come
        back later, and find the same quantities still captured. Printing is a
        button on that screen and on the saved slip's form.
        """
        invoices = self._get_picking_slip_invoices()
        if not invoices:
            raise UserError(_(
                'Select at least one customer invoice to create a picking slip.'))
        slip = self.env['vuma.picking.slip'].create_from_invoices(invoices)
        return slip.action_open_tablet()

    def action_create_delivery_document(self):
        """Create a delivery document for the goods loaded onto one truck.

        The document is a snapshot-style record: it keeps the selected
        invoices and calculates their combined weight and value. The user can
        still add or remove invoices on the document form before printing it.
        """
        invoices = self._get_picking_slip_invoices()
        if not invoices:
            raise UserError(_(
                'Select at least one customer invoice to create a delivery '
                'document.'))
        document = self.env['vuma.delivery.document'].create({
            'invoice_ids': [Command.set(invoices.ids)],
        })
        return {
            'type': 'ir.actions.act_window',
            'name': document.name,
            'res_model': 'vuma.delivery.document',
            'view_mode': 'form',
            'res_id': document.id,
        }

    def action_print_consolidated_picking_slip(self):
        """Print a blank consolidated slip straight from the invoice list.

        This is the quick paper path that existed before tablets: no saved
        record is needed when somebody only wants a sheet to walk around with.
        """
        invoices = self._get_picking_slip_invoices()
        if not invoices:
            raise UserError(_(
                'Select at least one customer invoice to create a picking slip.'))
        # Run the aggregation before opening the report so an empty or
        # service-only selection produces a friendly error instead of a blank
        # PDF.
        invoices.get_consolidated_picking_data()
        report = self.env.ref(
            'vuma_picking_slip.action_report_consolidated_picking_slip')
        # ``config=False`` keeps the report itself as the action. Without it an
        # administrator whose company has no report layout yet is sent to the
        # layout configurator instead of the slip, which is not what the
        # button promises.
        return report.report_action(invoices, config=False)
