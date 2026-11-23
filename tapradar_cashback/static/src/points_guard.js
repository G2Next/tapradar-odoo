import OrderPaymentValidation from "@point_of_sale/app/utils/order_payment_validation";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

// Loyalty points of a customer with a TapRadar card can only be spent when the
// live card was scanned for this order (member_card.js stores the token the
// server gives for that scan in order.tapradar_points_token, which is saved
// with the order). The server checks it again when the points are deducted,
// so skipping this check does not help.
patch(OrderPaymentValidation.prototype, {
    async validateOrder(isForceValidate) {
        const order = this.order;
        const spent = {};
        for (const line of order._get_reward_lines?.() || []) {
            const cardId = line.coupon_id?.id;
            if (cardId > 0 && line.points_cost > 0) {
                spent[cardId] = (spent[cardId] || 0) + line.points_cost;
            }
        }
        if (Object.keys(spent).length) {
            let result = { ok: true };
            try {
                result = await this.pos.data.call("pos.order", "tapradar_check_points_spend", [
                    spent,
                    order.tapradar_points_token || false,
                ]);
            } catch {
                // Offline: the server still refuses to deduct the points without the card.
            }
            if (!result.ok) {
                this.pos.dialog.add(AlertDialog, { title: _t("TapRadar"), body: result.message });
                return;
            }
        }
        return super.validateOrder(...arguments);
    },
});
