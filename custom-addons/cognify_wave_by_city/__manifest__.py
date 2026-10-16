{
    'name': 'Wave by City',
    'version': '1.0.0',
    'category': 'Inventory/Inventory',
    'summary': 'Adds a "City" grouping to automatic delivery batches and waves.',
    'description': """
Group delivery waves by the customer's delivery city
===================================================

Odoo can automatically batch and wave transfers grouped by contact,
destination **country**, carrier or storage location, but not by city. A
retailer delivering to several towns needs the wave to split by town so the
whole route for one city is picked together.

This adds a **City** option to the *Grouping by* list on the operation's
settings (Inventory > Configuration > Operations > Delivery Orders), next to
Contact and Destination Country.

A transfer is only merged into a wave whose existing transfers are all for the
same delivery city. A customer with no city on file is treated as its own
group, so those transfers never merge with a named city's wave.
""",
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['stock'],
    'data': [
        'views/stock_picking_type_views.xml',
    ],
    'installable': True,
    'application': False,
}