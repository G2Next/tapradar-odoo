from odoo import _, _lt, api, fields, models
from odoo.exceptions import UserError

from . import tapradar_api

LINK_ERRORS = {
    "invalid_code": _lt("This TapRadar code is not valid. Ask the customer to open a new code in the app."),
    "code_expired": _lt("This TapRadar code has expired. Ask the customer to open a new code in the app."),
    "customer_ref_taken": _lt("This customer is already linked with another TapRadar account."),
    "already_linked_other": _lt("This TapRadar account is already linked with another customer of your shop."),
    "invalid_customer_ref": _lt("The customer number is too long for TapRadar (max. 64 characters)."),
    "rate_limited": _lt("Too many attempts. Please wait a few minutes."),
    "integration_inactive": _lt("The TapRadar integration key is no longer valid. Create a new key in your TapRadar business account."),
}


class ResPartner(models.Model):
    _inherit = "res.partner"

    tapradar_link_url = fields.Char(compute="_compute_tapradar_link_url")
    tapradar_link_code = fields.Char(
        "TapRadar-Code",
        compute="_compute_tapradar_link_code",
        inverse="_inverse_tapradar_link_code",
        help="Scan the code the customer shows in the TapRadar app (My TapRadar code). "
        "It links this customer with the TapRadar account, and the cashback gift cards appear in the app.",
    )
    tapradar_linked = fields.Boolean(
        "Linked with TapRadar",
        company_dependent=True,
        copy=False,
        help="Whether this customer's number is linked with a TapRadar account at this company. "
        "Set when a TapRadar code is saved and updated with every gift card TapRadar receives, "
        "so a link removed in the TapRadar portal or made with the receipt QR code shows up here.",
    )

    def _tapradar_customer_ref(self):
        """Customer number sent to TapRadar: the Reference, or the Odoo ID when it is empty.

        Gift cards belong to the commercial partner, so its number is used.
        """
        self.ensure_one()
        partner = self.commercial_partner_id
        ref = (partner.ref or "").strip()
        return ref or ("odoo-%s" % partner.id if partner.id else "")

    @api.depends("ref", "commercial_partner_id.ref")
    @api.depends_context("company")
    def _compute_tapradar_link_url(self):
        company = self.env.company.sudo()
        enabled = company.tapradar_receipt_qr and company._tapradar_connected() and company.tapradar_link_secret
        for partner in self:
            ref = partner._tapradar_customer_ref() if enabled and partner.id else ""
            partner.tapradar_link_url = (
                tapradar_api.receipt_link(
                    company.tapradar_link_base or "https://www.tapradar.app/l/odoo",
                    company.tapradar_integration_id,
                    company.tapradar_link_secret,
                    ref,
                )
                if ref and len(ref) <= 64
                else False
            )

    def _compute_tapradar_link_code(self):
        # The code is used once when saving; it is never stored.
        self.tapradar_link_code = False

    def _inverse_tapradar_link_code(self):
        for partner in self:
            code = (partner.tapradar_link_code or "").strip()
            if code:
                partner._tapradar_link_with_code(code)

    def _tapradar_link_with_code(self, code):
        self.ensure_one()
        company = self.env.company.sudo()
        if not company._tapradar_connected():
            raise UserError(_(
                "Connect TapRadar first: Point of Sale > Configuration > Settings > TapRadar."
            ))
        result = tapradar_api.post(self.env, company.tapradar_api_key, {
            "type": "link_customer",
            "code": code[:32],
            "customer_ref": self._tapradar_customer_ref(),
        })
        if not result.get("ok"):
            error = result.get("error") or ""
            raise UserError(str(LINK_ERRORS[error]) if error in LINK_ERRORS
                            else _("TapRadar could not link this customer (%s).", error or "unknown"))
        self.commercial_partner_id.sudo().with_company(company).tapradar_linked = True
        return result
