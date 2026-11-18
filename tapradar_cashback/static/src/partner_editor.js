/** @odoo-module **/

import { PartnerDetailsEdit } from "@point_of_sale/app/screens/partner_list/partner_editor/partner_editor";
import { patch } from "@web/core/utils/patch";

// "TapRadar-Code" field in the POS customer editor: the cashier scans the code
// the customer shows in the TapRadar app. The server links the customer when
// the customer is saved (res.partner.tapradar_link_code).
patch(PartnerDetailsEdit.prototype, {
    setup() {
        super.setup(...arguments);
        this.changes.tapradar_link_code = "";
    },
    saveChanges() {
        if (!this.changes.tapradar_link_code?.trim()) {
            // Nothing scanned: do not send the field at all.
            delete this.changes.tapradar_link_code;
        }
        return super.saveChanges(...arguments);
    },
});
