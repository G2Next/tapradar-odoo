import { ProductScreen } from "@point_of_sale/app/screens/product_screen/product_screen";
import { PaymentScreen } from "@point_of_sale/app/screens/payment_screen/payment_screen";
import { useBarcodeReader } from "@point_of_sale/app/hooks/barcode_reader_hook";
import { PartnerList } from "@point_of_sale/app/screens/partner_list/partner_list";
import { ask, makeAwaitable } from "@point_of_sale/app/utils/make_awaitable_dialog";
import { AlertDialog } from "@web/core/confirmation_dialog/confirmation_dialog";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";
import { useEffect } from "@odoo/owl";

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

function alert(screen, body) {
    screen.dialog.add(AlertDialog, { title: _t("TapRadar"), body });
}

function showError(screen, error) {
    const message = error?.data?.message;
    if (!message) {
        throw error;
    }
    alert(screen, message);
}

async function select(screen, { partner_id: partnerId, points_token: token }) {
    const pos = screen.pos;
    // Always read it again: the scan may have changed whether the customer is
    // linked with TapRadar (badge on the payment screen).
    const [partner] = await pos.data.read("res.partner", [partnerId]);
    const order = pos.getOrder();
    if (partner && order.getPartner() !== partner) {
        pos.setPartnerToCurrentOrder(partner);
    }
    // The live card was scanned: this order may spend the customer's points.
    order.tapradar_points_token = token || false;
    pos.updateRewards?.();
}

async function link(screen, partnerId, card) {
    let result;
    try {
        result = await screen.pos.data.call("res.partner", "tapradar_link_member_card", [
            [partnerId],
            card.scanned,
        ]);
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
        return alert(
            screen,
            _t("Scan the live TapRadar card in the customer's app. A photo or screenshot of the card does not work.")
        );
    }
    let found;
    try {
        found = await screen.pos.data.call("res.partner", "tapradar_scan_member_card", [card.scanned]);
    } catch (error) {
        return showError(screen, error);
    }
    if (found) {
        screen.sound?.play("beep");
        return select(screen, found);
    }
    if (pending && pending.card === card.card && pending.until > Date.now()) {
        return link(screen, pending.partnerId, card);
    }
    const confirmed = await ask(screen.dialog, {
        title: _t("New TapRadar card"),
        body: _t(
            "This TapRadar card is not linked yet. Choose the customer it belongs to, or create a new customer. From then on, scanning the card selects this customer."
        ),
        confirmLabel: _t("Choose customer"),
    });
    if (!confirmed) {
        return;
    }
    const chosen = await makeAwaitable(screen.dialog, PartnerList, {
        partner: screen.pos.getOrder().getPartner(),
    });
    if (chosen) {
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
        this.barcodeReader = useService("barcode_reader");
        const onScan = async (code) => {
            const card = memberCard(code);
            if (card) {
                return cardAction(this, card);
            }
            this.barcodeReader?.showNotFoundNotification(code);
        };
        useBarcodeReader({ product: onScan, client: onScan });
    },
});

// The customer list puts the cursor in its search box, so a scanner types the
// card there instead of sending it as a scan. A card typed in the search box
// is handled like a scan: the list closes and the card selects its customer.
patch(PartnerList.prototype, {
    setup() {
        super.setup(...arguments);
        useEffect(
            (query) => {
                this.tapradarSearchCard(query, false);
            },
            () => [this.state.query]
        );
    },
    tapradarSearchCard(value, enter) {
        const card = memberCard({ code: value });
        if (this.tapradarCardScanned || !card || !(card.live || enter)) {
            return false;
        }
        this.tapradarCardScanned = true;
        // Keep the order's customer as it is; the card selects its own.
        this.props.getPayload(this.props.partner || false);
        this.props.close();
        // The list is closed: use the till's services, not the list's.
        cardAction({ pos: this.pos, dialog: this.pos.dialog, sound: this.pos.sound }, card);
        return true;
    },
    async onEnter() {
        if (this.tapradarSearchCard(this.searchInputRef?.el?.value, true)) {
            return;
        }
        return super.onEnter(...arguments);
    },
    async searchPartner() {
        if (this.tapradarSearchCard(this.searchInputRef?.el?.value, true)) {
            return [];
        }
        return super.searchPartner(...arguments);
    },
});
