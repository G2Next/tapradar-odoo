/** @odoo-module **/

import { Order } from "@point_of_sale/app/store/models";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

// Loyalty points of a customer with a TapRadar card can only be spent when the
// live card was scanned for this order (member_card.js stores the token the
// server gives for that scan). The server checks it again when the points are
// deducted, so skipping this check does not help.
patch(Order.prototype, {
    init_from_JSON(json) {
        super.init_from_JSON(...arguments);
        this.tapradarPointsToken = json.tapradar_points_token || false;
    },
    export_as_JSON() {
        const json = super.export_as_JSON(...arguments);
        json.tapradar_points_token = this.tapradarPointsToken || false;
        return json;
    },
});

patch(PaymentScreen.prototype, {
    async validateOrder(isForceValidate) {
        const spent = {};
        for (const line of this.currentOrder._get_reward_lines?.() || []) {
            if (line.coupon_id > 0 && line.points_cost > 0) {
                spent[line.coupon_id] = (spent[line.coupon_id] || 0) + line.points_cost;
            }
        }
        if (Object.keys(spent).length) {
            let result = { ok: true };
            try {
                result = await this.orm.call("pos.order", "tapradar_check_points_spend", [
                    spent,
                    this.currentOrder.tapradarPointsToken || false,
                ]);
            } catch {
                // Offline: the server still refuses to deduct the points without the card.
            }
            if (!result.ok) {
                this.popup.add(ErrorPopup, { title: _t("TapRadar"), body: result.message });
                return;
            }
        }
        return super.validateOrder(...arguments);
    },
});
