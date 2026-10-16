from odoo import _
from odoo.exceptions import AccessError, MissingError, UserError, ValidationError
from odoo.http import request, route

from odoo.addons.portal.controllers.portal import CustomerPortal, pager as portal_pager


class InvoiceSignPortal(CustomerPortal):
    """Customer-facing endpoints for signing an invoice."""

    def _document_check_access(self, model_name, document_id, access_token=None):
        """Reuse Odoo's own token check so a signed invoice is no more or less
        private than the rest of the portal."""
        return super()._document_check_access(
            model_name, document_id, access_token=access_token
        )

    @route(['/my/invoices/<int:invoice_id>/sign'], type='http', auth='public',
           website=True)
    def portal_invoice_sign_page(self, invoice_id, access_token=None, **kwargs):
        """The signing page: signature pad, name field, submit."""
        access_token = access_token or request.httprequest.args.get('access_token')
        try:
            invoice_sudo = self._document_check_access(
                'account.move', invoice_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.not_found()

        values = self._prepare_invoice_sign_page_values(
            invoice_sudo, access_token=access_token
        )
        return request.render('cognify_invoice_signature.portal_invoice_sign_page', values)

    def _prepare_invoice_sign_page_values(self, invoice_sudo, access_token=None, **kwargs):
        invoice_sudo.ensure_one()
        values = {
            'page_name': 'invoice_sign',
            'invoice': invoice_sudo,
            'access_token': access_token or invoice_sudo.access_token,
            'can_be_signed': invoice_sudo._can_be_signed(),
            'signature_requested': invoice_sudo.signature_requested,
            'signature_note': invoice_sudo.signature_note,
            'error': kwargs.get('error'),
            'error_message': kwargs.get('error_message'),
        }
        if invoice_sudo.partner_id:
            values['partner_name'] = invoice_sudo.partner_id.name or ''
        return values

    @route('/my/invoices/<int:invoice_id>/sign/submit', type='jsonrpc',
           auth='public', website=True)
    def portal_invoice_sign_submit(self, invoice_id, access_token=None, name=None,
                                   signature=None, **kwargs):
        """Called by the stock portal.signature_form component with
        {name, signature}. Stores the signature and returns a friendly result."""
        # The signature widget posts the token as a query parameter, so accept it
        # from either place.
        access_token = access_token or request.httprequest.args.get('access_token')
        try:
            invoice_sudo = self._document_check_access(
                'account.move', invoice_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return {'error': _('This invoice link is no longer valid.')}

        try:
            invoice_sudo._prepare_invoice_signature(name, signature)
        except (ValidationError, UserError) as e:
            # Surface the message on the pad instead of a 500.
            return {'error': str(e)}

        self.env.cr.flush()
        return {
            'success': True,
            'message': _('Thank you. Your signature has been recorded.'),
            'redirect_url': '/my/invoices/%s?sign=ok' % invoice_sudo.id,
        }

    @route('/my/invoices/<int:invoice_id>/signature.pdf', type='http',
           auth='public', website=True)
    def portal_invoice_signed_pdf(self, invoice_id, access_token=None, **kwargs):
        """The invoice PDF, with the signature block when it is signed."""
        try:
            invoice_sudo = self._document_check_access(
                'account.move', invoice_id, access_token=access_token
            )
        except (AccessError, MissingError):
            return request.not_found()

        pdf_content, _ = self.env['ir.actions.report'].sudo()._render_qweb_pdf(
            'account.account_invoices', [invoice_sudo.id]
        )
        return request.make_response(pdf_content, headers=[
            ('Content-Type', 'application/pdf'),
            ('Content-Length', len(pdf_content)),
            ('Content-Disposition',
             'inline; filename="Invoice-%s.pdf"' % invoice_sudo.name.replace('/', '-')),
        ])
