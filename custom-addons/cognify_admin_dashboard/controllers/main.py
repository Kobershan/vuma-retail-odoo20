import json

from odoo import http
from odoo.http import request

HIDDEN_XML = {"mail.menu_root_discuss"}


class VumaDashboardController(http.Controller):
    @http.route('/vuma', type='http', auth='user', website=True, sitemap=False)
    def vuma_dashboard(self, **kw):
        menus = request.env['ir.ui.menu'].search(
            [('parent_id', '=', False), ('active', '=', True)],
            order='sequence, id',
        )

        tiles = []
        for menu in menus:
            try:
                xmlid = menu.get_external_id().get(menu.id)
            except Exception:
                xmlid = None
            if xmlid in HIDDEN_XML:
                continue
            if not menu.action:
                # Skip pure folders without a runnable action.
                continue
            action_id = menu.action.id
            tiles.append({
                'id': menu.id,
                'xmlid': xmlid,
                'name': menu.name,
                'sequence': menu.sequence or 0,
                'icon_url': '/web/image/ir.ui.menu/{}/web_icon_data'.format(menu.id),
                'href': '/odoo/action-{}'.format(action_id),
            })

        # Keep a consistent, scannable order (by sequence then name).
        tiles.sort(key=lambda t: (t['sequence'], t['name'] or ''))

        return request.render('cognify_admin_dashboard.dashboard_page', {
            'title': 'Vuma Admin',
            'tiles': tiles,
            'user': request.env.user,
            'company': request.env.company,
        })
