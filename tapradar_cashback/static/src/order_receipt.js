/** @odoo-module **/

import { Order } from "@point_of_sale/app/store/models";
import { qrCodeSrc } from "@point_of_sale/utils";
import { patch } from "@web/core/utils/patch";

patch(Order.prototype, {
    export_for_printing() {
        const result = super.export_for_printing(...arguments);
        // Signed by the server: links the customer number to a TapRadar account.
        const url = this.get_partner()?.tapradar_link_url;
        result.tapradarQr = url ? qrCodeSrc(url, { size: 160 }) : false;
        return result;
    },
});
