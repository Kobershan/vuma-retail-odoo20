from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import float_is_zero

# Amount precision helper — comparisons must not use plain float equality.
AMOUNT_PRECISION_DIGITS = 2


class CognifyRemittance(models.Model):
    """One field trip by one rep.

    A rep collects cash from any number of customers in a day, so the money
    taken in is a set of *receipt lines* (customer + amount), not a single
    customer field. Trip expenses are recorded against the remittance as a
    whole. Nothing reaches the general ledger until the office verifies it and
    credits each customer's account.
    """

    _name = 'cognify.remittance'
    _inherit = ['mail.thread']
    _description = 'Cognify Field Remittance'
    _order = 'date desc, id desc'
    _rec_names_search = ['name', 'customer_ids', 'rep_id']

    name = fields.Char(
        string='Reference',
        required=True,
        copy=False,
        default=lambda self: _('New'),
    )

    # ----------------------------------------------------------------- who/when
    rep_id = fields.Many2one(
        'res.users',
        string='Sales Rep',
        required=True,
        default=lambda self: self.env.user,
        index=True,
        help='The field rep who collected the money.',
    )
    date = fields.Date(
        string='Date',
        required=True,
        default=fields.Date.context_today,
        help='Date the cash was collected from the customer(s).',
    )
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(
        'res.currency', related='company_id.currency_id', readonly=True,
    )

    # ------------------------------------------------------------ cash in
    receipt_ids = fields.One2many(
        'cognify.remittance.receipt',
        'remittance_id',
        string='Cash Collected',
        copy=True,
        help='One line per customer the rep collected cash from.',
    )
    customer_ids = fields.Many2many(
        'res.partner',
        string='Customers',
        compute='_compute_customer_ids',
        store=True,
        help='Every customer paid on this remittance. Used to search and group.',
    )
    total_received = fields.Monetary(
        string='Total Collected',
        compute='_compute_totals',
        store=True,
        help='Sum of the cash collected from all customers on this trip.',
    )

    # --------------------------------------------------------------- expenses
    line_ids = fields.One2many(
        'cognify.remittance.line',
        'remittance_id',
        string='Expenses',
        copy=True,
    )

    total_expenses = fields.Monetary(
        string='Total Expenses',
        compute='_compute_totals',
        store=True,
    )
    net_amount = fields.Monetary(
        string='Net to Return',
        compute='_compute_totals',
        store=True,
        help='Amount the rep must hand back to the office: '
             'total collected minus total expenses.',
    )

    notes = fields.Text(
        string='Notes',
        help='Anything the office should know when verifying this remittance.',
    )

    # ------------------------------------------------------------- workflow
    state = fields.Selection(
        selection=[
            ('draft', 'Draft'),
            ('submitted', 'Submitted'),
            ('verified', 'Verified'),
            ('credited', 'Credited'),
            ('rejected', 'Rejected'),
        ],
        default='draft',
        required=True,
        copy=False,
        tracking=True,
        index=True,
    )

    submitted_date = fields.Datetime(string='Submitted', readonly=True, copy=False)
    verified_by_id = fields.Many2one('res.users', string='Verified By', readonly=True, copy=False)
    verified_date = fields.Datetime(string='Verified', readonly=True, copy=False)
    rejection_reason = fields.Text(string='Rejection Reason', readonly=True, copy=False)

    move_id = fields.Many2one(
        'account.move',
        string='Journal Entry',
        readonly=True,
        copy=False,
        help='Accounting entry created when this remittance was credited.',
    )
    move_state = fields.Selection(
        related='move_id.state', string='Entry Status', readonly=True,
    )

    # ------------------------------------------------------------------ totals
    @api.depends('receipt_ids.customer_id')
    def _compute_customer_ids(self):
        for rec in self:
            rec.customer_ids = rec.receipt_ids.mapped('customer_id')

    @api.depends('receipt_ids.amount', 'line_ids.amount')
    def _compute_totals(self):
        for rec in self:
            rec.total_received = sum(rec.receipt_ids.mapped('amount'))
            rec.total_expenses = sum(rec.line_ids.mapped('amount'))
            rec.net_amount = rec.total_received - rec.total_expenses

    # --------------------------------------------------------------- sequence
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get('name', _('New')) == _('New'):
                # next_by_code resolves the company from the environment, so run
                # it in the record's own company for multi-company setups.
                company = self.env['res.company'].browse(
                    vals.get('company_id') or self.env.company.id
                )
                vals['name'] = self.with_company(company).env['ir.sequence'].next_by_code(
                    'cognify.remittance'
                ) or _('New')
        return super().create(vals_list)

    # ------------------------------------------------------------- constraints
    @api.constrains('total_received', 'total_expenses')
    def _check_amounts(self):
        for rec in self:
            if rec.total_received < 0:
                raise ValidationError(_('The amount collected cannot be negative.'))
            if rec.total_expenses < 0:
                raise ValidationError(_('Expense amounts cannot be negative.'))
            # Reps must not claim expenses beyond what they actually collected.
            if rec.net_amount < 0:
                raise ValidationError(
                    _(
                        'Total expenses (%(expenses)s) exceed the total collected '
                        '(%(received)s) on %(name)s. The rep would be out of pocket.',
                        expenses=rec.total_expenses,
                        received=rec.total_received,
                        name=rec.name,
                    )
                )

    # ------------------------------------------------------------ state helpers
    def _is_editable(self):
        """Only the rep's own draft/rejected remittances may be changed."""
        self.ensure_one()
        return self.state in ('draft', 'rejected')

    def write(self, vals):
        # Freeze verified/credited records, and freeze a rep out of their own
        # submitted paperwork.
        watched = ('total_received', 'receipt_ids', 'customer_ids', 'rep_id', 'line_ids')
        if any(f in vals for f in watched):
            for rec in self:
                if not rec._is_editable():
                    if rec.env.su or self.env.user.has_group(
                        'cognify_remittance.group_cognify_remittance_approver'
                    ):
                        continue
                    raise UserError(
                        _('%(name)s is %(state)s and can no longer be edited.',
                          name=rec.name, state=dict(rec._fields['state'].selection).get(rec.state, rec.state))
                    )
        return super().write(vals)

    def action_submit(self):
        for rec in self:
            # No expense line is required — a rep may collect cash and spend
            # nothing on the trip. Only the collections themselves are mandatory.
            if not rec.receipt_ids:
                raise UserError(
                    _('Please record at least one customer collection on %(name)s.', name=rec.name)
                )
            if float_is_zero(rec.total_received, precision_digits=AMOUNT_PRECISION_DIGITS):
                raise UserError(
                    _('Please enter the amount collected from at least one customer on %(name)s.',
                      name=rec.name)
                )
        self.write({
            'state': 'submitted',
            'submitted_date': fields.Datetime.now(),
        })
        return True

    def action_verify(self):
        for rec in self:
            if rec.state != 'submitted':
                raise UserError(_('Only submitted remittances can be verified.'))
        self.write({
            'state': 'verified',
            'verified_by_id': self.env.user.id,
            'verified_date': fields.Datetime.now(),
        })
        return True

    def action_reject(self):
        for rec in self:
            if rec.state not in ('submitted', 'verified'):
                raise UserError(_('Only submitted or verified remittances can be rejected.'))
        self.write({'state': 'rejected'})
        return True

    def action_reset_to_draft(self):
        for rec in self:
            if rec.state not in ('rejected', 'draft'):
                raise UserError(_('Only rejected remittances can be sent back to draft.'))
        self.write({
            'state': 'draft',
            'rejection_reason': False,
        })
        return True

    # -------------------------------------------------------------- accounting
    def _prepare_move_lines(self):
        """Build a balanced journal entry covering every customer paid.

        The rep received ``total_received`` in cash across all customers and
        spent ``total_expenses`` of it on the trip, bringing ``net_amount``
        back to the office. That gives:

            Dr Cash (office)            net_amount
            Dr Expense (per category)   each expense line amount
                Cr Accounts Receivable  each customer's collected amount

        Debits = net_amount + total_expenses = total_received, which balances
        against the receivable credits for all customers combined.
        """
        self.ensure_one()
        config = self.env['res.config.settings'].sudo().get_remittance_config()

        cash_account = config.get('remittance_cash_account_id')
        if not cash_account:
            raise UserError(
                _('Please set the remittance cash account in Settings → Cognify Remittance.')
            )

        lines = []
        # Debit the cash actually landing in the office.
        if not float_is_zero(self.net_amount, precision_digits=AMOUNT_PRECISION_DIGITS):
            lines.append((0, 0, {
                'account_id': cash_account.id,
                'name': _('Cash returned by %s', self.rep_id.display_name),
                'debit': self.net_amount,
                'credit': 0.0,
            }))

        # Debit each expense to its own category account.
        expenses_by_account = {}
        for line in self.line_ids:
            account = line.category_id.account_id
            if not account:
                raise UserError(
                    _('Expense category "%(cat)s" has no account set. '
                      'Please configure it before crediting %(name)s.',
                      cat=line.category_id.name, name=self.name)
                )
            expenses_by_account.setdefault(account, 0.0)
            expenses_by_account[account] += line.amount

        for account, amount in expenses_by_account.items():
            lines.append((0, 0, {
                'account_id': account.id,
                'name': _('Field expenses — %(name)s', name=self.name),
                'debit': amount,
                'credit': 0.0,
            }))

        # Credit each customer's receivable — the debt they just paid off.
        # Two collections from the same customer are merged into one credit.
        receivable_by_partner = {}
        for receipt in self.receipt_ids:
            customer = receipt.customer_id
            account = customer.property_account_receivable_id
            if not account:
                raise UserError(
                    _('Customer "%(cust)s" has no receivable account configured.',
                      cust=customer.display_name)
                )
            key = (account, customer)
            receivable_by_partner.setdefault(key, 0.0)
            receivable_by_partner[key] += receipt.amount

        for (account, customer), amount in receivable_by_partner.items():
            lines.append((0, 0, {
                'account_id': account.id,
                'partner_id': customer.id,
                'name': _('Cash collected in the field — %(name)s', name=self.name),
                'debit': 0.0,
                'credit': amount,
            }))
        return lines

    def action_credit_accounts(self):
        """Create and post the journal entry. This is the only path into the books."""
        for rec in self:
            if rec.state != 'verified':
                raise UserError(
                    _('%(name)s must be verified before the accounts can be credited.', name=rec.name)
                )

        moves = self.env['account.move']
        for rec in self:
            lines = rec._prepare_move_lines()
            move = self.env['account.move'].create({
                'move_type': 'entry',
                'date': rec.date,
                'ref': rec.name,
                'journal_id': rec._get_journal().id,
                'company_id': rec.company_id.id,
                'line_ids': lines,
            })
            move.action_post()
            moves |= move

        self.write({'state': 'credited', 'move_id': moves.id if len(moves) == 1 else False})
        return True

    def _get_journal(self):
        """Pick a journal for the entry: a configured one, else any general journal."""
        self.ensure_one()
        config = self.env['res.config.settings'].sudo().get_remittance_config()
        journal = config.get('remittance_journal_id')
        if journal and journal.company_id == self.company_id:
            return journal
        journal = self.env['account.journal'].search([
            ('type', '=', 'general'),
            ('company_id', '=', self.company_id.id),
        ], limit=1)
        if not journal:
            raise UserError(_('No miscellaneous journal found for company %s.', self.company_id.name))
        return journal

    def action_view_move(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Journal Entry'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.move_id.id,
            'target': 'current',
        }

    # ---------------------------------------------------------------- chatter
    # Workflow transitions post to the chatter automatically via `tracking`.


class CognifyRemittanceReceipt(models.Model):
    """Cash collected from a single customer on a trip."""

    _name = 'cognify.remittance.receipt'
    _description = 'Cognify Remittance Customer Collection'
    _order = 'id'

    remittance_id = fields.Many2one(
        'cognify.remittance', string='Remittance', required=True,
        ondelete='cascade', index=True,
    )
    customer_id = fields.Many2one(
        'res.partner',
        string='Customer',
        required=True,
        domain=[('customer_rank', '>', 0)],
        help='The customer this cash was collected from.',
    )
    amount = fields.Monetary(
        string='Amount Collected',
        required=True,
        help='Cash taken from this customer, to be credited to their account.',
    )
    currency_id = fields.Many2one(
        'res.currency', related='remittance_id.currency_id', readonly=True,
    )

    @api.constrains('amount')
    def _check_amount(self):
        for line in self:
            if line.amount < 0:
                raise ValidationError(_('A collected amount cannot be negative.'))


class CognifyRemittanceLine(models.Model):
    """A single expense claimed by the rep for the trip."""

    _name = 'cognify.remittance.line'
    _description = 'Cognify Field Remittance Expense'
    _order = 'category_sequence, id'

    remittance_id = fields.Many2one(
        'cognify.remittance', string='Remittance', required=True,
        ondelete='cascade', index=True,
    )
    category_id = fields.Many2one(
        'cognify.expense.category',
        string='Category',
        required=True,
        domain=[('active', '=', True)],
    )
    category_sequence = fields.Integer(
        related='category_id.sequence', store=True, index=True,
    )
    description = fields.Char(
        string='Description',
        help='Required for the "Other" category; optional otherwise.',
    )
    amount = fields.Monetary(
        required=True,
    )
    currency_id = fields.Many2one(
        'res.currency', related='remittance_id.currency_id', readonly=True,
    )

    @api.constrains('category_id', 'description')
    def _check_other_requires_description(self):
        other_category = self.env.ref(
            'cognify_remittance.expense_category_other', raise_if_not_found=False
        )
        for line in self:
            if other_category and line.category_id == other_category and not (line.description or '').strip():
                raise ValidationError(
                    _('Please describe the expense on the "Other" line (amount %(amt)s).', amt=line.amount)
                )

    @api.onchange('category_id')
    def _onchange_category_id(self):
        # Nudge the rep to type what "Other" was for.
        other_category = self.env.ref(
            'cognify_remittance.expense_category_other', raise_if_not_found=False
        )
        if other_category and self.category_id == other_category:
            self.description = False
