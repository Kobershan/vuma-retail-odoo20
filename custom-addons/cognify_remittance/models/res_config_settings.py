from odoo import api, fields, models

PARAM_PREFIX = 'cognify_remittance.'


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    remittance_journal_id = fields.Many2one(
        'account.journal',
        string='Remittance Journal',
        domain=[('type', '=', 'general')],
        help='Journal used for verified remittance entries. '
             'Defaults to the first miscellaneous journal.',
    )
    remittance_cash_account_id = fields.Many2one(
        'account.account',
        string='Cash Returned Account',
        help='Account debited with the net cash a rep hands back to the office.',
    )

    # Mapping of setting field -> model used to resolve its stored id.
    _PARAM_MODELS = {
        'remittance_journal_id': 'account.journal',
        'remittance_cash_account_id': 'account.account',
    }

    @api.model
    def get_remittance_config(self):
        """Return the configured journal and cash account.

        Reads only the committed ``ir.config_parameter`` values. Deliberately
        NOT reading a pending ``res.config.settings`` record: those are
        transient and can linger with stale values, which would silently post
        a remittance to the wrong cash account. ``set_values()`` already writes
        the parameters when the settings form is saved, so the parameters are
        always the authoritative source for the accounting path.
        """
        params = self.env['ir.config_parameter'].sudo()
        result = {}
        for field_name, model_name in self._PARAM_MODELS.items():
            raw = params.get_int(PARAM_PREFIX + field_name)
            result[field_name] = self.env[model_name].browse(raw) if raw else False
        return result

    def set_values(self):
        super().set_values()
        params = self.env['ir.config_parameter'].sudo()
        for field_name in self._PARAM_MODELS:
            value = getattr(self, field_name, False)
            params.set_int(PARAM_PREFIX + field_name, value.id or 0)
