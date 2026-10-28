# -*- coding: utf-8 -*-
"""Give every remaining stock product a department.

The 1.2.0 migration creates departments from sensible top-level categories.
Some products sit in company buckets such as "Trade Rite Products" or
"Shared Products", which are not departments.  Leaving them without a
department would break department-level stock reporting, so any stock
product still unassigned is placed in a shared "General" department.  Users
can move it afterwards; existing assignments are never overwritten.

Service products are left alone: they do not carry stock.
"""
from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    if not version:
        return

    env = api.Environment(cr, SUPERUSER_ID, {})
    if 'vuma.department' not in env:
        return

    Template = env['product.template']
    products = Template.search([
        ('type', '=', 'consu'),
        ('vuma_department_id', '=', False),
    ])
    if not products:
        return

    Department = env['vuma.department'].with_context(active_test=False)
    department = Department.search([('name', '=', 'General')], limit=1)
    if not department:
        department = Department.create({
            'name': 'General',
            'note': 'Fallback department for stock products that did not '
                    'match a catalogue category.',
        })
    products.write({'vuma_department_id': department.id})
