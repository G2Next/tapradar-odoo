from odoo import api, fields, models

TRACKED_FIELDS = {"points", "expiration_date", "code", "partner_id", "tapradar_campaign_id"}
LOYALTY_FIELDS = {"points", "partner_id", "program_id"}
MAX_REWARDS = 10


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
            "customer_ref": self.partner_id._tapradar_customer_ref(),
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
            if card.tapradar_campaign_id and card.partner_id
        ]
        if payloads:
            self.env["tapradar.cashback.event"]._enqueue(payloads)

    # --- Loyalty points ("Treuekarte") --------------------------------------
    # The balance of the shop's own loyalty programs is shown on the customer's
    # TapRadar card when the shop turned it on (res.company.tapradar_loyalty).
    # Points are still earned and spent only in Odoo.

    def _tapradar_loyalty_cards(self):
        return self.filtered(
            lambda card: card.program_id.program_type == "loyalty"
            and card.partner_id
            and not card.tapradar_campaign_id
        )

    def _tapradar_loyalty_payload(self, deleted=False):
        """What TapRadar stores about a loyalty card. No names: only the customer number."""
        self.ensure_one()
        company = self.company_id or self.env.company
        # Names in the shop's language, whoever triggered the update.
        program = self.program_id.with_context(lang=company.partner_id.lang or self.env.lang)
        active = not deleted and program.active
        rewards = program.reward_ids.filtered(lambda r: r.active and r.description).sorted("required_points")
        return {
            "external_id": str(self.id),
            "customer_ref": self.partner_id._tapradar_customer_ref(),
            "program": program.name,
            "points": round(max(self.points, 0.0), 2) if active else 0.0,
            "unit": program.portal_point_name or "Points",
            "rewards": [
                {"description": reward.description, "points": round(reward.required_points, 2)}
                for reward in rewards[:MAX_REWARDS]
            ] if active else [],
            "active": active,
        }

    def _tapradar_enqueue_loyalty(self, deleted=False, force=False):
        """`force`: also for shops that turned it off (to hide the points in the app)."""
        payloads = []
        for card in self.sudo()._tapradar_loyalty_cards():
            company = card.company_id or self.env.company
            if company.tapradar_loyalty or force:
                payloads.append((company, card, card._tapradar_loyalty_payload(deleted=deleted)))
        if payloads:
            self.env["tapradar.cashback.event"]._enqueue(payloads, kind="loyalty")

    @api.model_create_multi
    def create(self, vals_list):
        cards = super().create(vals_list)
        cards.filtered("tapradar_campaign_id")._tapradar_enqueue()
        cards._tapradar_enqueue_loyalty()
        return cards

    def write(self, vals):
        res = super().write(vals)
        if TRACKED_FIELDS & set(vals):
            self.filtered("tapradar_campaign_id")._tapradar_enqueue()
        if LOYALTY_FIELDS & set(vals):
            self._tapradar_enqueue_loyalty()
        return res

    def unlink(self):
        # Removed during the campaign (fully refunded): TapRadar hides it.
        self.filtered("tapradar_campaign_id")._tapradar_enqueue(deleted=True)
        self._tapradar_enqueue_loyalty(deleted=True)
        return super().unlink()
