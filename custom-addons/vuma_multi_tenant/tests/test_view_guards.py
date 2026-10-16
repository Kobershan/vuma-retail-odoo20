"""View-level guards for the views this add-on and its neighbours ship.

These are cross-cutting checks: `column_invisible` was the one attribute that
silently took a whole form down in the browser while every server-side check
passed, and it does the same in three different modules. Rather than repeat
this file in each of them, it runs once from here and looks at every view any
non-core module in this database ships.
"""
import os

from lxml import etree

import odoo
from odoo.modules.module import get_module_path
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval

MODULE = 'vuma_multi_tenant'


def _odoo_source_root():
    """The directory that holds Odoo's own source, core addons included.

    `odoo.addons.__path__` cannot be used for this: it lists every entry of
    `addons_path`, so `custom-addons` sits in it next to `base` and every
    module looks like a core one. Odoo's own tree is instead the parent of the
    `odoo` package, which covers both `odoo/addons` (sale, stock, account) and
    `odoo/odoo/addons` (base, web) and nothing of ours.
    """
    return os.path.abspath(os.path.dirname(odoo.__path__[0]))


@tagged('post_install', '-at_install', MODULE)
class TestColumnInvisibleScope(TransactionCase):
    """`column_invisible` is evaluated outside the row, so it cannot see the row.

    The list renderer resolves a column's `column_invisible` once for the whole
    list, against the list's eval context. That context holds the user, the
    context and the parent record -- and none of the row's own fields. A view
    that writes `column_invisible="not some_field"` therefore compiles, loads,
    passes every server-side test, and then throws

        EvalError: Can not evaluate python expression: (bool(not some_field))
        Error: Name 'some_field' is not defined

    in the browser the moment that form is opened. `parent.some_field` and
    `context.get(...)` are the forms that actually resolve.
    """

    def _own_module_names(self):
        """Installed modules that are ours rather than Odoo's."""
        root = _odoo_source_root()
        modules = self.env['ir.module.module'].search([
            ('state', '=', 'installed')])
        own = []
        for module in modules:
            path = get_module_path(module.name)
            if path and not os.path.abspath(path).startswith(root + os.sep):
                own.append(module.name)
        return own

    def _own_views(self):
        """Every stored view arch belonging to a non-core module.

        A view has no `module` field; the module it came from is on its
        `ir.model.data` entry, which is what gets read here.
        """
        modules = self._own_module_names()
        data = self.env['ir.model.data'].search([
            ('model', '=', 'ir.ui.view'), ('module', 'in', modules),
        ])
        views = self.env['ir.ui.view'].browse(data.mapped('res_id'))
        return views.filtered(
            lambda view: view.arch_db
            and view.type in ('list', 'tree', 'form', 'kanban'))

    def _list_eval_context(self):
        """Exactly the scope `getBasicEvalContext` hands the list renderer."""
        return {
            'context': {},
            'uid': self.env.uid,
            'allowed_company_ids': [self.env.company.id],
            'current_company_id': self.env.company.id,
            # `parent.<field>` is the supported way to reach record data, so the
            # guard does not second-guess which parent is meant -- only that
            # bare names, which can never resolve, are rejected.
            'parent': _PermissiveParent(),
        }

    def test_column_invisible_only_uses_names_the_list_can_resolve(self):
        broken = []
        seen = 0
        for view in self._own_views():
            try:
                root = etree.fromstring(view.arch_db)
            except etree.XMLSyntaxError:
                continue
            for node in root.iter():
                expression = node.get('column_invisible')
                if not expression:
                    continue
                seen += 1
                try:
                    safe_eval(expression, self._list_eval_context())
                except NameError as error:
                    broken.append('%s (%s): column_invisible="%s" -> %s'
                                  % (view.name, view.model, expression, error))
                except Exception as error:
                    broken.append('%s (%s): column_invisible="%s" -> %s: %s'
                                  % (view.name, view.model, expression,
                                     type(error).__name__, error))

        self.assertGreater(
            seen, 0, 'no view in this database sets column_invisible at all, '
                     'so this guard is not testing anything')
        self.assertFalse(
            broken, 'column_invisible is decided once for the whole list, '
                    'against a context that has no row fields in it:\n  '
                    + '\n  '.join(broken))

    def test_the_stock_source_columns_are_gated_on_the_order(self):
        """The columns that broke must now hang off the parent, not the line.

        A regression guard for the specific report from the field: the mixed
        basket order form showed the Stock Location and Available columns only
        for lines with a stock source, which `column_invisible` cannot express.
        """
        form = self.env.ref(
            'vuma_multi_tenant.view_sale_order_form_split')
        arch = etree.fromstring(form.arch_db)
        gated = {
            node.get('name'): node.get('column_invisible')
            for node in arch.iter()
            if node.get('column_invisible')
        }
        self.assertEqual(
            gated.get('vuma_source_location_id'),
            'not parent.vuma_is_mixed_basket')
        self.assertEqual(
            gated.get('vuma_source_available_qty'),
            'not parent.vuma_is_mixed_basket')
        self.assertTrue(
            self.env['sale.order']._fields['vuma_is_mixed_basket'],
            'the field the columns are gated on has gone')


class _PermissiveParent:
    """Stands in for the parent record inside the guarded expressions."""

    def __getattr__(self, name):
        return True