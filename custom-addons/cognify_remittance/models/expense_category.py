from odoo import api, fields, models


class CognifyExpenseCategory(models.Model):
    """Configurable travel/expense categories shown to the rep in a dropdown.

    Finance owns this list: each category points at the ledger account the
    expense lands in when the remittance is credited. Keeping it as a model
    rather than a hard-coded selection means a new category is a record, not a
    code change.
    """

    _name = 'cognify.expense.category'
    _description = 'Cognify Field Expense Category'
    _order = 'sequence, name'

    name = fields.Char(required=True, translate=True)
    sequence = fields.Integer(default=10)
    code = fields.Char(
        help='Short internal reference, e.g. FOOD. Not shown to reps.',
    )
    account_id = fields.Many2one(
        'account.account',
        string='Expense Account',
        domain=[('deprecated', '=', False)],
        help='Ledger account debited when a remittance containing this '
             'category is credited. Leave empty to block crediting.',
    )
    active = fields.Boolean(default=True)

    _name_uniq = models.Constraint(
        'UNIQUE(name)', 'An expense category with this name already exists.'
    )

    @api.depends('name')
    def _compute_display_name(self):
        # Shows the bare category name so the rep's dropdown reads naturally.
        for category in self:
            category.display_name = category.name

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get('code'):
                vals['code'] = ''.join(
                    ch for ch in (vals.get('name') or '').upper() if ch.isalnum()
                )[:12]
        return super().create(vals_list)