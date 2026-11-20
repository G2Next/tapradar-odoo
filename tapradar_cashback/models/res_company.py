from odoo import _, fields, models
from odoo.exceptions import UserError

from . import tapradar_api


class ResCompany(models.Model):
    _inherit = "res.company"

    tapradar_api_key = fields.Char("TapRadar integration key", groups="base.group_system", copy=False)
    tapradar_link_secret = fields.Char(groups="base.group_system", copy=False)
    tapradar_integration_id = fields.Char("TapRadar integration", readonly=True, copy=False)
    tapradar_shop_name = fields.Char("TapRadar shop", readonly=True, copy=False)
    tapradar_link_base = fields.Char(readonly=True, copy=False)
    tapradar_data_consent = fields.Boolean(
        "Share gift card data with TapRadar",
        copy=False,
        help="Customer numbers and cashback gift cards (code, value, validity, status) are sent to TapRadar "
        "so that customers receive them in the TapRadar app. Names are not sent.",
    )
    tapradar_receipt_qr = fields.Boolean(
        "TapRadar QR code on receipts",
        default=True,
        help="Print a QR code on POS receipts of customers with a customer number. "
        "Scanning it in the TapRadar app links the customer's account.",
    )

    tapradar_loyalty = fields.Boolean(
        "Loyalty points in the TapRadar app",
        copy=False,
        help="Customers linked with TapRadar see the points of your loyalty programs on their TapRadar card. "
        "Customer numbers, program names, point balances and reward thresholds are sent to TapRadar. "
        "Names are not sent. Points are still earned and spent only in Odoo.",
    )

    def write(self, vals):
        res = super().write(vals)
        if "tapradar_loyalty" in vals:
            Event = self.env["tapradar.cashback.event"]
            for company in self:
                cards = Event._loyalty_cards(company)
                if company.tapradar_loyalty:
                    cards._tapradar_enqueue_loyalty()
                else:
                    # Turned off: hide the points in the app again.
                    cards._tapradar_enqueue_loyalty(deleted=True, force=True)
        return res

    def _tapradar_connected(self):
        self.ensure_one()
        company = self.sudo()
        return bool(company.tapradar_integration_id and company.tapradar_api_key and company.tapradar_data_consent)

    def _tapradar_check_connected(self):
        for company in self:
            if not company._tapradar_connected():
                raise UserError(_(
                    "Connect TapRadar first: Point of Sale > Configuration > Settings > TapRadar. "
                    "The cashback gift cards are delivered to your customers through the TapRadar app."
                ))

    def action_tapradar_connect(self):
        self.ensure_one()
        company = self.sudo()
        if not company.tapradar_data_consent:
            raise UserError(_("Please agree to share the gift card data with TapRadar first."))
        if not company.tapradar_api_key:
            raise UserError(_("Enter the integration key from your TapRadar business account."))
        result = tapradar_api.post(self.env, company.tapradar_api_key.strip(), {"type": "ping"})
        if not result.get("integration_id") or not result.get("link_secret"):
            raise UserError(_("TapRadar answered without connection details."))
        company.write({
            "tapradar_api_key": company.tapradar_api_key.strip(),
            "tapradar_integration_id": result["integration_id"],
            "tapradar_link_secret": result["link_secret"],
            "tapradar_shop_name": result.get("organization_name") or "",
            "tapradar_link_base": result.get("receipt_link_base") or "https://www.tapradar.app/l/odoo",
        })
        # Gift cards (and loyalty points) changed while disconnected are sent now.
        self.env["tapradar.cashback.event"]._enqueue_company_cards(self)
        return True

    def action_tapradar_disconnect(self):
        self.sudo().write({
            "tapradar_api_key": False,
            "tapradar_link_secret": False,
            "tapradar_integration_id": False,
            "tapradar_shop_name": False,
            "tapradar_link_base": False,
        })
        return True
