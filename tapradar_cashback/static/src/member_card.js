/** @odoo-module **/

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ConfirmPopup } from "@point_of_sale/app/utils/confirm_popup/confirm_popup";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

// TapRadar shop card from the customer app ("Karten"): the card number
// (TRK + 10 characters) followed by a 6-digit code that changes every 30 s,
// e.g. TRK7M29QPAK3X482913. A bare card number (photo, screenshot of an old
// build) is recognised so the cashier gets a clear message.
const MEMBER_CARD = /^(TRK[0-9ABCDEFGHJKMNPQRSTVWXYZ]{10})([0-9]{6})?$/;
// How long a customer chosen for a new card is remembered when its code
// expired before the link went through (the cashier just scans again).
const PENDING_MS = 3 * 60 * 1000;

function memberCard(code) {
    const value = String(code?.code || "").replace(/[\s-]/g, "").toUpperCase();
    const match = MEMBER_CARD.exec(value);
    return match ? { card: match[1], scanned: value, live: Boolean(match[2]) } : null;
}

// Scanning a TapRadar card selects its customer once TapRadar confirmed the
// live code, so a screenshot cannot be used to act as that customer. An
// unknown card asks which customer it belongs to and links it. Cards are
// recognised whatever the barcode nomenclature makes of them.
patch(ProductScreen.prototype, {
    async _barcodeProductAction(code) {
        const card = memberCard(code);
        return card ? this._tapradarCardAction(card) : super._barcodeProductAction(...arguments);
    },
    async _barcodePartnerAction(code) {
        const card = memberCard(code);
        return card ? this._tapradarCardAction(card) : super._barcodePartnerAction(...arguments);
    },
    _tapradarError(error) {
        const message = error?.data?.message;
        if (!message) {
            throw error;
        }
        return this.popup.add(ErrorPopup, { title: _t("TapRadar"), body: message });
    },
    async _tapradarSelect({ partner_id: partnerId, points_token: token }) {
        await this.pos._loadPartners([partnerId]);
        const partner = this.pos.db.get_partner_by_id(partnerId);
        if (partner && this.currentOrder.get_partner() !== partner) {
            this.currentOrder.set_partner(partner);
        }
        // The live card was scanned: this order may spend the customer's points.
        this.currentOrder.tapradarPointsToken = token || false;
    },
    async _tapradarLink(partnerId, card) {
        let result;
        try {
            result = await this.orm.call("res.partner", "tapradar_link_member_card", [[partnerId], card.scanned]);
        } catch (error) {
            // Remember the choice: scanning the card again links it right away.
            this._tapradarPending = { card: card.card, partnerId, until: Date.now() + PENDING_MS };
            return this._tapradarError(error);
        }
        this._tapradarPending = null;
        await this._tapradarSelect(result);
    },
    async _tapradarCardAction(card) {
        if (!card.live) {
            return this.popup.add(ErrorPopup, {
                title: _t("TapRadar"),
                body: _t(
                    "Scan the live TapRadar card in the customer's app. A photo or screenshot of the card does not work."
                ),
            });
        }
        let found;
        try {
            found = await this.orm.call("res.partner", "tapradar_scan_member_card", [card.scanned]);
        } catch (error) {
            return this._tapradarError(error);
        }
        if (found) {
            return this._tapradarSelect(found);
        }

        const pending = this._tapradarPending;
        if (pending && pending.card === card.card && pending.until > Date.now()) {
            return this._tapradarLink(pending.partnerId, card);
        }
        const { confirmed } = await this.popup.add(ConfirmPopup, {
            title: _t("New TapRadar card"),
            body: _t(
                "This TapRadar card is not linked yet. Choose the customer it belongs to, or create a new customer. From then on, scanning the card selects this customer."
            ),
            confirmText: _t("Choose customer"),
        });
        if (!confirmed) {
            return;
        }
        const { confirmed: picked, payload: chosen } = await this.pos.showTempScreen("PartnerListScreen", {
            partner: this.currentOrder.get_partner(),
        });
        if (picked && chosen) {
            return this._tapradarLink(chosen.id, card);
        }
    },
});
