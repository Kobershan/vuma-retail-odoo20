{
    'name': 'Cognify Admin Shell',
    'version': '1.0.0',
    'category': 'Hidden',
    'summary': 'Charcoal administration dashboard shell: collapsible sidebar, redesigned admin chrome, no chat facility.',
    'description': """
A visual redesign of the Odoo web client for Vuma Retail, aimed at turning
the whole backend into a single coherent administration console.

Core features:

* A persistent, collapsible sidebar on the left. Every installed application is
  listed with its icon and grouped under clear labels (Sell, Stock, Money,
  People, Work, System); clicking an application selects it and reveals its
  submenus as an indented tree beneath it.
* A search box in the sidebar filters both applications and their submenus by
  name, so any menu item can be reached directly.
* No chat facility: the Discuss application and its messaging systray, chat
  bubbles and chat windows are all hidden, while the message chatter stays
  available on documents for audit trails.
* Restyled system bar: white topbar with readable icons, soft off-white canvas,
  charcoal primary buttons and marigold accents, sharper cards and control panel.
* Branded login page shown with the Vuma Retail identity instead of the
  generic Odoo wrapper.
""",
    'author': 'Cognify',
    'website': 'https://cognify.example',
    'license': 'LGPL-3',
    'depends': ['web', 'mail', 'website'],
    'data': [
        'data/hide_discuss_menu.xml',
        'views/login_layout.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cognify_admin_shell/static/src/xml/sidebar.xml',
            'cognify_admin_shell/static/src/xml/navbar.xml',
            'cognify_admin_shell/static/src/js/hide_chat.js',
            'cognify_admin_shell/static/src/js/sidebar.js',
            'cognify_admin_shell/static/src/js/navbar.js',
            'cognify_admin_shell/static/src/scss/backend.scss',
        ],
        'web.assets_frontend': [
            'cognify_admin_shell/static/src/scss/login.scss',
        ],
        'web._assets_primary_variables': [
            'cognify_admin_shell/static/src/scss/primary_variables.scss',
        ],
        'web.assets_web_dark': [
            'cognify_admin_shell/static/src/scss/backend.dark.scss',
        ],
    },
    'application': True,
    'installable': True,
}