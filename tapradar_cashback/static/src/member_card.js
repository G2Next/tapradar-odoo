/** @odoo-module **/

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { useBarcodeReader } from "@point_of_sale/app/barcode/barcode_reader_hook";
import { ErrorBarcodePopup } from "@point_of_sale/app/barcode/error_popup/barcode_error_popup";
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

// A new card the cashier chose a customer for, while its code expired before
// the link went through: scanning the card again links it right away.
let pending = null;

function showError(screen, error) {
    const message = error?.data?.message;
    if (!message) {
        throw error;
    }
    return screen.popup.add(ErrorPopup, { title: _t("TapRadar"), body: message });
}

async function select(screen, { partner_id: partnerId, points_token: token }) {
    const pos = screen.pos;
    await pos._loadPartners([partnerId]);
    const partner = pos.db.get_partner_by_id(partnerId);
    const order = pos.get_order();
    if (partner && order.get_partner() !== partner) {
        order.set_partner(partner);
    }
    // The live card was scanned: this order may spend the customer's points.
    order.tapradarPointsToken = token || false;
}

async function link(screen, partnerId, card) {
    let result;
    try {
        result = await screen.orm.call("res.partner", "tapradar_link_member_card", [[partnerId], card.scanned]);
    } catch (error) {
        pending = { card: card.card, partnerId, until: Date.now() + PENDING_MS };
        return showError(screen, error);
    }
    pending = null;
    await select(screen, result);
}

// Scanning a TapRadar card selects its customer once TapRadar confirmed the
// live code, so a screenshot cannot be used to act as that customer. An
// unknown card asks which customer it belongs to and links it.
async function cardAction(screen, card) {
    if (!card.live) {
        return screen.popup.add(ErrorPopup, {
            title: _t("TapRadar"),
            body: _t(
                "Scan the live TapRadar card in the customer's app. A photo or screenshot of the card does not work."
            ),
        });
    }
    let found;
    try {
        found = await screen.orm.call("res.partner", "tapradar_scan_member_card", [card.scanned]);
    } catch (error) {
        return showError(screen, error);
    }
    if (found) {
        return select(screen, found);
    }
    if (pending && pending.card === card.card && pending.until > Date.now()) {
        return link(screen, pending.partnerId, card);
    }
    const { confirmed } = await screen.popup.add(ConfirmPopup, {
        title: _t("New TapRadar card"),
        body: _t(
            "This TapRadar card is not linked yet. Choose the customer it belongs to, or create a new customer. From then on, scanning the card selects this customer."
        ),
        confirmText: _t("Choose customer"),
    });
    if (!confirmed) {
        return;
    }
    const { confirmed: picked, payload: chosen } = await screen.pos.showTempScreen("PartnerListScreen", {
        partner: screen.pos.get_order().get_partner(),
    });
    if (picked && chosen) {
        return link(screen, chosen.id, card);
    }
}

// Cards are recognised whatever the barcode nomenclature makes of them.
patch(ProductScreen.prototype, {
    async _barcodeProductAction(code) {
        const card = memberCard(code);
        return card ? cardAction(this, card) : super._barcodeProductAction(...arguments);
    },
    async _barcodePartnerAction(code) {
        const card = memberCard(code);
        return card ? cardAction(this, card) : super._barcodePartnerAction(...arguments);
    },
});

// The payment screen asks for the card when points are spent, so the cashier
// can scan it right there.
patch(PaymentScreen.prototype, {
    setup() {
        super.setup(...arguments);
        const onScan = async (code) => {
            const card = memberCard(code);
            if (card) {
                return cardAction(this, card);
            }
            return this.popup.add(ErrorBarcodePopup, { code: code.base_code || code.code });
        };
        useBarcodeReader({ product: onScan, client: onScan });
    },
});
