from odoo import api, fields, models

from . import tapradar_api


class ResPartner(models.Model):
    _inherit = "res.partner"

    tapradar_link_url = fields.Char(compute="_compute_tapradar_link_url")

    @api.depends("ref", "commercial_partner_id.ref")
    @api.depends_context("company")
    def _compute_tapradar_link_url(self):
        company = self.env.company.sudo()
        enabled = company.tapradar_receipt_qr and company._tapradar_connected() and company.tapradar_link_secret
        for partner in self:
            # Gift cards belong to the commercial partner, so its customer number is linked.
            ref = (partner.commercial_partner_id.ref or "").strip()
            partner.tapradar_link_url = (
                tapradar_api.receipt_link(
                    company.tapradar_link_base or "https://tapradar.app/l/odoo",
                    company.tapradar_integration_id,
                    company.tapradar_link_secret,
                    ref,
                )
                if enabled and ref and len(ref) <= 64
                else False
            )
