from odoo import api, fields, models


class TapradarCashbackPreview(models.TransientModel):
    _name = "tapradar.cashback.preview"
    _description = "Cashback campaign preview"

    campaign_id = fields.Many2one("tapradar.cashback.campaign", string="Campaign", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="campaign_id.currency_id")
    line_ids = fields.One2many("tapradar.cashback.preview.line", "preview_id")
    customer_count = fields.Integer(compute="_compute_totals")
    total_amount = fields.Monetary("Total gift card value", compute="_compute_totals")

    @api.depends("line_ids.voucher_amount")
    def _compute_totals(self):
        for preview in self:
            preview.customer_count = len(preview.line_ids)
            preview.total_amount = sum(preview.line_ids.mapped("voucher_amount"))


class TapradarCashbackPreviewLine(models.TransientModel):
    _name = "tapradar.cashback.preview.line"
    _description = "Cashback campaign preview line"
    _order = "voucher_amount desc"

    preview_id = fields.Many2one("tapradar.cashback.preview", required=True, ondelete="cascade")
    currency_id = fields.Many2one(related="preview_id.currency_id")
    partner_id = fields.Many2one("res.partner", string="Customer", readonly=True)
    customer_ref = fields.Char("Customer number", related="partner_id.ref")
    order_count = fields.Integer("Purchases", readonly=True)
    eligible_amount = fields.Monetary("Gross value", readonly=True)
    voucher_amount = fields.Monetary("Gift card", readonly=True)
