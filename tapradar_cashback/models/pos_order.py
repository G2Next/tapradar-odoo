import logging

from odoo import models

_logger = logging.getLogger(__name__)


class PosOrder(models.Model):
    _inherit = "pos.order"

    def _process_order(self, order, draft, existing_order):
        order_id = super()._process_order(order, draft, existing_order)
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
