# -*- coding: utf-8 -*-
"""Supplier stock codes and multi-supplier RFQs.

Odoo already stores one supplier pricelist line per vendor / product / pack,
and that line has a *Vendor Product Code*.  This module makes that mapping the
business object it is in a retail buying office:

* the product form lists every supplier and that supplier's own stock code for
  the item;
* one button builds one request for quotation per supplier, carrying the
  correct supplier code, description, pack, quantity and price;
* the supplier code is kept on the RFQ line and printed on the RFQ PDF.

Nothing is copied out of ``product.supplierinfo``: the pricelist remains the
single source of truth for the supplier, code, price, minimum quantity and
lead time.
"""
import logging

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def _vuma_supplier_rfq_rows(self):
        """One wizard row per supplier stock code.

        Several pricelist lines can exist for the same supplier and code
        because of quantity breaks.  They are collapsed to the lowest
        quantity break so the wizard does not create duplicate RFQ lines for
        the same code.
        """
        self.ensure_one()
        today = fields.Date.context_today(self)
        sellers = self.seller_ids.filtered(
            lambda seller: (not seller.date_start or seller.date_start <= today)
            and (not seller.date_end or seller.date_end >= today)
        ).sorted(lambda seller: (
            seller.sequence, seller.min_qty, seller.price, seller.id))
        rows = []
        seen = set()
        for seller in sellers:
            key = (
                seller.partner_id.id,
                seller.product_id.id or 0,
                (seller.product_code or '').strip().lower(),
            )
            if key in seen:
                continue
            seen.add(key)
            product = seller.product_id or self.product_variant_id
            rows.append(Command.create({
                'selected': True,
                'supplier_id': seller.partner_id.id,
                'supplierinfo_id': seller.id,
                'product_id': product.id,
                'stock_code': seller.product_code or '',
                'supplier_product_name': seller.product_name or '',
                'quantity': seller.min_qty or 1.0,
                'uom_id': (seller.uom_id or product.uom_id).id,
                'price': seller.price or 0.0,
                'lead_time': seller.delay or 0,
                'currency_id': seller.currency_id.id,
            }))
        return rows

    def action_vuma_request_supplier_quotes(self):
        """Open the multi-supplier RFQ wizard for this product."""
        self.ensure_one()
        rows = self._vuma_supplier_rfq_rows()
        if not rows:
            raise UserError(_(
                'Add at least one supplier and stock code on the '
                'Suppliers & Codes tab before requesting quotations.'))
        wizard = self.env['vuma.supplier.rfq.wizard'].create({
            'product_tmpl_id': self.id,
            'company_id': self.env.company.id,
            'line_ids': rows,
        })
        return {
            'type': 'ir.actions.act_window',
            'name': _('Request Quotations — %s', self.display_name),
            'res_model': 'vuma.supplier.rfq.wizard',
            'res_id': wizard.id,
            'view_mode': 'form',
            'target': 'new',
        }


class ProductProduct(models.Model):
    _inherit = 'product.product'

    def action_vuma_request_supplier_quotes(self):
        self.ensure_one()
        return self.product_tmpl_id.action_vuma_request_supplier_quotes()


class VumaSupplierRfqWizard(models.TransientModel):
    _name = 'vuma.supplier.rfq.wizard'
    _description = 'Request Quotations from Suppliers'

    product_tmpl_id = fields.Many2one(
        'product.template', string='Product', required=True,
        ondelete='cascade')
    company_id = fields.Many2one(
        'res.company', string='Company', required=True,
        default=lambda self: self.env.company)
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True)
    send_email = fields.Boolean(
        string='Email Each RFQ', default=False,
        help="Send the request for quotation to every selected supplier "
             "after creating it.")
    line_ids = fields.One2many(
        'vuma.supplier.rfq.wizard.line', 'wizard_id',
        string='Supplier Codes', copy=True)
    line_count = fields.Integer(compute='_compute_counts')
    selected_count = fields.Integer(compute='_compute_counts')

    @api.depends('line_ids', 'line_ids.selected')
    def _compute_counts(self):
        for wizard in self:
            wizard.line_count = len(wizard.line_ids)
            wizard.selected_count = len(wizard.line_ids.filtered('selected'))

    def action_create_rfqs(self):
        self.ensure_one()
        selected = self.line_ids.filtered('selected')
        if not selected:
            raise UserError(_('Select at least one supplier stock code.'))
        if any(line.quantity <= 0 for line in selected):
            raise UserError(_('Every selected line needs a quantity above zero.'))

        by_supplier = {}
        for line in selected:
            by_supplier.setdefault(line.supplier_id, self.env[
                'vuma.supplier.rfq.wizard.line'])
            by_supplier[line.supplier_id] |= line

        template = self.env.ref(
            'purchase.email_template_edi_purchase', raise_if_not_found=False)
        orders = self.env['purchase.order']
        for supplier, lines in by_supplier.items():
            order = self.env['purchase.order'].create({
                'partner_id': supplier.id,
                'company_id': self.company_id.id,
                'date_order': fields.Datetime.now(),
                'origin': _('Supplier codes for %s', self.product_tmpl_id.display_name),
                'order_line': [Command.create(
                    self._prepare_order_line_vals(line)) for line in lines],
            })
            orders |= order
            if self.send_email and template:
                if supplier.email:
                    try:
                        template.send_mail(order.id, force_send=True)
                        order.write({'state': 'sent'})
                    except Exception:
                        _logger.exception(
                            'Could not email RFQ %s to %s',
                            order.name, supplier.display_name)
                        order.message_post(body=_(
                            'The RFQ was created but could not be emailed. '
                            'Check the outgoing mail server.'))
                else:
                    order.message_post(body=_(
                        'RFQ was not emailed: the supplier has no email '
                        'address.'))
        return {
            'type': 'ir.actions.act_window',
            'name': _('Requests for Quotation'),
            'res_model': 'purchase.order',
            'view_mode': 'list,form',
            'domain': [('id', 'in', orders.ids)],
            'target': 'current',
        }

    def _prepare_order_line_vals(self, line):
        product = line.product_id or self.product_tmpl_id.product_variant_id
        date_planned = fields.Datetime.add(
            fields.Datetime.now(), days=line.lead_time or 0)
        return {
            'product_id': product.id,
            'name': line.supplier_product_name or product.display_name,
            'product_qty': line.quantity or 1.0,
            'uom_id': (line.uom_id or product.uom_id).id,
            'price_unit': line.price or 0.0,
            'date_planned': date_planned,
            'vuma_supplier_code': line.stock_code or '',
            'vuma_supplier_product_name': line.supplier_product_name or '',
            'vuma_supplierinfo_id': line.supplierinfo_id.id,
        }


class VumaSupplierRfqWizardLine(models.TransientModel):
    _name = 'vuma.supplier.rfq.wizard.line'
    _description = 'Supplier RFQ Wizard Line'
    _order = 'supplier_id, stock_code, id'

    wizard_id = fields.Many2one(
        'vuma.supplier.rfq.wizard', required=True, ondelete='cascade')
    selected = fields.Boolean(string='Include', default=True)
    supplier_id = fields.Many2one(
        'res.partner', string='Supplier', required=True,
        domain="[('supplier_rank', '>', 0)]")
    supplierinfo_id = fields.Many2one(
        'product.supplierinfo', string='Supplier Code Record')
    product_id = fields.Many2one('product.product', string='Variant')
    product_tmpl_id = fields.Many2one(
        'product.template', related='wizard_id.product_tmpl_id', store=False)
    stock_code = fields.Char(string='Supplier Stock Code')
    supplier_product_name = fields.Char(string='Supplier Description')
    quantity = fields.Float(string='Quantity', default=1.0, required=True)
    uom_id = fields.Many2one('uom.uom', string='Unit')
    price = fields.Float(string='Unit Price')
    lead_time = fields.Integer(string='Lead Time (days)')
    currency_id = fields.Many2one(
        'res.currency', related='wizard_id.company_id.currency_id',
        readonly=True)

    @api.constrains('quantity')
    def _check_quantity(self):
        for line in self:
            if line.quantity <= 0:
                raise ValidationError(_('Quantity must be above zero.'))


class PurchaseOrderLine(models.Model):
    _inherit = 'purchase.order.line'

    vuma_supplier_code = fields.Char(
        string='Supplier Stock Code', copy=False,
        help="The supplier's own code for this item, as captured on the "
             "supplier pricelist.")
    vuma_supplier_product_name = fields.Char(
        string='Supplier Description', copy=False,
        help="The supplier's own description for the item.")
    vuma_supplierinfo_id = fields.Many2one(
        'product.supplierinfo', string='Supplier Code Record', copy=False,
        help="The pricelist line this RFQ line was created from.")

    def _vuma_fill_supplier_code_from_pricelist(self):
        self.ensure_one()
        if not self.product_id or not self.order_id.partner_id:
            return
        seller_info = self.product_id._select_seller(
            partner_id=self.order_id.partner_id,
            quantity=self.product_qty or 1.0,
            date=fields.Date.context_today(self),
            uom_id=self.uom_id,
        ) or {}
        seller = seller_info.get('supplierinfo')
        if seller:
            self.vuma_supplier_code = seller.product_code
            self.vuma_supplier_product_name = seller.product_name
            self.vuma_supplierinfo_id = seller
        else:
            self.vuma_supplier_code = False
            self.vuma_supplier_product_name = False
            self.vuma_supplierinfo_id = False

    @api.onchange('product_id')
    def _onchange_product_vuma_supplier_code(self):
        if not self.product_id:
            self.vuma_supplier_code = False
            self.vuma_supplier_product_name = False
            self.vuma_supplierinfo_id = False
            return
        self._vuma_fill_supplier_code_from_pricelist()

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # The wizard passes the exact pricelist record, including a
            # deliberate blank code; never replace that with another choice.
            if 'vuma_supplier_code' in vals or 'vuma_supplierinfo_id' in vals:
                continue
            if not vals.get('order_id') or not vals.get('product_id'):
                continue
            order = self.env['purchase.order'].browse(vals['order_id'])
            if not order.partner_id:
                continue
            product = self.env['product.product'].browse(vals['product_id'])
            uom = self.env['uom.uom'].browse(vals['uom_id']) \
                if vals.get('uom_id') else product.uom_id
            seller_info = product._select_seller(
                partner_id=order.partner_id,
                quantity=vals.get('product_qty') or 1.0,
                date=fields.Date.context_today(self),
                uom_id=uom,
            ) or {}
            seller = seller_info.get('supplierinfo')
            if seller:
                vals['vuma_supplier_code'] = seller.product_code or ''
                vals['vuma_supplier_product_name'] = seller.product_name or ''
                vals['vuma_supplierinfo_id'] = seller.id
        return super().create(vals_list)
