# -*- coding: utf-8 -*-
"""Create stock departments from the existing product category tree.

The Department field is new.  Rather than leaving every existing product
without a department, this migration turns the top-level catalogue
categories into departments and assigns each product to the department of
its root category.  Categories that are really company or accounting
buckets (Goods, Services, "Trade Rite Products", ...) are skipped.

Existing department names are re-used, so running the migration more than
once is safe.  A department is only assigned where ``vuma_department_id`` is
still empty; anything a user has already chosen is left untouched.
"""
from odoo import SUPERUSER_ID, api

# Categories that describe what an item is for accounting or which company
# owns it, rather than the shelf / department it sits on.
SKIPPED_EXACT = {
    'Goods',
    'Expenses',
    'Services',
    'Deliveries',
    'Donations',
}
SKIPPED_PARTS = (
    'Products',
    'Product',
)


def _looks_like_company_bucket(name):
    if not name:
        return True
    if name in SKIPPED_EXACT:
        return True
    return any(part in name for part in SKIPPED_PARTS)


def _category_name(category):
    name = category.name
    if isinstance(name, dict):
        name = name.get('en_US') or next(iter(name.values()), '')
    return (name or '').strip()


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'vuma.department' not in env:
        return

    Department = env['vuma.department']
    Template = env['product.template']

    # Work with the real category tree rather than a translated display
    # name, so parent/child traversal is stable.
    for category in env['product.category'].search([]):
        root = category
        while root.parent_id:
            root = root.parent_id
        name = _category_name(root)
        if _looks_like_company_bucket(name):
            continue

        department = Department.with_context(active_test=False).search(
            [('name', '=', name)], limit=1)
        if not department:
            department = Department.create({
                'name': name,
                'active': True,
            })

        templates = Template.search([
            ('categ_id', 'child_of', root.id),
            ('vuma_department_id', '=', False),
        ])
        if templates:
            templates.write({'vuma_department_id': department.id})
