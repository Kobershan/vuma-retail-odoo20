{
    'name': 'Cognify Admin Shell',
    'version': '1.3.7',
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
* Quick Links: every menu and submenu carries a star. Starring one pins it to a
  Quick Links panel at the top of the sidebar, whatever tab it lives in; the
  shortcuts are stored per user in the browser.
* Five business tabs above the application list — Products, Sales, Debtors,
  Creditors and Accounting — presenting the day-to-day work (dashboards,
  profiles, stock takes, purchase orders, receiving, payments, age analysis,
  journals, expenses and reports) as flat, scannable links over the real Odoo
  menus.
* The grouped application list stays available under "All Apps" for anything
  the curated tabs do not cover.
* A search box in the sidebar filters both applications and their submenus by
  name, so any menu item can be reached directly.
* No chat facility: the Discuss application and its messaging systray, chat
  bubbles and chat windows are all hidden, while the message chatter stays
  available on documents for audit trails.
* The generic "Apps" menu is deactivated, so the navbar app dropdown lists only
  real applications and the redundant Apps entry no longer appears in it.
* The website footer is replaced. It used to show Odoo's American demo company
  ("8000 Marina Blvd ... United States", "+1 555-555-5556") on the login page,
  the shop and every other website page. It now renders the company name,
  address, phone and email from the company record, so the footer can no
  longer disagree with Settings > Companies.
* The website header's contact block and the contact form's phone placeholder
  are bound to the same company record, so the "+1 555-555-5556" that used to
  sit in the top bar of the shop is gone too.
* Restyled system bar: white topbar with readable icons, soft off-white canvas,
  charcoal primary buttons and marigold accents, sharper cards and control panel.
* Per-company background colours: each company has a colour picker in Settings >
  Companies. The colour is only painted on the logo band at the top of the
  sidebar; the main canvas stays white and the rest of the sidebar stays black.
  Multiple active companies get a visible two-colour gradient in that band, so
  mixed scopes are just as obvious.
* Branded login page shown with the Vuma Retail identity instead of the
  generic Odoo wrapper.
""",
    'author': 'Cognify',
    'website': 'https://cognify.example',
    'license': 'LGPL-3',
    'depends': ['web', 'mail', 'website', 'base_setup'],
    'data': [
        'data/hide_discuss_menu.xml',
        'data/hide_apps_menu.xml',
        'data/company_details.xml',
        'views/res_company_views.xml',
        'views/website_footer.xml',
        'views/website_header_contact.xml',
        'views/login_layout.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'cognify_admin_shell/static/src/xml/sidebar.xml',
            'cognify_admin_shell/static/src/xml/navbar.xml',
            'cognify_admin_shell/static/src/js/hide_chat.js',
            'cognify_admin_shell/static/src/js/company_theme.js',
            'cognify_admin_shell/static/src/js/sidebar.js',
            'cognify_admin_shell/static/src/js/navbar.js',
            'cognify_admin_shell/static/src/xml/company_scope_menu.xml',
            'cognify_admin_shell/static/src/js/company_scope_menu.js',
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
    'post_init_hook': 'post_init_hook',
    'application': True,
    'installable': True,
}