from markupsafe import Markup

from odoo import http
from odoo.http import request


class DashboardLandingController(http.Controller):
    @http.route('/_dashboard_landing', type='http', auth='public', website=True, csrf=False)
    def dashboard_landing(self, **kw):
        return request.redirect('/vuma')

    @http.route('/vuma', type='http', auth='public', website=True, csrf=False)
    def vuma_page(self, **kw):
        html = Markup(
            '<!doctype html><html style="height:100%;margin:0"><head>'
            '<meta charset="utf-8"/><title>Vuma</title>'
            '<style>html,body{height:100%;margin:0;overflow:hidden}iframe{width:100%;height:100%;border:0}</style>'
            '</head><body><iframe src="/odoo" allow="fullscreen"></iframe></body></html>'
        )
        return request.make_response(html, headers=[('Content-Type', 'text/html; charset=utf-8')])