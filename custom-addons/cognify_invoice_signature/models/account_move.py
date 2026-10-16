from odoo import _, api, fields, models
from odoo.exceptions import ValidationError


class AccountMove(models.Model):
    _inherit = 'account.move'

    # ------------------------------------------------------------- the signature
    signature = fields.Image(
        string='Customer Signature',
        copy=False,
        attachment=True,
        max_width=1024,
        max_height=1024,
        help='Drawn by the customer on the portal.',
    )
    signed_by = fields.Char(string='Signed By', copy=False, readonly=True)
    signed_on = fields.Datetime(string='Signed On', copy=False, readonly=True)

    # --------------------------------------------------------------- the request
    signature_requested = fields.Boolean(
        string='Customer Signature Requested',
        copy=False,
        help='Show the sign panel to the customer on their portal invoice page.',
    )
    signature_note = fields.Text(
        string='Signature Note',
        copy=False,
        help='Shown above the signature pad, e.g. "Please confirm receipt of '
             'these goods".',
    )

    # ------------------------------------------------------------------ helpers
    def _can_be_signed(self):
        """An invoice is signable when it is real, sent to the customer, and
        nobody has signed it yet.

        Rules, and why:

        * must be a customer invoice — a refund or a bill is not something the
          customer signs off on;
        * must be posted — signing a draft would let a customer "confirm"
          something that may still change before it is sent;
        * must be unpaid — once money has moved, a signature adds nothing;
        * must not already be signed — the first signature stands.
        """
        self.ensure_one()
        if self.move_type not in ('out_invoice', 'out_refund'):
            return False
        if self.state != 'posted':
            return False
        if self.payment_state in ('paid', 'in_payment', 'reconciled', 'paid_partly'):
            return False
        return not bool(self.signature)

    def action_request_signature(self):
        """Office-side: ask this customer to sign. Returns a portal action so
        the button can open their invoice straight away."""
        for move in self:
            if move.move_type not in ('out_invoice', 'out_refund'):
                raise ValidationError(
                    _('Only customer invoices can be sent for signature (%s).', move.display_name)
                )
            if move.state != 'posted':
                raise ValidationError(
                    _('%(name)s must be posted before you can request a signature.',
                      name=move.display_name)
                )
            if move.signature:
                raise ValidationError(
                    _('%(name)s is already signed by %(who)s.', name=move.display_name,
                      who=move.signed_by)
                )
            move.signature_requested = True
        return self._get_portal_sign_action()

    def action_open_signature_request(self):
        self.ensure_one()
        if not self.signature_requested:
            raise ValidationError(
                _('Tick "Customer Signature Requested" on %(name)s first.',
                  name=self.display_name)
            )
        return self._get_portal_sign_action()

    def action_revoke_signature(self):
        """Clear a signature so the customer can sign again."""
        for move in self:
            move.signature_requested = False
            move.write({'signature': False, 'signed_by': False, 'signed_on': False})
            move.message_post(
                body=_('Signature revoked; the customer may sign again.'),
                subtype_xmlid='mail.mt_note',
            )
        return True

    def _get_portal_sign_action(self):
        self.ensure_one()
        return {
            'type': 'ir.actions.act_window',
            'name': _('Send for Signature'),
            'res_model': 'account.move',
            'view_mode': 'form',
            'res_id': self.id,
            'target': 'current',
            'context': {
                'signature_portal': True,
                'invoice_portal_url': self._get_portal_url(),
            },
        }

    def _get_portal_url(self):
        """The customer's own invoice link, access token included.

        ``access_token`` is empty on a freshly created invoice; core only fills
        it in when a document is shared. ``_portal_ensure_token()`` generates it
        on demand, so the link we hand out always works.
        """
        self.ensure_one()
        token = self.sudo()._portal_ensure_token()
        base = self.access_url or '/my/invoices/%s' % self.id
        return '%s/sign?access_token=%s' % (base, token)

    def _prepare_invoice_signature(self, name, signature):
        """Validate and store a signature coming from the portal.

        Returns True. Raises on anything that should not be accepted, so the
        controller can turn it into a clean error message.
        """
        self.ensure_one()
        if not name or not str(name).strip():
            raise ValidationError(_('Please enter your name.'))
        if not signature:
            raise ValidationError(_('Please draw your signature before submitting.'))
        if not self._can_be_signed():
            raise ValidationError(
                _('%(name)s cannot be signed. It may already be signed or paid.',
                  name=self.display_name)
            )
        self.write({
            'signature': signature,
            'signed_by': str(name).strip(),
            'signed_on': fields.Datetime.now(),
            'signature_requested': False,
        })
        self.message_post(
            body=_('Signed online by %s.', str(name).strip()),
            message_type='notification',
            subtype_xmlid='mail.mt_note',
        )
        return True
