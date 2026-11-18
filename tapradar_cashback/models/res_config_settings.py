from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    tapradar_api_key = fields.Char(related="company_id.tapradar_api_key", readonly=False, groups="base.group_system")
    tapradar_data_consent = fields.Boolean(related="company_id.tapradar_data_consent", readonly=False)
    tapradar_receipt_qr = fields.Boolean(related="company_id.tapradar_receipt_qr", readonly=False)
    tapradar_shop_name = fields.Char(related="company_id.tapradar_shop_name")
    tapradar_integration_id = fields.Char(related="company_id.tapradar_integration_id")

    def action_tapradar_connect(self):
        self.execute()
        return self.company_id.action_tapradar_connect()

    def action_tapradar_disconnect(self):
        return self.company_id.action_tapradar_disconnect()
