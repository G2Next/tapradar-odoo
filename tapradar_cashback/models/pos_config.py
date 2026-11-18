from odoo import models


class PosConfig(models.Model):
    _inherit = "pos.config"

    def use_coupon_code(self, code, creation_date, partner_id, pricelist_id):
        """Explain clearly why a campaign gift card is refused during the campaign.

        Odoo already refuses it (the program only starts the day after the
        campaign), but with a generic "invalid coupon" message.
        """
        card = self.env["loyalty.card"].sudo().search(
            [("code", "=", code), ("tapradar_campaign_id", "!=", False)], limit=1
        )
        if card:
            message = card.tapradar_campaign_id._redemption_block_message()
            if message:
                return {"successful": False, "payload": {"error_message": message}}
        return super().use_coupon_code(code, creation_date, partner_id, pricelist_id)
