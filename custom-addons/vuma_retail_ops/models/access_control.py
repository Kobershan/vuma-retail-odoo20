# -*- coding: utf-8 -*-
"""Per-user switchboard for the Vuma screens and the actions that move money.

Vuma ships eight role groups (owner, operations, finance, cashier, debtors,
creditors, driver, sales rep).  Those groups decide which records a person may
touch at all.  This file adds a second, finer layer that the business owner can
change from one screen without editing groups or code: a plain table of
``(user, function) -> allowed`` rows.

Two kinds of function exist:

``section``
    A screen.  Revoking it removes the matching menu, and everything below it,
    from that user's menu bar.
``action``
    A money-changing operation.  Revoking it makes the server refuse the
    operation, whatever the user's groups say.

Nobody is locked out on day one.  Every internal user is seeded with a row for
every function, allowed, on install and on upgrade, and new users are seeded
the same way.  Portal and public users are never seeded and never pass a
function check.

A missing row falls back to ``vuma.function.default_allowed``, which is True,
so adding a new function later cannot lock anybody out before the rows are
synced.
"""
import logging

from odoo import _, api, fields, models
from odoo.exceptions import AccessError

_logger = logging.getLogger(__name__)

SECTION = 'section'
ACTION = 'action'

# Raw SQL below reads these names directly, so they are spelled out once.  The
# many2many relation is named explicitly instead of relying on Odoo's default
# ``vuma_function_menu_ids_rel`` so the query cannot drift from the field.
FUNCTION_TABLE = 'vuma_function'
ACCESS_TABLE = 'vuma_function_access'
MENU_REL_TABLE = 'vuma_function_menu_rel'


def vuma_require(env, code, message=None):
    """Raise ``AccessError`` unless the current user holds ``code``."""
    if env.user.has_vuma_function(code):
        return
    raise AccessError(message or _(
        'Your Vuma access does not allow this action. Ask an administrator '
        'to grant it under Configuration & Audit > Function Access.'))


class VumaFunction(models.Model):
    _name = 'vuma.function'
    _description = 'Vuma Function'
    _order = 'category, sequence, name'
    _rec_name = 'name'

    code = fields.Char(
        string='Code', required=True, index=True,
        help='Stable identifier used in code, for example action.approve.')
    name = fields.Char(string='Function', required=True, translate=True)
    category = fields.Selection(
        selection=[(SECTION, 'Screen'), (ACTION, 'Action')],
        string='Kind', required=True, default=SECTION, index=True,
        help='A screen is a menu entry. An action is an operation the server '
             'refuses when it is revoked.')
    sequence = fields.Integer(default=10)
    description = fields.Text(translate=True)
    active = fields.Boolean(default=True)
    default_allowed = fields.Boolean(
        string='Allowed by Default', default=True,
        help='Used when a user has no row of their own yet. Keep this ticked '
             'so a newly added function cannot lock anybody out.')
    menu_ids = fields.Many2many(
        'ir.ui.menu', MENU_REL_TABLE, 'function_id', 'menu_id',
        string='Menus',
        help='Menus hidden from a user who does not hold this function. '
             'Everything below a hidden menu is hidden with it.')
    access_ids = fields.One2many(
        'vuma.function.access', 'function_id', string='User Access')

    _sql_constraints = [
        ('code_uniq', 'unique(code)', 'Each function code must be unique.'),
    ]

    @api.model
    def _blocked_menu_ids(self):
        """Menu ids the current user must not see.

        One query, because this runs on every menu load.  Menus are only ever
        blocked through a ``section`` function whose effective value for this
        user is False.
        """
        if 'vuma.function' not in self.env:
            return set()
        # ``env.user`` is sudoed, so this reads the user record rather than
        # the environment; testing ``env.su`` would block nothing.
        user = self.env.user
        if not user or not user._is_internal():
            return set()
        if user._is_system():
            return set()
        # The query below goes around the ORM, so it cannot see writes still
        # sitting in the write buffer. Ticking a box and reloading the menus
        # would otherwise still show the old tree, because ``write()`` only
        # stores the value in the cache until something flushes it.
        self.env['vuma.function.access'].flush_model(
            ['user_id', 'function_id', 'allowed'])
        self.env['vuma.function'].flush_model(
            ['active', 'category', 'default_allowed'])
        self.env.cr.execute(
            """
            SELECT rel.menu_id
              FROM %(rel)s rel
              JOIN %(function)s fn ON fn.id = rel.function_id
              LEFT JOIN %(access)s acc
                     ON acc.function_id = fn.id
                    AND acc.user_id = %%s
             WHERE fn.active IS TRUE
               AND fn.category = %%s
               AND COALESCE(acc.allowed, fn.default_allowed) IS FALSE
            """ % {
                'rel': MENU_REL_TABLE,
                'function': FUNCTION_TABLE,
                'access': ACCESS_TABLE,
            },
            (user.id, SECTION),
        )
        return {row[0] for row in self.env.cr.fetchall()}

    @api.model
    def _vuma_sync_access(self, users=None):
        """Create the missing access rows for ``users`` (all internal by default).

        Returns the number of rows created.  Existing rows are left alone so a
        deliberate revocation survives a sync.
        """
        Access = self.env['vuma.function.access'].sudo()
        if users is None:
            users = self.env['res.users'].sudo().with_context(
                active_test=False).search([('share', '=', False)])
        users = users.filtered(lambda user: not user.share)
        functions = self.sudo().with_context(active_test=False).search([])
        if not users or not functions:
            return 0
        existing = Access.search_read(
            [('user_id', 'in', users.ids)], ['user_id', 'function_id'])
        seen = {
            (row['user_id'][0], row['function_id'][0]) for row in existing
        }
        vals_list = []
        for user in users:
            for function in functions:
                if (user.id, function.id) in seen:
                    continue
                vals_list.append({
                    'user_id': user.id,
                    'function_id': function.id,
                    'allowed': function.default_allowed,
                })
        if not vals_list:
            return 0
        Access.create(vals_list)
        _logger.info(
            'Vuma function access: seeded %s rows for %s users',
            len(vals_list), len(users))
        return len(vals_list)

    def action_vuma_sync_all_users(self):
        created = self._vuma_sync_access()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('%s access rows created.', created),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class VumaFunctionAccess(models.Model):
    _name = 'vuma.function.access'
    _description = 'Vuma Function Access'
    _order = 'user_id, function_id'
    _rec_name = 'function_id'

    user_id = fields.Many2one(
        'res.users', string='User', required=True, index=True,
        ondelete='cascade')
    function_id = fields.Many2one(
        'vuma.function', string='Function', required=True, index=True,
        ondelete='cascade')
    allowed = fields.Boolean(
        string='Allowed', default=True,
        help='Untick to take this function away from this user only.')
    category = fields.Selection(
        related='function_id.category', string='Kind', readonly=True)
    function_code = fields.Char(
        related='function_id.code', string='Code', readonly=True)

    _sql_constraints = [
        ('user_function_uniq', 'unique(user_id, function_id)',
         'A user can only have one row per function.'),
    ]

    def write(self, vals):
        before = {}
        if 'allowed' in vals:
            before = {record.id: record.allowed for record in self}
        result = super().write(vals)
        for record in self:
            if record.id not in before or before[record.id] == record.allowed:
                continue
            self.env['vuma.audit.log'].sudo().log(
                record.user_id, 'override',
                note=_(
                    '%(state)s "%(function)s" for %(user)s',
                    state=_('Granted') if record.allowed else _('Revoked'),
                    function=record.function_id.name,
                    user=record.user_id.name,
                ))
        return result

    def unlink(self):
        for record in self:
            self.env['vuma.audit.log'].sudo().log(
                record.user_id, 'override',
                note=_('Removed the "%(function)s" access row',
                       function=record.function_id.name))
        return super().unlink()


class ResUsers(models.Model):
    _inherit = 'res.users'

    vuma_function_access_ids = fields.One2many(
        'vuma.function.access', 'user_id', string='Vuma Functions',
        groups='base.group_system')
    vuma_function_count = fields.Integer(
        string='Functions Granted', compute='_compute_vuma_function_stats',
        compute_sudo=True, groups='base.group_system')
    vuma_function_denied_count = fields.Integer(
        string='Functions Revoked', compute='_compute_vuma_function_stats',
        compute_sudo=True, groups='base.group_system')

    @api.depends('vuma_function_access_ids.allowed',
                 'vuma_function_access_ids.function_id')
    def _compute_vuma_function_stats(self):
        for user in self:
            rows = user.vuma_function_access_ids
            user.vuma_function_count = len(rows.filtered('allowed'))
            user.vuma_function_denied_count = len(rows.filtered(
                lambda row: not row.allowed))

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        # A fresh module load creates users before ``vuma_function`` exists;
        # the guard keeps that path quiet while still seeding real new users.
        if 'vuma.function' in self.env:
            internal = users.filtered(lambda user: not user.share)
            if internal:
                self.env['vuma.function'].sudo()._vuma_sync_access(internal)
        return users

    def has_vuma_function(self, code):
        """True when this user may use ``code``.

        Superuser and Settings administrators always pass.  Portal and public
        users never do.  An unknown code passes, so a typo in a guard cannot
        lock a user out of a working screen.

        Every test below reads the user record, never the environment.
        ``env.user`` is a *sudoed* browse (``Environment.user`` is built with
        ``su=True``), so testing ``env.su`` here would answer "yes" for every
        user on every request and the guard would never fire.
        """
        self.ensure_one()
        if self._is_superuser():
            return True
        if self._is_system():
            return True
        if not self._is_internal():
            return False
        function = self.env['vuma.function'].sudo().search(
            [('code', '=', code)], limit=1)
        if not function:
            return True
        row = self.env['vuma.function.access'].sudo().search([
            ('user_id', '=', self.id),
            ('function_id', '=', function.id),
        ], limit=1)
        if row:
            return row.allowed
        return function.default_allowed

    def _vuma_check_admin(self):
        if not self.env.user.has_group('base.group_system'):
            raise AccessError(_(
                'Only a Settings administrator can change Vuma access.'))

    def action_vuma_grant_all_functions(self):
        self._vuma_check_admin()
        self.env['vuma.function'].sudo()._vuma_sync_access(self)
        self.env['vuma.function.access'].sudo().search(
            [('user_id', 'in', self.ids)]).write({'allowed': True})
        return True

    def action_vuma_revoke_all_functions(self):
        self._vuma_check_admin()
        self.env['vuma.function'].sudo()._vuma_sync_access(self)
        self.env['vuma.function.access'].sudo().search(
            [('user_id', 'in', self.ids)]).write({'allowed': False})
        return True

    def action_vuma_sync_functions(self):
        self._vuma_check_admin()
        created = self.env['vuma.function'].sudo()._vuma_sync_access(self)
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _('%s access rows created.', created),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    @api.model
    def _visible_menu_ids(self, debug=False):
        """Drop the menus whose section function this user does not hold.

        The parent keeps its group-based cache; this filter runs on top of it
        so an access change takes effect on the next page load without any
        cache invalidation.
        """
        visible = super()._visible_menu_ids(debug=debug)
        blocked = self.env['vuma.function']._blocked_menu_ids()
        if not visible or not blocked:
            return visible
        parent_of = {
            menu.id: menu.parent_id.id
            for menu in self.sudo().search([], order='id')
        }
        keep = set()
        for menu_id in visible:
            current = menu_id
            seen = set()
            while current and current not in seen:
                if current in blocked:
                    break
                seen.add(current)
                current = parent_of.get(current)
            else:
                keep.add(menu_id)
        return frozenset(keep)


class VumaAuditMixin(models.AbstractModel):
    _inherit = 'vuma.audit.mixin'

    def unlink(self):
        vuma_require(
            self.env, 'action.delete',
            _('You are not allowed to delete Vuma documents.'))
        return super().unlink()


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    def write(self, vals):
        # ``vuma_pricing_solved`` marks the pricing tab's own write-back: when
        # a cost or a tax moves, the solver recomputes the other three figures
        # and stores the new sales price. That is arithmetic following a change
        # the user *was* allowed to make, not a person typing a price, so it
        # must not be refused here. The constant is spelled out rather than
        # imported because ``product_pricing`` loads after this file.
        if 'list_price' in vals and not self.env.context.get(
                'vuma_pricing_solved'):
            vuma_require(
                self.env, 'action.price_edit',
                _('You are not allowed to change selling prices.'))
        return super().write(vals)


class ResPartner(models.Model):
    _inherit = 'res.partner'

    def write(self, vals):
        if 'credit_limit' in vals:
            vuma_require(
                self.env, 'action.credit_limit',
                _('You are not allowed to change credit limits.'))
        return super().write(vals)
