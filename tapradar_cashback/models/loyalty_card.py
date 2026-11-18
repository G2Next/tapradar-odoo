from odoo import api, fields, models

TRACKED_FIELDS = {"points", "expiration_date", "code", "partner_id", "tapradar_campaign_id"}


class LoyaltyCard(models.Model):
    _inherit = "loyalty.card"

    tapradar_campaign_id = fields.Many2one(
        "tapradar.cashback.campaign",
        string="Cashback Campaign",
        index=True,
        readonly=True,
        ondelete="set null",
        help="Cashback campaign that issued this gift card.",
    )

    def _tapradar_status(self, today=None):
        self.ensure_one()
        today = today or fields.Date.context_today(self)
        if self.points <= 0.004:
            return "used"
        if self.expiration_date and self.expiration_date < today:
            return "expired"
        return "ready"

    def _tapradar_payload(self, deleted=False):
        """What TapRadar stores about a gift card. No names: only the customer number."""
        self.ensure_one()
        campaign = self.tapradar_campaign_id
        valid_until = self.expiration_date or campaign.valid_until
        return {
            "external_id": str(self.id),
            "customer_ref": (self.partner_id.commercial_partner_id.ref or "").strip(),
            "code": self.code,
            "title": campaign.name,
            "balance": 0.0 if deleted else round(max(self.points, 0.0), 2),
            "currency": campaign.currency_id.name or "EUR",
            "valid_from": fields.Date.to_string(campaign.valid_from) if campaign.valid_from else None,
            "valid_until": fields.Date.to_string(valid_until) if valid_until else None,
            "status": "expired" if deleted else self._tapradar_status(),
            "timezone": campaign.tz or "Europe/Vienna",
        }

    def _tapradar_enqueue(self, deleted=False):
        payloads = [
            (card.tapradar_campaign_id.company_id, card, card._tapradar_payload(deleted=deleted))
            for card in self.sudo()
            if card.tapradar_campaign_id and card.partner_id.commercial_partner_id.ref
        ]
        if payloads:
            self.env["tapradar.cashback.event"]._enqueue(payloads)

    @api.model_create_multi
    def create(self, vals_list):
        cards = super().create(vals_list)
        cards.filtered("tapradar_campaign_id")._tapradar_enqueue()
        return cards

    def write(self, vals):
        res = super().write(vals)
        if TRACKED_FIELDS & set(vals):
            self.filtered("tapradar_campaign_id")._tapradar_enqueue()
        return res

    def unlink(self):
        # Removed during the campaign (fully refunded): TapRadar hides it.
        self.filtered("tapradar_campaign_id")._tapradar_enqueue(deleted=True)
        return super().unlink()
