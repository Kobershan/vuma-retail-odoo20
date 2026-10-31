# -*- coding: utf-8 -*-
"""Post-install hooks: seed demo products (+ real stock) and install the ACLs.

ACLs are created in Python (not ir.access.csv) because Odoo 20 reflects the
module's own models into ``ir.model`` during registry setup; doing it here
guarantees those rows exist before the access rows referencing them.
"""
from odoo import _, fields


def _vuma_companies(env):
    companies = env['res.company'].sudo().search([
        '|', ('name', '=', 'Trade Rite'), ('name', '=', 'Siyaya DC'),
    ])
    return (
        companies.filtered(lambda c: c.name == 'Trade Rite')[:1],
        companies.filtered(lambda c: c.name == 'Siyaya DC')[:1],
    )


# ------------------------------------------------------------------
# ACLs
# ------------------------------------------------------------------
ACCESS_MODEL = 'ir.access'


def _create_access(env, name, model_name, xml_group, operation, domain):
    model = env['ir.model'].sudo().search([('model', '=', model_name)], limit=1)
    if not model:
        return
    group = env.ref(xml_group, None) if xml_group else None
    existing = env['ir.access'].sudo().search([
        ('model_id', '=', model.id),
        ('group_id', '=', group.id if group else False),
        ('operation', '=', operation),
    ], limit=1)
    if existing:
        return
    env['ir.access'].sudo().create({
        'name': name,
        'model_id': model.id,
        'group_id': group.id if group else None,
        'operation': operation,
        'domain': domain or '',
    })


def install_acls(env):
    model = 'vuma_multi_tenant'
    _create_access(env, 'Own cash-collection sessions (read)',
                   'vuma.payment.session', f'{model}.group_vuma_mixed_basket_user', 'r',
                   "[('user_id', '=', user.id)]")
    _create_access(env, 'Own cash-collection sessions (change)',
                   'vuma.payment.session', f'{model}.group_vuma_mixed_basket_user', 'cud',
                   "[('user_id', '=', user.id)]")
    _create_access(env, 'All cash-collection sessions (manager)',
                   'vuma.payment.session', f'{model}.group_vuma_mixed_basket_manager', 'crud', '')
    _create_access(env, 'Own cash-collection session lines',
                   'vuma.payment.session.line', f'{model}.group_vuma_mixed_basket_user', 'crud',
                   "[('session_id.user_id', '=', user.id)]")
    _create_access(env, 'All cash-collection session lines (manager)',
                   'vuma.payment.session.line', f'{model}.group_vuma_mixed_basket_manager', 'crud', '')
    _create_access(env, 'Cross-company credit wizard',
                   'vuma.partner.credit.wizard', f'{model}.group_vuma_mixed_basket_user', 'crud', '')
    # NOTE: the stock-traceability audit is NOT granted here. ir.access created
    # in a post_init_hook only exists for databases installed while the hook
    # contained it: the hook never runs again on -u, so a model added later
    # ended up with no access row at all. Odoo then refuses to read it and
    # silently drops its menu ("vuma.stock.trace" was invisible). Its rules
    # live in security/ir.access.csv, which is loaded on install AND upgrade.


# ------------------------------------------------------------------
# Stock-source backfill
# ------------------------------------------------------------------
def backfill_stock_sources(env):
    """Give every existing order line a stock-source company.

    Before this module auto-resolved the field, it was only ever set by the
    UI onchange, so every line created through RPC, an import or a data load
    kept a blank "Stock Source" -- and a mixed basket that could not be split
    without guessing which company owned the stock.
    """
    SOL = env['sale.order.line'].sudo()
    lines = SOL.search([('product_id', '!=', False),
                        ('vuma_source_company_id', '=', False)])
    if not lines:
        return 0
    lines._vuma_apply_source_company()
    return len(lines)


# ------------------------------------------------------------------
# Chart of accounts
# ------------------------------------------------------------------
def _ensure_chart_template(env, company):
    """Load the South African chart of accounts into the company if missing.

    Both trading companies need real accounts (receivable, income, VAT) so
    split quotes can be invoiced and cash collections posted.
    """
    company = company.sudo()
    if company.chart_template:
        return
    env['account.chart.template'].sudo().try_loading(
        'za', company, install_demo=False)


def _ensure_invoice_prefix(env, company, prefix):
    """Invoices are numbered from the SALE journal code: set it so each
    company invoices as TR/INV/... resp. SY/INV/..."""
    journal = env['account.journal'].sudo().search([
        ('company_id', '=', company.id),
        ('type', '=', 'sale'),
    ], limit=1)
    if journal and journal.code != prefix:
        journal.code = prefix


def _ensure_cash_journal(env, company):
    """A dedicated CASH journal per company for field collections.

    The SA chart ships bank/sale/purchase journals only; field reps collect
    cash, so each company gets its own cash journal (using the chart's bank
    account as its default account).
    """
    Journal = env['account.journal'].sudo()
    journal = Journal.search([
        ('company_id', '=', company.id),
        ('type', '=', 'cash'),
    ], limit=1)
    if journal:
        return journal
    bank = Journal.search([
        ('company_id', '=', company.id),
        ('type', '=', 'bank'),
    ], limit=1)
    vals = {
        'name': _('%s - Cash') % company.name,
        'code': 'CSH',
        'type': 'cash',
        'company_id': company.id,
    }
    if bank and bank.default_account_id:
        vals['default_account_id'] = bank.default_account_id.id
    return Journal.create(vals)


# ------------------------------------------------------------------
# Stock seeding
# ------------------------------------------------------------------
def _seed_quant(env, company, product_tmpl, qty):
    """Apply an inventory adjustment increasing on-hand in the company's
    own warehouse stock location."""
    product = product_tmpl.product_variant_id
    if not product:
        return
    Quant = env['stock.quant'].sudo()
    wh = env['stock.warehouse'].sudo().search(
        [('company_id', '=', company.id)], limit=1)
    if not wh:
        return
    quant = Quant.search([
        ('product_id', '=', product.id),
        ('location_id', '=', wh.lot_stock_id.id),
        ('company_id', '=', company.id),
    ], limit=1)
    if quant:
        quant.inventory_quantity = (quant.inventory_quantity or 0.0) + qty
        quant.action_apply_inventory()
    else:
        new_quant = Quant.create({
            'product_id': product.id,
            'location_id': wh.lot_stock_id.id,
            'inventory_quantity': qty,
        })
        new_quant.action_apply_inventory()


def grant_mixed_basket_access(env, only_logins=None):
    """Give the Mixed Basket group AND both trading companies to the right users.

    Granting only ``company_ids`` is not enough. Every cross-company menu and
    action is gated on ``group_vuma_mixed_basket_user``, so a user who can
    reach the two companies but not the group still sees a plain single-company
    Odoo and no sign that the feature exists.

    The user's current company must stay in the list as well: Odoo rejects a
    user whose ``company_id`` is not among ``company_ids`` (see
    ``_check_user_company``), which is how the record rule resolves the
    current company.

    :param only_logins: restrict the grant to these logins; ``None`` means
        "everyone who is meant to use it" (admins + users explicitly flagged
        with ``vuma_mixed_basket_access``).
    :returns: the users that were updated
    """
    group = env.ref('vuma_multi_tenant.group_vuma_mixed_basket_user', False)
    trade, siyaya = _vuma_companies(env)
    main = env.ref('base.main_company', None)
    target_companies = trade | siyaya | (main if main else env['res.company'].browse())
    if not target_companies:
        return env['res.users'].browse()

    Users = env['res.users'].sudo().with_context(active_test=False)
    if only_logins:
        users = Users.search([('login', 'in', list(only_logins))])
    else:
        users = (env.ref('base.user_admin', False) or Users.browse())
        users |= env.ref('base.user_root', False) or Users.browse()
        # Users an administrator has explicitly marked as mixed-basket users.
        users |= Users.search([('vuma_mixed_basket_access', '=', True)])
    users = users.filtered('login')
    if not users:
        return Users.browse()

    # Work out the deltas first: writing company_ids/group_ids on a user with a
    # current company that would be dropped raises, so never narrow the set.
    for user in users:
        vals = {}
        missing_companies = target_companies - user.company_ids
        if missing_companies:
            vals['company_ids'] = [fields.Command.link(c.id) for c in missing_companies]
        if group and group not in user.group_ids:
            vals['group_ids'] = [fields.Command.link(group.id)]
        if not vals:
            continue
        if user.company_id and user.company_id not in (
                user.company_ids | target_companies):
            # keep the current company reachable
            vals['company_ids'] = [
                fields.Command.link(c.id) for c in (user.company_ids | target_companies)
            ]
        user.write(vals)
    return users


# ------------------------------------------------------------------
# Staff administrators
# ------------------------------------------------------------------
# Shop-floor logins that run the backend. The passwords are only applied
# when a login is first created: a password the person has since changed
# is never reset by a replay of the seed.
STAFF_ADMINS = [
    # (login, display name, password)
    ('traderite.dc', 'Trade Rite DC', '0757918931'),
    ('junaid', 'Junaid', '0783331297'),
    ('augustin', 'Augustin', '0680167091'),
]


def seed_staff_admins(env):
    """Create the staff administrators (idempotent).

    Each gets the Settings group. The Mixed Basket flag is set as well, so
    ``grant_mixed_basket_access`` gives them the Mixed Basket group and both
    trading companies on the same pass.

    :returns: the users that were created or updated
    """
    settings = env.ref('base.group_system', False)
    main = env.ref('base.main_company', None)
    Users = env['res.users'].sudo().with_context(active_test=False)
    touched = Users.browse()
    for login, name, password in STAFF_ADMINS:
        user = Users.search([('login', '=', login)], limit=1)
        if not user:
            vals = {
                'login': login,
                'name': name,
                'password': password,
                'vuma_mixed_basket_access': True,  # adds group + trading companies
            }
            if settings:
                vals['group_ids'] = [fields.Command.link(settings.id)]
            if main:
                vals['company_id'] = main.id
                vals['company_ids'] = [fields.Command.link(main.id)]
            touched |= Users.create(vals)
        else:
            vals = {}
            if settings and settings not in user.group_ids:
                vals['group_ids'] = [fields.Command.link(settings.id)]
            if not user.vuma_mixed_basket_access:
                vals['vuma_mixed_basket_access'] = True
            if vals:
                user.write(vals)
            touched |= user
    # Guarantee group + both trading companies even when the flag was already
    # set before this seed ran.
    touched |= grant_mixed_basket_access(
        env, only_logins=[login for login, _n, _p in STAFF_ADMINS])
    return touched


def open_both_catalogues(env):
    """Let staff see BOTH trading catalogues whatever the company switcher says.

    Odoo 20 keeps record rules in ``ir.access`` with ``kind='restriction'``,
    which is derived: ``group_id`` set means "permission", ``group_id`` empty
    means "restriction". Restrictions are AND-ed, so a rule that *excludes*
    another company's products can only be relaxed by widening that same rule,
    never by adding a second one.

    The stock rule is::

        ['|', ('company_id', '=', False),
              ('company_id', 'parent_of', company_ids)]

    ``company_ids`` there is ``env.companies`` -- the companies the *switcher*
    has active, not the user's settings. Two things then break a rep who has
    both companies configured:

    * a fresh login starts on the single default company, because with no
      ``cids`` cookie ``web/static/src/core/user.js`` falls back to
      ``[defaultCompany]``, so only the shared catalogue is listed;
    * worse, the sale product configurator does
      ``request.update_context(allowed_company_ids=[company_id])`` with the
      *quote's* company before reading the template, so clicking a Trade Rite
      product on a Siyaya quote raised "Administrator doesn't have 'read'
      access to Product ... you might be able to access the record by
      switching to the company: Siyaya DC".

    Widening the rule to also accept the two trading companies makes both
    catalogues readable no matter which single company happens to be active,
    which is exactly the promise of a mixed basket. It is deliberately global
    rather than group-scoped: Odoo 20 cannot express a group-scoped
    restriction, and every internal user here needs to see both catalogues to
    serve a customer.

    :returns: the rules that were widened
    """
    trade, siyaya = _vuma_companies(env)
    if not trade or not siyaya:
        return env['ir.access'].browse()
    Access = env['ir.access'].sudo()
    wanted = "['|', '|', ('company_id', '=', False), " \
             "('company_id', 'parent_of', company_ids), " \
             "('company_id', 'in', %s)]" % [trade.id, siyaya.id]
    widened = Access.browse()
    for model in ('product.template', 'product.product'):
        model_rec = env['ir.model'].sudo().search([('model', '=', model)], limit=1)
        if not model_rec:
            continue
        rules = Access.search([
            ('model_id', '=', model_rec.id),
            ('group_id', '=', False),
            ('active', '=', True),
        ])
        for rule in rules:
            domain = str(rule.domain or '')
            if 'parent_of' not in domain or 'company_ids' not in domain:
                continue
            if domain == wanted:
                continue
            rule.domain = wanted
            widened |= rule
    return widened


def vuma_post_init(env):
    trade, siyaya = _vuma_companies(env)
    if not trade or not siyaya:
        return

    # Real chart of accounts in both companies (needed for invoicing).
    _ensure_chart_template(env, trade)
    _ensure_chart_template(env, siyaya)

    # Cash journals for field collections (one per company).
    _ensure_cash_journal(env, trade)
    _ensure_cash_journal(env, siyaya)

    # Company-specific invoice numbering: TR/INV/..., SY/INV/...
    _ensure_invoice_prefix(env, trade, 'TR/INV')
    _ensure_invoice_prefix(env, siyaya, 'SY/INV')

    seeds = [
        # Trade Rite exclusive stock
        ('vuma_multi_tenant.product_trade_rite_cement', trade, 500),
        ('vuma_multi_tenant.product_trade_rite_bricks', trade, 50),
        ('vuma_multi_tenant.product_trade_rite_sand', trade, 200),
        ('vuma_multi_tenant.product_trade_rite_steel', trade, 300),
        ('vuma_multi_tenant.product_trade_rite_paint', trade, 100),
        # Siyaya DC exclusive stock
        ('vuma_multi_tenant.product_siyaya_cement', siyaya, 1000),
        ('vuma_multi_tenant.product_siyaya_aggregate', siyaya, 500),
        ('vuma_multi_tenant.product_siyaya_blocks', siyaya, 2000),
        ('vuma_multi_tenant.product_siyaya_waterproof', siyaya, 150),
        ('vuma_multi_tenant.product_siyaya_admixture', siyaya, 80),
        # Shared products: BOTH warehouses hold stock
        ('vuma_multi_tenant.product_shared_safety', trade, 200),
        ('vuma_multi_tenant.product_shared_safety', siyaya, 150),
        ('vuma_multi_tenant.product_shared_gloves', trade, 300),
        ('vuma_multi_tenant.product_shared_gloves', siyaya, 250),
    ]
    for xmlid, company, qty in seeds:
        product = env.ref(xmlid, None)
        if not product:
            continue
        _seed_quant(env, company, product.sudo(), qty)

    seed_staff_admins(env)

    grant_mixed_basket_access(env)

    install_acls(env)
    open_both_catalogues(env)
    backfill_stock_sources(env)
