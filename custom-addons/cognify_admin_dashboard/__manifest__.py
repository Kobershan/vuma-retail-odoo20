{
    'name': 'Cognify Admin Dashboard',
    'version': '1.0.0',
    'category': 'Hidden',
    'summary': 'Standalone administration dashboard at /vuma linking to every module. No web-client hacks.',
    'author': 'Cognify',
    'license': 'LGPL-3',
    'depends': ['web'],
    'data': [
        'views/dashboard_templates.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'cognify_admin_dashboard/static/src/js/dashboard.js',
            'cognify_admin_dashboard/static/src/css/dashboard.css',
        ],
    },
    'installable': True,
}