/** @odoo-module **/

import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { ConfirmPopup } from "@point_of_sale/app/utils/confirm_popup/confirm_popup";
import { ErrorPopup } from "@point_of_sale/app/errors/popups/error_popup";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";

// TapRadar shop card from the customer app ("Karten"), e.g. TRK7M29QPAK3X.
const MEMBER_CARD = /^TRK[0-9ABCDEFGHJKMNPQRSTVWXYZ]{10}$/;

function memberCard(code) {
    const value = String(code?.code || "").replace(/\s/g, "").toUpperCase();
    return MEMBER_CARD.test(value) ? value : null;
}

// Scanning a TapRadar card selects its customer. An unknown card asks which
// customer it belongs to and links it through TapRadar. Cards are recognised
// whatever the barcode nomenclature makes of them (product or customer).
patch(ProductScreen.prototype, {
    async _barcodeProductAction(code) {
        const card = memberCard(code);
        return card ? this._tapradarCardAction(card) : super._barcodeProductAction(...arguments);
    },
    async _barcodePartnerAction(code) {
        const card = memberCard(code);
        return card ? this._tapradarCardAction(card) : super._barcodePartnerAction(...arguments);
    },
    async _tapradarPartnerByCard(card) {
        let partner = Object.values(this.pos.db.partner_by_id).find((p) => p.tapradar_card === card);
        if (!partner) {
            const ids = await this.orm.search("res.partner", [["tapradar_card", "=", card]], { limit: 1 });
            if (ids.length) {
                await this.pos._loadPartners(ids);
                partner = this.pos.db.get_partner_by_id(ids[0]);
            }
        }
        return partner;
    },
    async _tapradarCardAction(card) {
        const order = this.currentOrder;
        const known = await this._tapradarPartnerByCard(card);
        if (known) {
            if (order.get_partner() !== known) {
                order.set_partner(known);
            }
            return;
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
            partner: order.get_partner(),
        });
        if (!picked || !chosen) {
            return;
        }
        let partnerId;
        try {
            partnerId = await this.orm.call("res.partner", "tapradar_link_member_card", [[chosen.id], card]);
        } catch (error) {
            const message = error?.data?.message;
            if (!message) {
                throw error;
            }
            return this.popup.add(ErrorPopup, { title: _t("TapRadar"), body: message });
        }
        // Reload so the card number and "Linked with TapRadar" are known offline too.
        await this.pos._loadPartners([...new Set([chosen.id, partnerId])]);
        order.set_partner(this.pos.db.get_partner_by_id(chosen.id));
    },
});
