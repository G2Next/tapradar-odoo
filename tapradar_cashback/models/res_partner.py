import re

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
    "platinum_required": _lt("TapRadar cards are part of the TapRadar Platinum plan. Upgrade your TapRadar business account to link cards."),
    "invalid_card": _lt("This TapRadar card is not known for your shop. The customer can add your shop in the TapRadar app under Cards."),
    "code_required": _lt("Scan the live TapRadar card in the customer's app. A photo or screenshot of the card does not work."),
}

# TapRadar answers for the shop card that need their own wording.
CARD_ERRORS = {
    "code_expired": _lt("The code of this TapRadar card has expired. Scan the card in the app again."),
}

# TapRadar shop card from the customer app ("Karten"): the card number
# (TRK + 10 characters) followed by a 6-digit code that changes every 30 s.
MEMBER_CARD = re.compile(r"^(TRK[0-9ABCDEFGHJKMNPQRSTVWXYZ]{10})([0-9]{6})?$")


def parse_member_card(value):
    """(card number, scanned value) for a TapRadar shop card, else (None, None).

    The scanned value keeps the code; a bare card number (no code) is returned
    as is so TapRadar can answer "code_required".
    """
    scanned = re.sub(r"[\s-]", "", value or "").upper()
    match = MEMBER_CARD.match(scanned)
    return (match.group(1), scanned) if match else (None, None)


def tapradar_error(error, card=False):
    if card and error in CARD_ERRORS:
        return str(CARD_ERRORS[error])
    if error in LINK_ERRORS:
        return str(LINK_ERRORS[error])
    return _("TapRadar could not link this customer (%s).", error or "unknown")


class ResPartner(models.Model):
    _inherit = "res.partner"

    tapradar_link_url = fields.Char(compute="_compute_tapradar_link_url")
    tapradar_link_code = fields.Char(
        "TapRadar-Code",
        compute="_compute_tapradar_link_code",
        inverse="_inverse_tapradar_link_code",
        help="Scan the code the customer shows in the TapRadar app (My TapRadar code) "
        "or the TapRadar card of your shop. It links this customer with the TapRadar account, "
        "and the cashback gift cards appear in the app.",
    )
    tapradar_card = fields.Char(
        "TapRadar card",
        copy=False,
        index=True,
        help="The customer's TapRadar card for your shop. Scanning it at the till selects this customer.",
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
            card, scanned = parse_member_card(code)
            if card:
                partner._tapradar_link_with_card(scanned)
            elif code:
                partner._tapradar_link_with_code(code)

    @api.model
    def _tapradar_request(self, payload):
        """POST to TapRadar with the company's key; raises a message for the cashier when it fails."""
        company = self.env.company.sudo()
        if not company._tapradar_connected():
            raise UserError(_(
                "Connect TapRadar first: Point of Sale > Configuration > Settings > TapRadar."
            ))
        result = tapradar_api.post(self.env, company.tapradar_api_key, payload)
        if not result.get("ok"):
            raise UserError(tapradar_error(result.get("error") or "", card="card" in payload))
        return result

    def _tapradar_link(self, payload):
        self.ensure_one()
        result = self._tapradar_request({**payload, "customer_ref": self._tapradar_customer_ref()})
        self.commercial_partner_id.sudo().with_company(self.env.company).tapradar_linked = True
        return result

    def _tapradar_link_with_code(self, code):
        return self._tapradar_link({"type": "link_customer", "code": code[:32]})

    def _tapradar_link_with_card(self, scanned):
        card, scanned = parse_member_card(scanned)
        result = self._tapradar_link({"type": "link_member_card", "card": scanned})
        partner = self.commercial_partner_id.sudo()
        # A card belongs to one customer: take it away from anyone who had it before.
        self.sudo().search([("tapradar_card", "=", card), ("id", "!=", partner.id)]).tapradar_card = False
        partner.tapradar_card = card
        return result

    def tapradar_link_member_card(self, scanned):
        """POS: link the scanned TapRadar card to this customer. Returns the commercial partner id."""
        self.ensure_one()
        self.check_access_rights("write")
        self.check_access_rule("write")
        card, scanned = parse_member_card(scanned)
        if not card:
            raise UserError(str(LINK_ERRORS["invalid_card"]))
        self._tapradar_link_with_card(scanned)
        return self.commercial_partner_id.id

    @api.model
    def tapradar_scan_member_card(self, scanned):
        """POS: a TapRadar card was scanned.

        Returns the customer the card belongs to, after TapRadar confirmed the
        live code, or False when no customer has this card yet (the till then
        asks which customer it belongs to).
        """
        self.check_access_rights("read")
        card, scanned = parse_member_card(scanned)
        if not card:
            raise UserError(str(LINK_ERRORS["invalid_card"]))
        partner = self.search([("tapradar_card", "=", card)], limit=1)
        if not partner:
            return False
        result = self._tapradar_request({"type": "check_member_card", "card": scanned})
        linked = result.get("linked") is True
        commercial = partner.commercial_partner_id.sudo().with_company(self.env.company)
        if commercial.tapradar_linked != linked:
            commercial.tapradar_linked = linked
        return partner.id
