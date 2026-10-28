# -*- coding: utf-8 -*-
"""Customer 360: one place for the customer's billing, delivery, contacts,
rating, groups, credit terms, discount and document history.

The new form page is deliberately built on standard Odoo data wherever that is
possible:

* contacts and delivery addresses are ordinary ``res.partner`` children, so
  they can still be selected on quotations, deliveries and the portal;
* credit terms are the normal customer payment term and shared credit limit;
* invoices, credit notes and payments are read from the accounting tables.

Only the customer groups, the account rating, the delivery instructions and
the default discount need new storage.  The history is a read-only SQL view so
the form can show invoices, credit notes and payments in one ordered list
without copying accounting data.
"""
from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain


class VumaCustomerGroup(models.Model):
    _name = 'vuma.customer.group'
    _description = 'Customer Group'
    _order = 'sequence, name'

    name = fields.Char(string='Group Name', required=True)
    code = fields.Char(string='Code', index=True)
    sequence = fields.Integer(string='Sequence', default=10)
    active = fields.Boolean(string='Active', default=True)
    discount_percent = fields.Float(
        string='Default Discount (%)', digits=(5, 2),
        help="Suggested discount for customers in this group. The discount "
             "actually applied is the one captured on the customer record.")
    description = fields.Text(string='Description')
    partner_ids = fields.Many2many(
        'res.partner', 'vuma_customer_group_partner_rel',
        'group_id', 'partner_id', string='Customers')
    partner_count = fields.Integer(
        string='Customers', compute='_compute_partner_count')

    @api.depends('partner_ids')
    def _compute_partner_count(self):
        for group in self:
            group.partner_count = len(group.partner_ids)

    @api.constrains('discount_percent')
    def _check_discount_percent(self):
        for group in self:
            if not 0.0 <= group.discount_percent <= 100.0:
                raise ValidationError(_(
                    'A customer group discount must be between 0 and 100%%.'))


class VumaCustomerHistory(models.Model):
    _name = 'vuma.customer.history'
    _description = 'Customer Document History'
    _auto = False
    _log_access = False
    _order = 'date desc, source, source_id desc'

    partner_id = fields.Many2one(
        'res.partner', string='Customer', readonly=True, index=True)
    company_id = fields.Many2one(
        'res.company', string='Company', readonly=True)
    currency_id = fields.Many2one(
        'res.currency', string='Currency', readonly=True)
    date = fields.Date(string='Date', readonly=True)
    source = fields.Selection(
        [('move', 'Accounting Document'), ('payment', 'Payment')],
        string='Source', readonly=True)
    source_id = fields.Integer(string='Source ID', readonly=True)
    document_type = fields.Selection(
        [('out_invoice', 'Invoice'), ('out_refund', 'Credit Note'),
         ('payment', 'Payment')],
        string='Document Type', readonly=True)
    name = fields.Char(string='Document', readonly=True)
    reference = fields.Char(string='Reference', readonly=True)
    due_date = fields.Date(string='Due Date', readonly=True)
    total = fields.Monetary(
        string='Total', currency_field='currency_id', readonly=True)
    residual = fields.Monetary(
        string='Outstanding', currency_field='currency_id', readonly=True)
    debit = fields.Monetary(
        string='Debit', currency_field='currency_id', readonly=True)
    credit = fields.Monetary(
        string='Credit', currency_field='currency_id', readonly=True)
    balance = fields.Monetary(
        string='Running Balance', currency_field='currency_id', readonly=True)
    state = fields.Selection(
        [('draft', 'Draft'), ('posted', 'Posted'),
         ('in_process', 'In Process'), ('paid', 'Paid'),
         ('reconciled', 'Reconciled'), ('canceled', 'Cancelled'),
         ('rejected', 'Rejected')],
        string='Status', readonly=True)

    def init(self):
        from odoo import tools
        tools.drop_view_if_exists(self.env.cr, 'vuma_customer_history')
        self.env.cr.execute("""
            CREATE OR REPLACE VIEW vuma_customer_history AS (
                WITH documents AS (
                    SELECT
                        'move'::text                              AS source,
                        am.id                                     AS source_id,
                        COALESCE(rp.commercial_partner_id,
                                 am.partner_id)                   AS partner_id,
                        am.company_id,
                        am.currency_id,
                        COALESCE(am.invoice_date, am.date)        AS date,
                        am.move_type                              AS document_type,
                        am.name,
                        am.ref                                    AS reference,
                        am.invoice_date_due                       AS due_date,
                        am.amount_total                           AS total,
                        am.amount_residual                        AS residual,
                        am.amount_total                           AS debit,
                        0.0                                       AS credit,
                        am.state
                    FROM account_move am
                    LEFT JOIN res_partner rp ON rp.id = am.partner_id
                    WHERE am.move_type IN ('out_invoice', 'out_refund')
                      AND am.state = 'posted'

                    UNION ALL

                    SELECT
                        'payment'::text,
                        ap.id,
                        COALESCE(ap.commercial_partner_id, ap.partner_id),
                        ap.company_id,
                        ap.currency_id,
                        ap.date::date,
                        'payment'::text,
                        ap.name,
                        ap.memo,
                        NULL::date,
                        ap.amount,
                        0.0,
                        0.0,
                        ap.amount,
                        ap.state
                    FROM account_payment ap
                    LEFT JOIN res_partner rp ON rp.id = ap.partner_id
                    WHERE ap.partner_type = 'customer'
                      AND ap.payment_type = 'inbound'
                      AND ap.state IN ('paid', 'reconciled')
                )
                SELECT
                    -- A stable id is essential: row_number() changes with the
                    -- query plan, so Odoo could read a different row when it
                    -- followed up a search by id. Move and payment ids are
                    -- separated by the parity bit.
                    (q.source_id * 2
                     + CASE WHEN q.source = 'payment' THEN 1 ELSE 0 END) AS id,
                    q.*,
                    SUM(q.debit - q.credit) OVER (
                        PARTITION BY q.partner_id, q.currency_id
                        ORDER BY q.date, q.source, q.source_id
                    ) AS balance
                FROM documents q
            )
        """)

    def action_open_document(self):
        """Open the invoice, credit note or payment behind a history row."""
        self.ensure_one()
        if self.source == 'payment':
            return {
                'type': 'ir.actions.act_window',
                'name': _('Payment'),
                'res_model': 'account.payment',
                'res_id': self.source_id,
                'view_mode': 'form',
                'target': 'current',
            }
        return {
            'type': 'ir.actions.act_window',
            'name': self.name or _('Document'),
            'res_model': 'account.move',
            'res_id': self.source_id,
            'view_mode': 'form',
            'target': 'current',
        }


class ResPartner(models.Model):
    _inherit = 'res.partner'

    vuma_is_customer = fields.Boolean(
        string='Is a Customer', compute='_compute_vuma_is_customer')
    vuma_account_rating = fields.Selection(
        [('a', 'A'), ('b', 'B'), ('c', 'C'), ('d', 'D')],
        string='Account Rating', default='c', tracking=False,
        help="The rating the business uses for this account. The calculated "
             "debtor rating is shown next to it for reference.")
    vuma_rating_notes = fields.Text(string='Rating Notes')
    vuma_customer_group_ids = fields.Many2many(
        'vuma.customer.group', 'vuma_customer_group_partner_rel',
        'partner_id', 'group_id', string='Customer Groups')
    vuma_discount_percent = fields.Float(
        string='Customer Discount (%)', digits=(5, 2), default=0.0,
        help="Default line discount for this customer. It is applied to new "
             "quotation and order lines; a user may still change it on a line.")
    vuma_credit_terms_note = fields.Text(
        string='Credit Terms Notes',
        help="Any agreed credit terms that are not expressed by the payment "
             "term or credit limit, for example a seasonal arrangement.")
    vuma_delivery_contact_name = fields.Char(string='Delivery Contact')
    vuma_delivery_contact_phone = fields.Char(string='Delivery Phone')
    vuma_delivery_window = fields.Char(
        string='Preferred Delivery Time', help="For example: weekdays 08:00-16:00.")
    vuma_delivery_instructions = fields.Text(string='Delivery Instructions')

    # Buildings are common in business parks and apartment blocks.  These two
    # extra address lines are printed as part of the street address and are
    # available for both the billing and the delivery address.
    vuma_building_name = fields.Char(
        string='Building / Business Park / Complex',
        help="The building, business park or apartment complex, for example "
             "Eastgate Business Park.")
    vuma_unit_number = fields.Char(
        string='Unit / Suite / Floor',
        help="The unit, suite or floor inside the building, for example "
             "Unit 12 or 3rd Floor.")

    # The primary delivery address is the first delivery child of the customer.
    # These fields expose it inline on the Customer tab so that the street
    # address is captured next to the delivery contact instead of only further
    # down in the Delivery Addresses list.  Additional addresses keep working
    # through ``vuma_delivery_address_ids``.
    vuma_delivery_building_name = fields.Char(
        string='Delivery Building / Business Park',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_unit_number = fields.Char(
        string='Delivery Unit / Suite / Floor',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_street = fields.Char(
        string='Delivery Street Address',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_street2 = fields.Char(
        string='Delivery Suburb / Street 2',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_city = fields.Char(
        string='Delivery City / Town',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_state_id = fields.Many2one(
        'res.country.state', string='Delivery Province / State',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_zip = fields.Char(
        string='Delivery Postal Code',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')
    vuma_delivery_country_id = fields.Many2one(
        'res.country', string='Delivery Country',
        compute='_compute_vuma_delivery_address',
        inverse='_inverse_vuma_delivery_address')

    vuma_contact_ids = fields.One2many(
        'res.partner', 'parent_id', string='Customer Contacts',
        domain=[('type', '=', 'contact')])
    vuma_delivery_address_ids = fields.One2many(
        'res.partner', 'parent_id', string='Delivery Addresses',
        domain=[('type', '=', 'delivery')])

    vuma_history_ids = fields.One2many(
        'vuma.customer.history', compute='_compute_vuma_history_ids',
        string='Payment History')
    vuma_invoice_count = fields.Integer(
        string='Invoices & Credit Notes', compute='_compute_vuma_document_stats')
    vuma_payment_count = fields.Integer(
        string='Payments', compute='_compute_vuma_document_stats')
    vuma_outstanding_balance = fields.Monetary(
        string='Outstanding Balance', currency_field='currency_id',
        compute='_compute_vuma_document_stats')
    vuma_last_payment_date = fields.Date(
        string='Last Payment', compute='_compute_vuma_document_stats')

    def _vuma_primary_delivery_address(self):
        """Return the delivery child shown inline in Delivery Details."""
        self.ensure_one()
        return self.env['res.partner'].search([
            ('parent_id', '=', self.id),
            ('type', '=', 'delivery'),
        ], order='id', limit=1)

    @api.depends('vuma_delivery_address_ids',
                 'vuma_delivery_address_ids.vuma_building_name',
                 'vuma_delivery_address_ids.vuma_unit_number',
                 'vuma_delivery_address_ids.street',
                 'vuma_delivery_address_ids.street2',
                 'vuma_delivery_address_ids.city',
                 'vuma_delivery_address_ids.state_id',
                 'vuma_delivery_address_ids.zip',
                 'vuma_delivery_address_ids.country_id')
    def _compute_vuma_delivery_address(self):
        for partner in self:
            address = partner._vuma_primary_delivery_address()
            partner.vuma_delivery_building_name = address.vuma_building_name
            partner.vuma_delivery_unit_number = address.vuma_unit_number
            partner.vuma_delivery_street = address.street
            partner.vuma_delivery_street2 = address.street2
            partner.vuma_delivery_city = address.city
            partner.vuma_delivery_state_id = address.state_id
            partner.vuma_delivery_zip = address.zip
            partner.vuma_delivery_country_id = address.country_id

    def _inverse_vuma_delivery_address(self):
        """Create or update the primary delivery address from the inline fields."""
        for partner in self:
            vals = {
                'vuma_building_name': partner.vuma_delivery_building_name,
                'vuma_unit_number': partner.vuma_delivery_unit_number,
                'street': partner.vuma_delivery_street,
                'street2': partner.vuma_delivery_street2,
                'city': partner.vuma_delivery_city,
                'state_id': partner.vuma_delivery_state_id.id,
                'zip': partner.vuma_delivery_zip,
                'country_id': partner.vuma_delivery_country_id.id,
            }
            if not any(vals.values()):
                # Do not create an empty address when the user clears the block
                continue
            address = partner._vuma_primary_delivery_address()
            if address:
                address.write(vals)
            elif partner.id:
                vals.update({
                    'name': partner.vuma_delivery_contact_name
                            or _('Delivery Address'),
                    'parent_id': partner.id,
                    'type': 'delivery',
                })
                self.env['res.partner'].create(vals)

    def _prepare_display_address(self, without_name=False):
        """Print the building and unit as part of the street address."""
        address_format, args = super()._prepare_display_address(without_name)
        lines = [
            self.vuma_building_name,
            self.vuma_unit_number,
            args.get('street'),
        ]
        args['street'] = ', '.join(line for line in lines if line)
        return address_format, args

    def _display_address_depends(self):
        return super()._display_address_depends() + [
            'vuma_building_name', 'vuma_unit_number']

    @api.model
    @api.readonly
    def name_search(self, name='', domain=None, operator='ilike', limit=100):
        """Only offer the population that belongs to the field being filled.

        ``res_partner_search_mode`` is already attached to the core vendor and
        customer fields (requests for quotation, quotations, invoices and
        payments).  Applying it here means a supplier can never surface in a
        debtor field, and a debtor can never surface in a vendor field, even on
        a view that does not repeat the domain.
        """
        rank_field = {
            'customer': 'customer_rank',
            'supplier': 'supplier_rank',
        }.get(self.env.context.get('res_partner_search_mode'))
        if rank_field:
            domain = Domain(rank_field, '>', 0) & Domain(domain or Domain.TRUE)
        return super().name_search(name, domain, operator, limit)

    @api.depends_context('res_partner_search_mode', 'default_customer_rank')
    @api.depends('customer_rank')
    def _compute_vuma_is_customer(self):
        search_mode = self.env.context.get('res_partner_search_mode')
        default_rank = self.env.context.get('default_customer_rank')
        for partner in self:
            partner.vuma_is_customer = bool(
                partner.customer_rank
                or search_mode == 'customer'
                or default_rank
            )

    @api.constrains('email')
    def _check_vuma_customer_email(self):
        """Email is mandatory on the customer account.

        The constraint deliberately watches only ``email``. A customer record
        created by an integration without the field at all is not blocked
        mid-import, but as soon as the email is saved (including saved empty
        from the customer form) the account must have an address.
        """
        for partner in self:
            if partner.customer_rank and not partner.parent_id \
                    and not (partner.email or '').strip():
                raise ValidationError(_(
                    'An email address is mandatory for a customer account.'))

    @api.constrains('email', 'type', 'parent_id')
    def _check_vuma_contact_email(self):
        """Every named contact captured under a customer needs an email."""
        for partner in self:
            if partner.parent_id and partner.type == 'contact' \
                    and partner.parent_id.customer_rank \
                    and not (partner.email or '').strip():
                raise ValidationError(_(
                    'An email address is mandatory for a customer contact.'))

    @api.constrains('vuma_discount_percent')
    def _check_vuma_discount(self):
        for partner in self:
            if not 0.0 <= partner.vuma_discount_percent <= 100.0:
                raise ValidationError(_(
                    'The customer discount must be between 0 and 100%%.'))

    @api.model_create_multi
    def create(self, vals_list):
        partners = super().create(vals_list)
        for partner, vals in zip(partners, vals_list):
            if 'property_payment_term_id' in vals:
                partner._vuma_share_payment_term(
                    vals['property_payment_term_id'])
        return partners

    def write(self, vals):
        res = super().write(vals)
        if 'property_payment_term_id' in vals \
                and not self.env.context.get('vuma_payment_term_sync'):
            self._vuma_share_payment_term(vals['property_payment_term_id'])
        return res

    def _vuma_share_payment_term(self, payment_term):
        """Apply one credit term to the debtor in every trading company.

        ``property_payment_term_id`` is company-dependent. Vuma manages the
        debtor once across Trade Rite and Siyaya DC, so capturing the term on
        the Customer tab must not leave the other company on a different
        default.
        """
        companies = self.env['res.company'].sudo().search([])
        for partner in self.sudo():
            for company in companies:
                if company == self.env.company:
                    continue  # already written by super()
                partner.with_company(company).with_context(
                    vuma_payment_term_sync=True,
                ).property_payment_term_id = payment_term

    def _vuma_history_partner_ids(self):
        self.ensure_one()
        commercial = self.commercial_partner_id or self
        return [commercial.id] + commercial.child_ids.ids

    @api.depends()
    def _compute_vuma_history_ids(self):
        History = self.env['vuma.customer.history']
        for partner in self:
            if not partner.id:
                partner.vuma_history_ids = False
                continue
            partner.vuma_history_ids = History.search([
                ('partner_id', '=', partner.commercial_partner_id.id),
            ])

    @api.depends()
    def _compute_vuma_document_stats(self):
        History = self.env['vuma.customer.history']
        for partner in self:
            if not partner.id:
                partner.vuma_invoice_count = 0
                partner.vuma_payment_count = 0
                partner.vuma_outstanding_balance = 0.0
                partner.vuma_last_payment_date = False
                continue
            rows = History.search([
                ('partner_id', '=', partner.commercial_partner_id.id),
            ])
            moves = rows.filtered(lambda r: r.source == 'move')
            payments = rows.filtered(
                lambda r: r.source == 'payment'
                and r.state in ('paid', 'reconciled'))
            posted = moves.filtered(lambda r: r.state == 'posted')
            partner.vuma_invoice_count = len(moves)
            partner.vuma_payment_count = len(
                rows.filtered(lambda r: r.source == 'payment'))
            partner.vuma_outstanding_balance = sum(posted.mapped('residual'))
            dates = [p.date for p in payments if p.date]
            partner.vuma_last_payment_date = max(dates) if dates else False

    def action_vuma_use_calculated_rating(self):
        for partner in self:
            if partner.vuma_debtor_rating:
                partner.vuma_account_rating = partner.vuma_debtor_rating
        return True

    def action_vuma_view_invoices(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Invoices & Credit Notes — %s', self.display_name),
            'res_model': 'account.move',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', 'in', self._vuma_history_partner_ids()),
                ('move_type', 'in', ('out_invoice', 'out_refund')),
                ('state', '!=', 'cancel'),
            ],
            'context': {'create': False, 'default_move_type': 'out_invoice'},
        }

    def action_vuma_view_payments(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Payments — %s', self.display_name),
            'res_model': 'account.payment',
            'view_mode': 'list,form',
            'domain': [
                ('partner_id', 'in', self._vuma_history_partner_ids()),
                ('partner_type', '=', 'customer'),
                ('payment_type', '=', 'inbound'),
                ('state', 'not in', ('canceled', 'rejected')),
            ],
            'context': {'create': False},
        }

    def action_vuma_view_history(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Account History — %s', self.display_name),
            'res_model': 'vuma.customer.history',
            'view_mode': 'list',
            'domain': [('partner_id', '=', self.commercial_partner_id.id)],
            'context': {'create': False, 'delete': False},
        }

    def action_vuma_view_customer_groups(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Customer Groups'),
            'res_model': 'vuma.customer.group',
            'view_mode': 'list,form',
        }


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    vuma_customer_discount = fields.Float(
        string='Customer Discount (%)',
        compute='_compute_vuma_customer_discount')

    @api.depends('partner_id')
    def _compute_vuma_customer_discount(self):
        for order in self:
            order.vuma_customer_discount = (
                order.partner_id.commercial_partner_id.vuma_discount_percent
                or 0.0)

    @api.onchange('partner_id')
    def _onchange_partner_vuma_discount(self):
        """Apply the customer's default discount to untouched lines.

        A line already carrying a discount is left alone: the salesperson may
        have negotiated something different for that line.
        """
        discount = self._vuma_default_discount()
        if not discount:
            return
        for line in self.order_line:
            if line.display_type == 'product' and not line.discount:
                line.discount = discount

    def _vuma_default_discount(self):
        self.ensure_one()
        return (
            self.partner_id.commercial_partner_id.vuma_discount_percent or 0.0)


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if 'discount' not in vals and vals.get('order_id'):
                order = self.env['sale.order'].browse(vals['order_id'])
                discount = order._vuma_default_discount()
                if discount:
                    vals['discount'] = discount
        return super().create(vals_list)

    @api.onchange('product_id')
    def _onchange_product_vuma_discount(self):
        discount = self.order_id._vuma_default_discount() \
            if self.order_id else 0.0
        if discount and not self.discount:
            self.discount = discount
