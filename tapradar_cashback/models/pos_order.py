import logging

from odoo import _, api, fields, models

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    tapradar_points_token = fields.Char(
        "TapRadar card scanned",
        copy=False,
        readonly=True,
        help="Set when the customer's live TapRadar card was scanned for this order; "
        "needed to spend the loyalty points of customers with a TapRadar card.",
    )

    @api.model
    def _tapradar_unverified_cards(self, spent, token):
        """Loyalty cards in `spent` ({card id: points spent}) whose owner has a TapRadar
        card but `token` does not prove the live card was scanned."""
        card_ids = [int(card_id) for card_id, points in spent.items() if int(card_id) > 0 and points > 0]
        cards = self.env["loyalty.card"].sudo().browse(card_ids).exists()
        return cards.filtered(
            lambda card: card.program_id.program_type == "loyalty"
            and card.partner_id
            and card.partner_id._tapradar_points_protected()
            and not card.partner_id._tapradar_points_verified(token)
        )

    @api.model
    def tapradar_check_points_spend(self, spent, token):
        """POS, before payment: may this order spend these loyalty points?"""
        self.check_access("create")
        if self._tapradar_unverified_cards(spent or {}, token):
            return {"ok": False, "message": _(
                "This customer collects points with the TapRadar card. To spend points, "
                "scan the live TapRadar card in the customer's app first."
            )}
        return {"ok": True}

    def confirm_coupon_programs(self, coupon_data):
        """Last line of defence: points of a TapRadar customer are not deducted
        without the live card, even if the till skipped the check.

        The till sends the point changes itself, so any card whose balance would
        go down counts, whatever the reward lines say.
        """
        coupon_data = {int(k): dict(v) for k, v in coupon_data.items()}
        spent = {}
        for line in self.lines.filtered(lambda l: l.is_reward_line and l.coupon_id and l.points_cost > 0):
            spent[line.coupon_id.id] = spent.get(line.coupon_id.id, 0.0) + line.points_cost
        for card_id, values in coupon_data.items():
            if card_id > 0 and (values.get("points") or 0) < 0:
                spent[card_id] = max(spent.get(card_id, 0.0), -values["points"])
        unverified = self._tapradar_unverified_cards(spent, self.tapradar_points_token)
        if unverified:
            for card in unverified:
                if card.id in coupon_data:
                    # Keep what the order earned, never take points away.
                    values = coupon_data[card.id]
                    change = (values.get("points") or 0) + spent[card.id]
                    values["points"] = max(change, 0.0)
                    if "points_spent" in values:
                        # Loyalty history: nothing was spent.
                        values["points_spent"] = 0.0
                        values["points_earned"] = values["points"]
            _logger.warning("POS order %s: loyalty points of cards %s kept, TapRadar card not scanned",
                            self.id, unverified.ids)
            message = _("Loyalty points were not deducted: the customer's TapRadar card was not scanned for this order.")
            self.sudo().internal_note = "\n".join(filter(None, [self.internal_note, message]))
        return super().confirm_coupon_programs(coupon_data)

    @api.model
    def _process_order(self, order, existing_order):
        # Any value the till sends is only kept when it is a short string; the
        # signature is checked when points are deducted.
        if "tapradar_points_token" in order:
            token = order["tapradar_points_token"]
            order["tapradar_points_token"] = token[:200] if isinstance(token, str) and token else False
        draft = order.get("state") == "draft"
        order_id = super()._process_order(order, existing_order)
        if not draft:
            pos_order = self.browse(order_id)
            try:
                # Never let a gift card problem block the sale itself; the hourly
                # cron catches up on anything missed here.
                with self.env.cr.savepoint():
                    self.env["tapradar.cashback.campaign"]._sync_for_pos_orders(pos_order)
            except Exception:
                _logger.exception("campaign gift card update failed for POS order %s", order_id)
        return order_id

    def validate_coupon_programs(self, point_changes, new_codes):
        """Server-side guard: campaign gift cards cannot be spent during their campaign."""
        spent_ids = [int(card_id) for card_id, change in point_changes.items() if change < 0]
        cards = self.env["loyalty.card"].sudo().browse(spent_ids).exists().filtered("tapradar_campaign_id")
        for card in cards:
            message = card.tapradar_campaign_id._redemption_block_message()
            if message:
                return {
                    "successful": False,
                    "payload": {"message": message, "removed_coupons": [card.id]},
                }
        return super().validate_coupon_programs(point_changes, new_codes)
