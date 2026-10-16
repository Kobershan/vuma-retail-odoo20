# -*- coding: utf-8 -*-
"""
Cross-Company Invoice Crediting Wizard.

Customer crediting across BOTH companies from one screen:
1. Pick the customer.
2. The open invoices of that partner appear from BOTH companies (m2m picker,
   each tag shows its company badge).
3. Choose full or partial credit (FIFO allocation of the cap) + a reason.
4. One draft credit note is created per referenced invoice IN ITS OWN COMPANY
   (never mixed, never re-profiled); accountant posts them separately.
"""
from odoo import api, fields, models, _
from odoo.exceptions import UserError

DISPLAY_TYPES = ('line_section', 'line_note')


class VumaPartnerCreditWizard(models.TransientModel):
    _name = 'vuma.partner.credit.wizard'
    _description = 'Credit Invoices Across Both Companies'

    partner_id = fields.Many2one(
        'res.partner', string='Customer', required=True,
        domain=[('customer_rank', '>', 0)])
    invoice_ids = fields.Many2many(
        'account.move', 'vuma_credit_wizard_invoice_rel', 'wizard_id', 'move_id',
        string='Open Invoices (both companies)',
        domain="[('move_type', '=', 'out_invoice'), ('state', '=', 'posted'), "
               "('payment_state', 'in', ['not_paid', 'partial'])]")
    full_credit = fields.Boolean(
        string='Full Credit', default=True,
        help="Credit the full remaining amount of every selected invoice.")
    amount_limit = fields.Monetary(
        string='Credit Cap (total)', currency_field='currency_id',
        help="Partial credit: allocate this total across the selected "
             "invoices oldest-first (proportional line quantities).")
    currency_id = fields.Many2one(
        'res.currency', default=lambda self: self.env.company.currency_id)
    reason = fields.Text(string='Crediting Reason', required=True)

    total_selected_due = fields.Monetary(
        string='Total Due (selected)', compute='_compute_totals',
        currency_field='currency_id')
    count_selected = fields.Integer(
        string='Invoices Selected', compute='_compute_totals')

    created_move_ids = fields.Many2many(
        'account.move', 'vuma_credit_wizard_moves_rel', 'wizard_id', 'move_id',
        string='Credit Notes Created', readonly=True)

    # ------------------------------------------------------------------
    @api.onchange('partner_id')
    def _onchange_partner_id(self):
        """Autoload the customer's open invoices from BOTH companies."""
        self.invoice_ids = [(5, 0, 0)]
        self.full_credit = True
        self.amount_limit = 0.0
        if not self.partner_id:
            return
        commercial = self.partner_id.commercial_partner_id
        partners = [self.partner_id.id] + commercial.child_ids.mapped('id') \
            if commercial else [self.partner_id.id]
        invoices = self.env['account.move'].sudo().search([
            ('partner_id', 'in', partners),
            ('move_type', '=', 'out_invoice'),
            ('state', '=', 'posted'),
            ('payment_state', 'in', ['not_paid', 'partial']),
        ], order='invoice_date asc')
        self.invoice_ids = invoices.ids
        return {'warning': _('%s open invoices found across both companies.') % len(invoices)}

    @api.depends('invoice_ids')
    def _compute_totals(self):
        for wiz in self:
            wiz.total_selected_due = sum(
                inv.amount_residual for inv in wiz.invoice_ids)
            wiz.count_selected = len(wiz.invoice_ids)

    # ------------------------------------------------------------------
    def action_credit_all_full(self):
        self.ensure_one()
        self.full_credit = True
        self.amount_limit = 0.0
        return {'type': 'ir.actions.act_window_close'}

    def action_create_credit_notes(self):
        self.ensure_one()
        Move = self.env['account.move'].sudo()
        invoices = self.invoice_ids.sorted(key=lambda i: (i.invoice_date or fields.Date.today(), i.id))
        if not invoices:
            raise UserError(_('Select at least one invoice to credit.'))

        cap = 0.0 if self.full_credit else self.amount_limit
        if not self.full_credit and cap <= 0:
            raise UserError(_('Enter the total credit amount for a partial credit.'))

        created = Move
        remaining_cap = cap
        for inv in invoices:
            due = inv.amount_residual
            ratio = 1.0 if self.full_credit else (
                1.0 if remaining_cap >= due else (remaining_cap / due if due else 0.0))
            if ratio <= 0:
                break
            if not self.full_credit:
                remaining_cap -= min(remaining_cap, due * ratio)
            ratio = min(1.0, max(0.0, ratio))
            created |= self._vuma_create_credit_note(inv, ratio)

        self.created_move_ids = created
        comps = sorted({c.name for c in created.mapped('company_id')})
        return {
            'type': 'ir.actions.act_window',
            'name': _('Draft Credit Notes — companies: %s') % (
                ' + '.join(comps) if comps else _('none')),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [('id', 'in', created.ids)],
            'target': 'current',
        }

    # ------------------------------------------------------------------
    def _vuma_create_credit_note(self, inv, ratio):
        """Create a DRAFT credit note in the invoice's OWN company."""
        self.ensure_one()
        Move = self.env['account.move'].sudo()
        company = inv.company_id
        vals = {
            'move_type': 'out_refund',
            'partner_id': inv.partner_id.id,
            'company_id': company.id,
            'currency_id': inv.currency_id.id,
            'invoice_date': fields.Date.context_today(self),
            'invoice_origin': inv.name,
            'ref': _('Cross-company credit: %s — %s') % (inv.name, (self.reason or '').strip()[:500]),
            'invoice_line_ids': self._vuma_credit_lines(inv, ratio),
        }
        credit = Move.with_company(company).create(vals)
        credit.message_post(body=_(
            'Draft credit note from invoice %s — cross-company crediting (%s%%). '
            'Reason: %s') % (inv.name, round(ratio * 100, 2), self.reason))
        return credit

    def _vuma_credit_lines(self, inv, ratio):
        """Clone invoice lines; partial credits scale quantities."""
        vals_lines = []
        for line in inv.sudo().invoice_line_ids:
            if line.display_type in DISPLAY_TYPES:
                continue
            vals_lines.append((0, 0, {
                'sequence': line.sequence,
                'name': line.name,
                'product_id': line.product_id.id,
                'product_uom_id': line.product_uom_id.id,
                'quantity': line.quantity * ratio,
                'price_unit': line.price_unit,
                'discount': line.discount,
                'tax_ids': [(6, 0, line.tax_ids.ids)],
                'account_id': line.account_id.id,
                'display_type': line.display_type,
            }))
        if not vals_lines:
            vals_lines.append((0, 0, {
                'name': _('Cross-company credit: %s') % (self.reason or _('Manual credit')),
                'quantity': 1.0,
                'price_unit': self.amount_limit or inv.amount_residual,
            }))
        return vals_lines