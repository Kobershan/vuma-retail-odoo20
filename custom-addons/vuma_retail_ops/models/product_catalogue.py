# -*- coding: utf-8 -*-
"""Catalogue tabs for a product.

The product form in Vuma Retail is organised around what a shop actually
needs to know about an item:

* **General** -- the everyday fields plus how many pack sizes the item has.
* **Details** -- references, units, stock figures and free-text details.
* **Suppliers** -- the vendors and their prices / lead times.
* **Recipes** -- the bill of materials used to make the item (Odoo MRP).
* **Labels** -- shelf / price / barcode labels that can be printed.
* **Components** -- every *other* item that uses this stock part, and how
  much of it each parent requires.

Recipes and components are the two directions of ``mrp.bom.line``:

* ``template.bom_ids`` are the recipes for the item;
* ``mrp.bom.line`` rows whose ``product_tmpl_id`` is the item are the
  components -- i.e. the parent products that consume it.

The ``vuma_component_label`` field on a BOM line lets the same stock part be
named differently in each recipe without renaming the catalogue product.
"""
from odoo import api, fields, models
from odoo.exceptions import UserError

# Categories that describe what an item is for accounting or which company
# owns it, rather than the shelf / department it sits on.
DEPARTMENT_SKIPPED_EXACT = {
    'Goods',
    'Expenses',
    'Services',
    'Deliveries',
    'Donations',
}
DEPARTMENT_SKIPPED_PARTS = (
    'Products',
    'Product',
)


class VumaDepartment(models.Model):
    """A retail department (Paint, Hardware, Groceries, ...).

    Departments are deliberately independent of HR departments: they
    classify stock, not people, and remain usable in companies that do not
    install the HR application.
    """

    _name = 'vuma.department'
    _description = 'Stock Department'
    _order = 'sequence, name'
    _check_company_auto = True

    name = fields.Char(string='Department', required=True, translate=True)
    code = fields.Char(
        string='Code', index='btree_not_null',
        help='Short code used on reports, for example PAINT or HARD.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        'res.company', string='Company', index=True,
        help='Leave empty for a department shared by every company.')
    note = fields.Text(string='Notes')
    product_count = fields.Integer(
        string='Products', compute='_compute_product_count')

    _code_company_uniq = models.Constraint(
        'unique(company_id, code)',
        'A department code can only be used once per company.')

    @api.depends()
    def _compute_product_count(self):
        Template = self.env['product.template']
        for department in self:
            department.product_count = Template.search_count([
                ('vuma_department_id', '=', department.id),
            ])

    @api.model
    def _vuma_seed_from_catalogue(self):
        """Create departments from top-level catalogue categories.

        Used both by the upgrade migrations and by a fresh installation.
        Company / accounting buckets are skipped, and every remaining stock
        product is put in a shared *General* department so no stocked item
        is left without one.
        """
        Template = self.env['product.template']
        for category in self.env['product.category'].search([]):
            root = category
            while root.parent_id:
                root = root.parent_id
            name = root.name
            if isinstance(name, dict):
                name = name.get('en_US') or next(iter(name.values()), '')
            name = (name or '').strip()
            if (
                not name
                or name in DEPARTMENT_SKIPPED_EXACT
                or any(part in name for part in DEPARTMENT_SKIPPED_PARTS)
            ):
                continue
            department = self.with_context(active_test=False).search(
                [('name', '=', name)], limit=1)
            if not department:
                department = self.create({
                    'name': name,
                    'active': True,
                })
            templates = Template.search([
                ('categ_id', 'child_of', root.id),
                ('vuma_department_id', '=', False),
            ])
            if templates:
                templates.write({'vuma_department_id': department.id})

        remaining = Template.search([
            ('type', '=', 'consu'),
            ('vuma_department_id', '=', False),
        ])
        if remaining:
            general = self.with_context(active_test=False).search(
                [('name', '=', 'General')], limit=1)
            if not general:
                general = self.create({
                    'name': 'General',
                    'note': 'Fallback department for stock products that did '
                            'not match a catalogue category.',
                })
            remaining.write({'vuma_department_id': general.id})
        return True


class VumaProductLabel(models.Model):
    """A printable label line for a product or one of its pack sizes."""

    _name = 'vuma.product.label'
    _description = 'Product Label'
    _order = 'sequence, id'

    template_id = fields.Many2one(
        'product.template', string='Product', required=True,
        ondelete='cascade', index=True)
    name = fields.Char(
        string='Label Text', required=True,
        help='Main text printed on the label. Defaults to the pack name, or '
             'the product name when the label is for the base item.')
    label_type = fields.Selection([
        ('shelf', 'Shelf Label'),
        ('price', 'Price Label'),
        ('promo', 'Promotion Label'),
        ('barcode', 'Barcode Label'),
    ], string='Label Type', default='shelf', required=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    pack_line_id = fields.Many2one(
        'vuma.product.pack.line', string='Pack Size', index=True,
        ondelete='set null',
        help='Print the label for a specific pack size instead of the base '
             'item.')
    product_id = fields.Many2one(
        'product.product', string='Variant', index=True,
        ondelete='set null',
        help='Optional variant to print the barcode of.')
    barcode = fields.Char(
        string='Barcode',
        help='Barcode printed on the label. Defaults to the pack or variant '
             'barcode.')
    price = fields.Float(
        string='Price', digits='Product Price',
        help='Shelf price printed on the label. Defaults to the pack price, '
             'or the product sales price.')
    currency_id = fields.Many2one(
        related='template_id.currency_id', string='Currency', readonly=True)
    price_display = fields.Char(
        string='Price Display', compute='_compute_price_display')
    label_size = fields.Selection([
        ('shelf', 'Shelf Label (100 x 50 mm)'),
        ('small', 'Small Label (50 x 25 mm)'),
        ('a4', 'A4 Sheet Label'),
    ], string='Label Size', default='shelf', required=True)
    copies = fields.Integer(
        string='Copies', default=1,
        help='How many of this label to print at a time.')
    notes = fields.Text(
        string='Extra Details',
        help='Printed under the price, for example a promotion end date or '
             'storage note.')
    last_printed = fields.Datetime(string='Last Printed', readonly=True)
    print_count = fields.Integer(
        string='Times Printed', default=0, readonly=True)

    def _vuma_default_barcode(self):
        self.ensure_one()
        return (
            (self.pack_line_id.barcode if self.pack_line_id else False)
            or (self.product_id.barcode if self.product_id else False)
            or self.template_id.barcode
            or ''
        )

    @api.depends('price', 'currency_id')
    def _compute_price_display(self):
        for label in self:
            label.price_display = (
                label.currency_id.format(label.price)
                if label.currency_id else '%g' % (label.price or 0.0))

    def _vuma_default_price(self):
        self.ensure_one()
        if self.pack_line_id and self.pack_line_id.price:
            return self.pack_line_id.price
        if self.product_id and self.product_id.lst_price:
            return self.product_id.lst_price
        return self.template_id.list_price

    def _vuma_default_name(self):
        self.ensure_one()
        if self.pack_line_id:
            return self.pack_line_id._vuma_pack_name()
        return self.template_id.name

    @api.onchange('template_id', 'pack_line_id', 'product_id')
    def _onchange_label_target(self):
        for label in self:
            if not label.template_id:
                continue
            if label.pack_line_id and not label.product_id:
                variants = label.pack_line_id.product_ids
                if len(variants) == 1:
                    label.product_id = variants
            if not label.name or label.name == label.template_id.name:
                label.name = label._vuma_default_name()
            label.barcode = label._vuma_default_barcode()
            label.price = label._vuma_default_price()

    @api.model_create_multi
    def create(self, vals_list):
        Template = self.env['product.template']
        PackLine = self.env['vuma.product.pack.line']
        Product = self.env['product.product']
        for vals in vals_list:
            template = Template.browse(vals.get('template_id'))
            if not template:
                continue
            pack_line = PackLine.browse(vals.get('pack_line_id'))
            product = Product.browse(vals.get('product_id'))
            if not vals.get('name'):
                vals['name'] = (
                    pack_line._vuma_pack_name()
                    if pack_line else template.name)
            if 'barcode' not in vals:
                vals['barcode'] = (
                    (pack_line.barcode if pack_line else False)
                    or (product.barcode if product else False)
                    or template.barcode
                    or '')
            if 'price' not in vals:
                if pack_line and pack_line.price:
                    vals['price'] = pack_line.price
                elif product and product.lst_price:
                    vals['price'] = product.lst_price
                else:
                    vals['price'] = template.list_price
        return super().create(vals_list)

    def action_print_label(self):
        """Print the selected labels (from the list or a product tab)."""
        self.ensure_one()
        return self.env.ref(
            'vuma_retail_ops.action_report_vuma_product_label'
        ).report_action(self, config=False)


class MrpBomLine(models.Model):
    _inherit = 'mrp.bom.line'

    vuma_component_label = fields.Char(
        string='Component Name',
        help='Optional name used for this component inside recipes and the '
             'Components tab, for example "Red lid" when the same stock part '
             'is used for different purposes. The catalogue product itself is '
             'not renamed.')


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vuma_department_id = fields.Many2one(
        'vuma.department', string='Department', index=True,
        ondelete='restrict', check_company=True,
        help='Stock department this item belongs to, used for filtering and '
             'departmental stock reporting.')
    vuma_label_ids = fields.One2many(
        'vuma.product.label', 'template_id', string='Labels', copy=True)
    vuma_label_count = fields.Integer(
        string='Labels', compute='_compute_vuma_label_count')
    vuma_recipe_count = fields.Integer(
        string='Recipes', compute='_compute_vuma_recipe_and_component_info')
    vuma_recipe_line_ids = fields.Many2many(
        'mrp.bom.line', string='Recipe Components',
        compute='_compute_vuma_recipe_and_component_info',
        help='Every component line that belongs to one of this item\'s '
             'recipes.')
    vuma_component_ids = fields.Many2many(
        'mrp.bom.line', string='Used In',
        compute='_compute_vuma_recipe_and_component_info',
        help='Recipe lines in other items that consume this stock part.')
    vuma_used_in_product_count = fields.Integer(
        string='Used in Items',
        compute='_compute_vuma_recipe_and_component_info',
        help='Number of different parent products that require this stock '
             'part.')

    @api.depends('vuma_label_ids')
    def _compute_vuma_label_count(self):
        for template in self:
            template.vuma_label_count = len(template.vuma_label_ids)

    @api.depends('bom_ids', 'bom_ids.bom_line_ids', 'bom_line_ids')
    def _compute_vuma_recipe_and_component_info(self):
        Line = self.env['mrp.bom.line']
        for template in self:
            recipe_lines = Line.search([
                ('bom_id.product_tmpl_id', '=', template.id),
            ])
            component_lines = Line.search([
                ('product_tmpl_id', '=', template.id),
            ])
            template.vuma_recipe_count = len(template.bom_ids)
            template.vuma_recipe_line_ids = recipe_lines
            template.vuma_component_ids = component_lines
            template.vuma_used_in_product_count = len(
                component_lines.mapped('bom_id.product_tmpl_id'))

    def action_add_default_labels(self):
        """Add one shelf label for the base item and one per pack size."""
        Label = self.env['vuma.product.label']
        for template in self:
            existing_pack_ids = template.vuma_label_ids.filtered(
                lambda label: label.label_type == 'shelf'
            ).mapped('pack_line_id').ids
            values = []
            if not template.vuma_label_ids.filtered(
                    lambda label: label.label_type == 'shelf'
                    and not label.pack_line_id):
                variants = template.product_variant_ids
                values.append({
                    'template_id': template.id,
                    'name': template.name,
                    'label_type': 'shelf',
                    'barcode': template.barcode or '',
                    'price': template.list_price,
                    'product_id': (
                        variants.id if len(variants) == 1 else False),
                })
            for pack_line in template.vuma_pack_line_ids.sorted(
                    lambda line: (line.sequence, line.id)):
                if pack_line.id in existing_pack_ids:
                    continue
                values.append({
                    'template_id': template.id,
                    'name': pack_line._vuma_pack_name(),
                    'label_type': 'shelf',
                    'pack_line_id': pack_line.id,
                    'product_id': (
                        pack_line.product_ids.id
                        if len(pack_line.product_ids) == 1 else False),
                    'barcode': pack_line.barcode or '',
                    'price': pack_line.price or template.list_price,
                })
            if values:
                Label.create(values)
        return True

    def action_print_labels(self):
        """Print every label configured on the item."""
        self.ensure_one()
        if not self.vuma_label_ids:
            raise UserError(
                'Add at least one label to this product before printing.')
        return self.env.ref(
            'vuma_retail_ops.action_report_vuma_product_label'
        ).report_action(self.vuma_label_ids, config=False)

    def action_new_recipe(self):
        """Open a blank BOM for this item."""
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': 'New Recipe',
            'res_model': 'mrp.bom',
            'view_mode': 'form',
            'target': 'current',
            'context': {
                'default_product_tmpl_id': self.id,
                'default_product_id': (
                    self.product_variant_ids.id
                    if len(self.product_variant_ids) == 1 else False),
                'default_uom_id': self.uom_id.id,
                'default_company_id': (
                    self.company_id.id or self.env.company.id),
            },
        }

    def action_open_recipes(self):
        """Open this item's recipes in the standard BOM list."""
        self.ensure_one()
        action = self.env['ir.actions.actions']._for_xml_id(
            'mrp.mrp_bom_form_action')
        action['domain'] = [('product_tmpl_id', '=', self.id)]
        action['context'] = {'default_product_tmpl_id': self.id}
        return action


class ProductProduct(models.Model):
    """The variant form inherits the template form, so expose the actions."""

    _inherit = 'product.product'

    def action_add_default_labels(self):
        self.ensure_one()
        return self.product_tmpl_id.action_add_default_labels()

    def action_print_labels(self):
        self.ensure_one()
        return self.product_tmpl_id.action_print_labels()

    def action_new_recipe(self):
        self.ensure_one()
        return self.product_tmpl_id.action_new_recipe()

    def action_open_recipes(self):
        self.ensure_one()
        return self.product_tmpl_id.action_open_recipes()
