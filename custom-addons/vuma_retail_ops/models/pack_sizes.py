# -*- coding: utf-8 -*-
"""Pack sizes for a single catalogue item.

A shop item is one ``product.template``.  Each pack size becomes a product
variant, so it keeps its own stock, barcode and selling price.

Pack sizes are free-form: an item can be saved as 50ml, 500ml, 1L, 25L,
Pallet, Box of 37 -- whatever the supplier or shelf actually uses.  A pack
row stores its own name, quantity and unit.  When that unit is compatible with
the item's stock unit, the quantity is converted to base units for reporting
(five 6 Packs = 30 singles, 500ml = 0.5L).  ``vuma.pack.size`` is a reusable
library that is filled from the rows, not a fixed list that limits them.

The standard Odoo "Pack Size" attribute is still the mechanism that creates
the variants.  The module keeps that attribute in step with the item's pack
rows so users never have to edit the product's Attributes & Variants tab.
"""
from odoo import Command, _, api, fields, models
from odoo.exceptions import ValidationError

PACK_ATTRIBUTE_XMLID = 'vuma_retail_ops.product_attribute_pack_size'


def _convert_qty(qty, from_uom, to_uom):
    """Convert a quantity between compatible units, or zero if they are not.

    Pack definitions may use a different unit from the item's stock unit
    (50ml on an item stocked in litres).  When the units are not in the same
    family there is no meaningful base quantity; the pack still exists, but
    its equivalent is left at zero instead of producing a nonsense number.
    """
    qty = qty or 0.0
    if not qty or not from_uom or not to_uom:
        return 0.0
    if from_uom == to_uom:
        return qty
    if not from_uom._has_common_reference(to_uom):
        return 0.0
    return from_uom._compute_quantity(qty, to_uom, round=False)


class VumaPackSize(models.Model):
    _name = 'vuma.pack.size'
    _description = 'Pack Size'
    _order = 'sequence, id'

    name = fields.Char(
        string='Pack Size', required=True, translate=True,
        help='How the pack is written on the shelf or supplier document, '
             'for example 50ml, 500ml, 1L, 25L, Pallet or Box of 37.')
    code = fields.Char(
        string='Code', index='btree_not_null',
        help='Optional short code used by imports, for example "pack_6".')
    units = fields.Float(
        string='Default Quantity', digits='Product Unit', default=1.0,
        help='Default quantity in one pack, expressed in the default unit. '
             'Each item can override this.')
    default_uom_id = fields.Many2one(
        'uom.uom', string='Default Unit',
        default=lambda self: self.env.ref(
            'uom.product_uom_unit', raise_if_not_found=False),
        help='Unit the default quantity is expressed in, for example ml, L '
             'or Units.')
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    attribute_value_id = fields.Many2one(
        'product.attribute.value', string='Pack Size Value',
        readonly=True, ondelete='restrict', copy=False,
        help='The value on the shared "Pack Size" attribute that creates '
             'the variant for this pack size.')
    product_count = fields.Integer(
        string='Items Using It', compute='_compute_product_count')

    _code_uniq = models.Constraint(
        'unique(code)',
        'A pack size code can only be used once.')

    @api.depends()
    def _compute_product_count(self):
        Line = self.env['vuma.product.pack.line']
        for pack_size in self:
            pack_size.product_count = Line.search_count([
                '|',
                ('pack_size_id', '=', pack_size.id),
                ('name', '=', pack_size.name),
            ])

    @api.constrains('name')
    def _check_name_is_unique(self):
        for pack_size in self:
            if not pack_size.name:
                continue
            duplicate = self.search_count([
                ('name', '=', pack_size.name),
                ('id', '!=', pack_size.id),
            ])
            if duplicate:
                raise ValidationError(_(
                    'The pack size "%(name)s" already exists. Re-use it, or '
                    'give this one a different name.',
                    name=pack_size.name))

    def _vuma_pack_attribute(self):
        """The shared attribute that turns pack sizes into variants."""
        attribute = self.env.ref(
            PACK_ATTRIBUTE_XMLID, raise_if_not_found=False)
        if not attribute:
            attribute = self.env['product.attribute'].search(
                [('name', '=', _('Pack Size'))], limit=1)
        if not attribute:
            attribute = self.env['product.attribute'].create({
                'name': _('Pack Size'),
                'create_variant': 'always',
                'display_type': 'radio',
                'sequence': 50,
            })
        return attribute

    def _vuma_ensure_attribute_value(self):
        """Return the attribute value for this pack size, creating it once."""
        self.ensure_one()
        attribute = self._vuma_pack_attribute()
        value = self.attribute_value_id
        if value and value.attribute_id != attribute:
            value = self.env['product.attribute.value']
        if not value:
            value = self.env['product.attribute.value'].search([
                ('attribute_id', '=', attribute.id),
                ('name', '=', self.name),
            ], limit=1)
        if not value:
            value = self.env['product.attribute.value'].create({
                'name': self.name,
                'attribute_id': attribute.id,
                'sequence': self.sequence,
            })
        if self.attribute_value_id != value:
            self.attribute_value_id = value
        return value

    @api.model_create_multi
    def create(self, vals_list):
        pack_sizes = super().create(vals_list)
        for pack_size in pack_sizes:
            pack_size._vuma_ensure_attribute_value()
        return pack_sizes

    def write(self, vals):
        res = super().write(vals)
        if 'name' in vals or 'sequence' in vals:
            for pack_size in self:
                value = pack_size._vuma_ensure_attribute_value()
                if 'name' in vals:
                    value.name = pack_size.name
                if 'sequence' in vals:
                    value.sequence = pack_size.sequence
        return res


class VumaProductPackLine(models.Model):
    _name = 'vuma.product.pack.line'
    _description = 'Product Pack Size'
    _order = 'template_id, sequence, id'

    template_id = fields.Many2one(
        'product.template', string='Item', required=True,
        ondelete='cascade', index=True)
    name = fields.Char(
        string='Pack Name',
        help='Any name that makes sense on the shelf: 50ml, 500ml, 1L, 25L, '
             'Pallet, Box of 37, 6 Pack. This is the label shown on the '
             'pack-size stock item.')
    pack_size_id = fields.Many2one(
        'vuma.pack.size', string='Saved Pack', index=True,
        ondelete='restrict',
        help='Optional. Pick a pack from the shared library to copy its '
             'name, quantity and unit into this row.')
    sequence = fields.Integer(default=10)
    pack_qty = fields.Float(
        string='Pack Quantity', digits='Product Unit', default=1.0,
        help='How much is in one pack, in the pack unit. For example 50 with '
             'a pack unit of ml, 1 with a pack unit of L, or 6 with a pack '
             'unit of Units.')
    pack_uom_id = fields.Many2one(
        'uom.uom', string='Pack Unit',
        default=lambda self: self._vuma_default_pack_uom(),
        help='Unit the pack quantity is written in: ml, L, Units, kg, ...')
    units_per_pack = fields.Float(
        string='Base Units', digits='Product Unit', default=0.0,
        help='The pack quantity converted to the item\'s stock unit. It is '
             'filled from the pack quantity and unit.')
    price = fields.Float(
        string='Pack Price', digits='Product Price',
        help='Selling price of one pack. Leave zero to use the item\'s '
             'normal sales price.')
    currency_id = fields.Many2one(
        related='template_id.currency_id', readonly=True)
    barcode = fields.Char(
        string='Pack Barcode',
        help='Barcode printed on this pack. It is applied to the pack-size '
             'stock item when the item has a single variant for this pack '
             'size.')
    product_ids = fields.Many2many(
        'product.product', string='Stock Items',
        compute='_compute_product_ids')
    product_count = fields.Integer(
        string='Stock Items', compute='_compute_product_ids')
    qty_available = fields.Float(
        string='On Hand', digits='Product Unit',
        compute='_compute_quantities')
    single_equivalent_qty = fields.Float(
        string='Equivalent Base Units', digits='Product Unit',
        compute='_compute_quantities')

    _pack_size_uniq = models.Constraint(
        'unique(template_id, pack_size_id)',
        'Each saved pack can only be listed once on an item.')

    def _vuma_default_pack_uom(self):
        template_id = self.env.context.get('default_template_id')
        if template_id:
            template = self.env['product.template'].browse(template_id)
            if template.uom_id:
                return template.uom_id
        return self.env.ref('uom.product_uom_unit', raise_if_not_found=False)

    def _vuma_pack_name(self):
        """The name written on this row, with the old saved pack as fallback."""
        self.ensure_one()
        return (self.name or (self.pack_size_id.name or '')).strip()

    def _vuma_find_pack_size(self):
        """Find the shared library row for this pack, without creating one."""
        self.ensure_one()
        name = self._vuma_pack_name()
        if not name:
            return self.env['vuma.pack.size']
        if self.pack_size_id and self.pack_size_id.name == name:
            return self.pack_size_id
        return self.env['vuma.pack.size'].search(
            [('name', '=', name)], limit=1)

    def _vuma_ensure_pack_size(self):
        """Find or create the shared library row used to build the variant."""
        self.ensure_one()
        pack_size = self._vuma_find_pack_size()
        if pack_size:
            return pack_size
        name = self._vuma_pack_name()
        if not name:
            raise ValidationError(_(
                'Give each pack a name on %s.', self.template_id.display_name))
        return self.env['vuma.pack.size'].create({
            'name': name,
            'units': self.pack_qty or self.units_per_pack or 0.0,
            'default_uom_id': (
                self.pack_uom_id or self.template_id.uom_id).id,
            'sequence': self.sequence or 10,
        })

    def _vuma_base_qty(self):
        """This pack's quantity in the item's stock unit."""
        self.ensure_one()
        if not self.pack_qty:
            return self.units_per_pack or 0.0
        return _convert_qty(
            self.pack_qty,
            self.pack_uom_id or self.template_id.uom_id,
            self.template_id.uom_id)

    def _vuma_recompute_base_qty(self):
        self.units_per_pack = self._vuma_base_qty()

    @api.depends('template_id.product_variant_ids'
                 '.product_template_attribute_value_ids')
    def _compute_product_ids(self):
        for line in self:
            value = line._vuma_find_pack_size().attribute_value_id
            variants = line.template_id.product_variant_ids.filtered(
                lambda product, value=value: value and value in
                product.product_template_attribute_value_ids
                .product_attribute_value_id)
            line.product_ids = variants
            line.product_count = len(variants)

    @api.depends('product_ids.qty_available', 'product_ids.vuma_units_per_pack',
                 'units_per_pack')
    def _compute_quantities(self):
        for line in self:
            line.qty_available = sum(line.product_ids.mapped('qty_available'))
            line.single_equivalent_qty = sum(
                product.qty_available * (product.vuma_units_per_pack
                                         or line.units_per_pack)
                for product in line.product_ids)

    @api.onchange('pack_size_id')
    def _onchange_pack_size_id(self):
        if not self.pack_size_id:
            return
        self.name = self.pack_size_id.name
        self.pack_qty = self.pack_size_id.units
        self.pack_uom_id = (
            self.pack_size_id.default_uom_id or self.template_id.uom_id)
        self.sequence = self.pack_size_id.sequence
        self._vuma_recompute_base_qty()
        if not self.price and self.template_id:
            self.price = self.template_id.list_price * (
                self.units_per_pack or 1.0)

    @api.onchange('pack_qty', 'pack_uom_id')
    def _onchange_pack_quantity(self):
        self._vuma_recompute_base_qty()

    @api.onchange('template_id')
    def _onchange_template_id(self):
        if self.template_id and not self.pack_uom_id:
            self.pack_uom_id = self.template_id.uom_id
        if self.template_id and self.pack_qty and self.pack_uom_id:
            self._vuma_recompute_base_qty()

    @api.onchange('name')
    def _onchange_name(self):
        if (self.name and self.pack_size_id
                and self.name != self.pack_size_id.name):
            self.pack_size_id = False

    @api.constrains('name', 'pack_size_id', 'template_id')
    def _check_pack_identity(self):
        for line in self:
            name = line._vuma_pack_name()
            if not name:
                raise ValidationError(_(
                    'Give each pack a name on %s.',
                    line.template_id.display_name))
            duplicate = self.search_count([
                ('template_id', '=', line.template_id.id),
                ('name', '=', name),
                ('id', '!=', line.id),
            ])
            if duplicate:
                raise ValidationError(_(
                    'The pack "%(name)s" is already listed on %(item)s.',
                    name=name, item=line.template_id.display_name))

    def _vuma_sync_variants(self):
        """Push this line's quantity, barcode and price to its variants."""
        self.ensure_one()
        template = self.template_id
        pack_size = self._vuma_ensure_pack_size()
        value = pack_size.attribute_value_id
        variants = template.product_variant_ids.filtered(
            lambda product: value in
            product.product_template_attribute_value_ids.product_attribute_value_id)
        if variants:
            vals = {'vuma_units_per_pack': self.units_per_pack or 0.0}
            if self.barcode and len(variants) == 1:
                vals['barcode'] = self.barcode
            variants.write(vals)
        values = template.attribute_line_ids.product_template_value_ids
        values = values.filtered(
            lambda ptav: ptav.product_attribute_value_id == value)
        # The variant's selling price is the template price plus the extra
        # price stored on its attribute value.  A zero pack price means "use
        # the item's normal price", so it clears any extra.  The sequence
        # follows the row order on the item, not the library order.
        values.write({
            'price_extra': (
                self.price - template.list_price) if self.price else 0.0,
            'sequence': self.sequence or 10,
        })

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            pack_size = self.env['vuma.pack.size'].browse(
                vals.get('pack_size_id') or [])
            if pack_size and not vals.get('name'):
                vals['name'] = pack_size.name
            template = self.env['product.template'].browse(
                vals.get('template_id') or [])
            if template:
                if not vals.get('pack_uom_id'):
                    vals['pack_uom_id'] = (
                        pack_size.default_uom_id or template.uom_id).id
                if (vals.get('pack_qty') and vals.get('pack_uom_id')
                        and not vals.get('units_per_pack')):
                    vals['units_per_pack'] = _convert_qty(
                        vals['pack_qty'],
                        self.env['uom.uom'].browse(vals['pack_uom_id']),
                        template.uom_id)
        lines = super().create(vals_list)
        if self.env.context.get('vuma_pack_sync', True):
            lines.mapped('template_id')._vuma_apply_pack_sizes()
        return lines

    def write(self, vals):
        res = super().write(vals)
        if {'pack_qty', 'pack_uom_id', 'name', 'pack_size_id',
                'template_id'} & vals.keys():
            for line in self:
                updates = {}
                if not line.name and line.pack_size_id:
                    updates['name'] = line.pack_size_id.name
                if not line.pack_uom_id and line.template_id:
                    updates['pack_uom_id'] = line.template_id.uom_id.id
                if ('pack_qty' in vals or 'pack_uom_id' in vals) and (
                        'units_per_pack' not in vals):
                    updates['units_per_pack'] = line._vuma_base_qty()
                if updates:
                    super(VumaProductPackLine, line).write(updates)
        if self.env.context.get('vuma_pack_sync', True):
            self.mapped('template_id')._vuma_apply_pack_sizes()
        return res

    def unlink(self):
        templates = self.mapped('template_id')
        res = super().unlink()
        if self.env.context.get('vuma_pack_sync', True):
            templates._vuma_apply_pack_sizes()
        return res


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    vuma_pack_line_ids = fields.One2many(
        'vuma.product.pack.line', 'template_id', string='Pack Sizes',
        copy=True)
    vuma_pack_count = fields.Integer(
        string='Pack Sizes', compute='_compute_vuma_pack_info')
    vuma_pack_summary = fields.Char(
        string='Pack Sizes', compute='_compute_vuma_pack_info')
    vuma_has_pack_sizes = fields.Boolean(
        string='Has Pack Sizes', compute='_compute_vuma_has_pack_sizes',
        store=True, index=True)

    @api.depends('vuma_pack_line_ids', 'vuma_pack_line_ids.name',
                 'vuma_pack_line_ids.pack_size_id',
                 'vuma_pack_line_ids.pack_qty',
                 'vuma_pack_line_ids.pack_uom_id',
                 'vuma_pack_line_ids.units_per_pack')
    def _compute_vuma_pack_info(self):
        for template in self:
            lines = template.vuma_pack_line_ids.sorted(
                lambda line: (line.sequence, line.id))
            template.vuma_pack_count = len(lines)
            parts = []
            for line in lines:
                name = line._vuma_pack_name()
                if line.pack_qty and line.pack_uom_id:
                    parts.append('%s (%g %s)' % (
                        name, line.pack_qty, line.pack_uom_id.name))
                elif line.units_per_pack:
                    parts.append('%s (%g)' % (name, line.units_per_pack))
                else:
                    parts.append(name)
            template.vuma_pack_summary = ', '.join(parts)

    @api.depends('vuma_pack_line_ids')
    def _compute_vuma_has_pack_sizes(self):
        for template in self:
            template.vuma_has_pack_sizes = bool(template.vuma_pack_line_ids)

    def _vuma_apply_pack_sizes(self):
        """Rebuild the shared Pack Size attribute and its variants.

        A single-value line is written first.  When an existing item is
        converted, that keeps its current stock on the first pack size
        (normally Single) instead of letting Odoo recreate the variant.
        """
        for template in self:
            lines = template.vuma_pack_line_ids.sorted(
                lambda line: (line.sequence, line.id))
            if not lines:
                continue
            values = self.env['product.attribute.value']
            for line in lines:
                values |= line._vuma_ensure_pack_size().attribute_value_id
            if not values:
                continue
            attribute = values[:1].attribute_id
            attribute_line = template.attribute_line_ids.filtered(
                lambda line: line.attribute_id == attribute and line.active
            ).sorted('id')[:1]
            if not attribute_line:
                preferred = lines.filtered(
                    lambda line: line._vuma_pack_name().lower()
                    in ('single', 'each'))
                first = (preferred or lines)[:1]
                first_value = first._vuma_ensure_pack_size().attribute_value_id
                attribute_line = self.env[
                    'product.template.attribute.line'].create({
                        'product_tmpl_id': template.id,
                        'attribute_id': attribute.id,
                        'value_ids': [Command.set([first_value.id])],
                    })
            if set(attribute_line.value_ids.ids) != set(values.ids):
                attribute_line.write({
                    'value_ids': [Command.set(values.ids)],
                })
            template.env.flush_all()
            for line in lines:
                line._vuma_sync_variants()

    def action_apply_pack_sizes(self):
        self._vuma_apply_pack_sizes()
        return True

    def action_add_standard_pack_sizes(self):
        """Add the everyday examples in one click; every row stays editable."""
        specs = [
            ('single', 'Single', 1.0),
            ('pack_6', '6 Pack', 6.0),
            ('pack_12', '12 Pack', 12.0),
            ('pack_24', '24 Pack', 24.0),
            ('pallet', 'Pallet', 0.0),
        ]
        for template in self:
            existing = template.vuma_pack_line_ids.mapped('name')
            commands = []
            for index, (code, fallback_name, fallback_qty) in enumerate(
                    specs, start=1):
                pack_size = self.env['vuma.pack.size'].search(
                    [('code', '=', code)], limit=1)
                name = pack_size.name if pack_size else fallback_name
                if name in existing:
                    continue
                qty = pack_size.units if pack_size else fallback_qty
                uom = (
                    (pack_size.default_uom_id if pack_size else False)
                    or template.uom_id)
                commands.append(Command.create({
                    'pack_size_id': pack_size.id or False,
                    'name': name,
                    'pack_qty': qty,
                    'pack_uom_id': uom.id,
                    'units_per_pack': _convert_qty(
                        qty, uom, template.uom_id),
                    'price': template.list_price * (qty or 0.0),
                    'sequence': index * 10,
                }))
            if commands:
                template.write({'vuma_pack_line_ids': commands})
        return True

    @api.model_create_multi
    def create(self, vals_list):
        templates = super(
            ProductTemplate,
            self.with_context(vuma_pack_sync=False)).create(vals_list)
        # ``create`` returns records attached to the guarded context above.
        # Put the caller's context back so later writes still sync.
        templates = templates.with_env(self.env)
        templates.filtered('vuma_pack_line_ids')._vuma_apply_pack_sizes()
        return templates

    def write(self, vals):
        res = super(
            ProductTemplate,
            self.with_context(vuma_pack_sync=False)).write(vals)
        if self.env.context.get('vuma_pack_sync', True) and (
                'vuma_pack_line_ids' in vals or 'list_price' in vals):
            self.filtered('vuma_pack_line_ids')._vuma_apply_pack_sizes()
        return res


class ProductProduct(models.Model):
    _inherit = 'product.product'

    vuma_pack_size_id = fields.Many2one(
        'vuma.pack.size', string='Pack Size',
        compute='_compute_vuma_pack_size_id')
    vuma_units_per_pack = fields.Float(
        string='Base Units', digits='Product Unit', default=0.0,
        help='Number of base stock units in this pack-size stock item. It '
             'is set on the item\'s Pack Sizes tab.')
    vuma_single_equivalent_qty = fields.Float(
        string='Equivalent Base Units', digits='Product Unit',
        compute='_compute_vuma_single_equivalent_qty',
        help='On hand, converted to base units using this pack size.')

    @api.depends('product_template_attribute_value_ids'
                 '.product_attribute_value_id')
    def _compute_vuma_pack_size_id(self):
        PackSize = self.env['vuma.pack.size']
        values = self.mapped(
            'product_template_attribute_value_ids.product_attribute_value_id')
        pack_sizes = PackSize.search(
            [('attribute_value_id', 'in', values.ids)]) if values else PackSize
        mapping = {pack.attribute_value_id.id: pack for pack in pack_sizes}
        for product in self:
            value_ids = product.product_template_attribute_value_ids\
                .product_attribute_value_id.ids
            product.vuma_pack_size_id = next(
                (mapping[value_id] for value_id in value_ids
                 if value_id in mapping), False)

    @api.depends('qty_available', 'vuma_units_per_pack')
    def _compute_vuma_single_equivalent_qty(self):
        for product in self:
            product.vuma_single_equivalent_qty = (
                product.qty_available * (product.vuma_units_per_pack or 0.0))

    @api.model_create_multi
    def create(self, vals_list):
        products = super().create(vals_list)
        for product in products:
            if (product.vuma_pack_size_id and not product.vuma_units_per_pack
                    and product.vuma_pack_size_id.units):
                product.vuma_units_per_pack = product.vuma_pack_size_id.units
        return products

    def action_apply_pack_sizes(self):
        """Variant-side entry point for the template form's button."""
        self.ensure_one()
        return self.product_tmpl_id.action_apply_pack_sizes()

    def action_add_standard_pack_sizes(self):
        """Variant-side entry point for the template form's button."""
        self.ensure_one()
        return self.product_tmpl_id.action_add_standard_pack_sizes()


class ProductTemplateAttributeValue(models.Model):
    _inherit = 'product.template.attribute.value'

    vuma_pack_size_id = fields.Many2one(
        'vuma.pack.size', string='Pack Size',
        compute='_compute_vuma_pack_size_id')

    @api.depends('product_attribute_value_id')
    def _compute_vuma_pack_size_id(self):
        PackSize = self.env['vuma.pack.size']
        values = self.mapped('product_attribute_value_id')
        pack_sizes = PackSize.search(
            [('attribute_value_id', 'in', values.ids)]) if values else PackSize
        mapping = {pack.attribute_value_id.id: pack for pack in pack_sizes}
        for ptav in self:
            ptav.vuma_pack_size_id = mapping.get(
                ptav.product_attribute_value_id.id)
