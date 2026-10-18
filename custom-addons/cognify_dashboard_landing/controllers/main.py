from odoo import http
from odoo.http import request


class DashboardLandingController(http.Controller):
    @http.route('/_dashboard_landing', type='http', auth='public', website=True, csrf=False)
    def dashboard_landing(self, **kw):
        # /vuma is now the backend web client itself; the old iframe page is gone.
        return request.redirect('/vuma')
