import { OrderReceipt } from "@point_of_sale/app/screens/receipt_screen/receipt/order_receipt";
import { generateQRCodeDataUrl } from "@point_of_sale/utils";
import { patch } from "@web/core/utils/patch";

patch(OrderReceipt.prototype, {
    // Signed by the server: links the customer number to a TapRadar account.
    get tapradarQr() {
        const url = this.order.getPartner?.()?.tapradar_link_url;
        return url ? generateQRCodeDataUrl(url, { width: 160, height: 160 }) : false;
    },
});
