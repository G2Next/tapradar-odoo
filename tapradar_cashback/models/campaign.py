import logging
from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date

_logger = logging.getLogger(__name__)

PAID_POS_STATES = ("paid", "done", "invoiced")


def calculate_vouchers(
    lines, percent, min_cents, threshold_scope="per_order", rounding="cent", reward_type="percent", fixed_cents=0
):
    """Compute cashback from POS line data.

    ``lines`` is an iterable of dicts with ``order_id``, ``partner_id`` and
    ``cents`` (gross line value incl. taxes, in cents; negative on refunds).
    Excluded products (e.g. deposits) must already be filtered out.

    Orders with a negative total are refunds and are netted against the
    customer's qualifying value, never below zero. Returns a list of dicts
    ``{partner_id, eligible_cents, voucher_cents, order_ids}`` sorted by partner.

    ``reward_type`` "percent": ``percent`` of the qualifying value.
    ``reward_type`` "fixed": ``fixed_cents`` per qualifying purchase (``per_order``)
    or once per customer (``per_customer``). With ``per_order``, refunds take a
    fixed amount back as soon as the remaining value no longer covers the
    minimum for every qualifying purchase.
    """
    if reward_type == "fixed":
        if fixed_cents <= 0:
            raise ValueError("fixed amount must be positive")
    elif not 0 < percent <= 100:
        raise ValueError("percent must be between 0 and 100")

    orders = {}
    for line in lines:
        order = orders.setdefault(line["order_id"], {"partner_id": line["partner_id"], "cents": 0})
        order["cents"] += line["cents"]

    customers = defaultdict(lambda: {"purchases": 0, "refunds": 0, "qualifying": 0, "order_ids": []})
    for order_id, order in orders.items():
        customer = customers[order["partner_id"]]
        if order["cents"] < 0:
            customer["refunds"] += order["cents"]
        elif threshold_scope == "per_customer" or order["cents"] >= min_cents:
            customer["purchases"] += order["cents"]
            if order["cents"] > 0:
                customer["qualifying"] += 1
        else:
            continue
        customer["order_ids"].append(order_id)

    vouchers = []
    for partner_id, customer in customers.items():
        eligible = max(0, customer["purchases"] + customer["refunds"])
        if eligible == 0 or (threshold_scope == "per_customer" and eligible < min_cents):
            continue
        if reward_type == "fixed":
            if threshold_scope == "per_customer":
                voucher = fixed_cents
            else:
                earned = customer["qualifying"]
                if min_cents > 0:
                    earned = min(earned, eligible // min_cents)
                voucher = fixed_cents * earned
        else:
            # Integer arithmetic in cents: no floating point drift on money.
            voucher = eligible * round(percent * 100) // 10000
            if rounding == "euro_down":
                voucher -= voucher % 100
        if voucher <= 0:
            continue
        vouchers.append({
            "partner_id": partner_id,
            "eligible_cents": eligible,
            "voucher_cents": voucher,
            "order_ids": sorted(customer["order_ids"]),
        })
    return sorted(vouchers, key=lambda v: v["partner_id"])


def _tz_get(self):
    return [(tz, tz) for tz in sorted(pytz.all_timezones, key=lambda tz: tz if not tz.startswith("Etc/") else "_")]


class TapradarCashbackCampaign(models.Model):
    _name = "tapradar.cashback.campaign"
    _description = "Cashback Campaign"
    _inherit = ["mail.thread"]
    _order = "date_from desc, id desc"

    name = fields.Char(required=True, tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    currency_id = fields.Many2one(related="company_id.currency_id")
    state = fields.Selection(
        [("draft", "Draft"), ("active", "Active"), ("done", "Finished")],
        default="draft",
        required=True,
        readonly=True,
        copy=False,
        tracking=True,
        help="Active: every paid POS order of a customer creates or updates the customer's gift card right away.",
    )

    date_from = fields.Date("First campaign day", required=True, tracking=True)
    date_to = fields.Date("Last campaign day", required=True, tracking=True)
    tz = fields.Selection(
        _tz_get,
        string="Timezone",
        required=True,
        default=lambda self: self.env.user.tz or "Europe/Vienna",
        help="The campaign days start and end at midnight in this timezone.",
    )
    pos_config_ids = fields.Many2many(
        "pos.config", string="Points of Sale", help="Leave empty to include every Point of Sale of the company."
    )

    reward_type = fields.Selection(
        [("percent", "Percentage of the purchase"), ("fixed", "Fixed amount")],
        string="Gift card value",
        default="percent",
        required=True,
        tracking=True,
    )
    percent = fields.Float("Cashback (%)", default=10.0, tracking=True, help="e.g. 5 or 10")
    fixed_amount = fields.Monetary(
        "Fixed amount",
        tracking=True,
        help="Per qualifying purchase, or once per customer if the minimum applies to the sum of all purchases.",
    )
    min_amount = fields.Monetary("Minimum purchase", default=50.0, required=True, tracking=True)
    threshold_scope = fields.Selection(
        [("per_order", "Per purchase"), ("per_customer", "Sum of all campaign purchases")],
        string="Minimum applies to",
        default="per_order",
        required=True,
    )
    rounding = fields.Selection(
        [("cent", "To the cent"), ("euro_down", "Round down to whole units")],
        default="cent",
        required=True,
    )
    excluded_categ_ids = fields.Many2many(
        "product.category",
        string="Excluded categories",
        help="Products in these categories (e.g. bottle or crate deposits) do not count towards the gross value.",
    )

    program_id = fields.Many2one(
        "loyalty.program",
        string="Gift card program",
        required=True,
        copy=False,
        domain="[('program_type', '=', 'gift_card'), ('company_id', 'in', [company_id, False])]",
        help="A gift card program used only for this campaign. Its validity dates are set from the campaign.",
    )
    valid_from = fields.Date(compute="_compute_valid_from", help="The day after the last campaign day.")
    valid_until = fields.Date(
        "Valid until",
        required=True,
        tracking=True,
        help="Last day on which the campaign's gift cards can be used.",
    )
    send_email = fields.Boolean(
        "Email the gift card",
        default=False,
        help="Send the gift card program's email when a customer's gift card is created.",
    )

    card_ids = fields.One2many("loyalty.card", "tapradar_campaign_id", string="Gift Cards", readonly=True)
    card_count = fields.Integer(compute="_compute_card_stats")
    card_total = fields.Monetary("Issued value", compute="_compute_card_stats")

    _sql_constraints = [
        ("min_amount_positive", "CHECK(min_amount >= 0)", "The minimum purchase cannot be negative."),
        ("program_unique", "UNIQUE(program_id)", "Each campaign needs its own gift card program."),
    ]

    @api.constrains("reward_type", "percent", "fixed_amount")
    def _check_reward(self):
        for campaign in self:
            if campaign.reward_type == "percent" and not 0 < campaign.percent <= 100:
                raise ValidationError(_("The cashback must be between 0 and 100 %."))
            if campaign.reward_type == "fixed" and campaign.fixed_amount <= 0:
                raise ValidationError(_("The fixed amount must be greater than zero."))

    @api.constrains("date_from", "date_to", "valid_until")
    def _check_dates(self):
        for campaign in self:
            if campaign.date_to < campaign.date_from:
                raise ValidationError(_("The last campaign day cannot be before the first one."))
            if campaign.valid_until <= campaign.date_to:
                raise ValidationError(_("The gift cards must stay valid after the last campaign day."))

    @api.constrains("program_id")
    def _check_program(self):
        for campaign in self:
            if campaign.program_id.program_type != "gift_card":
                raise ValidationError(_("The campaign needs a gift card program."))

    @api.depends("date_to")
    def _compute_valid_from(self):
        for campaign in self:
            campaign.valid_from = campaign.date_to + timedelta(days=1) if campaign.date_to else False

    @api.depends("card_ids.points")
    def _compute_card_stats(self):
        for campaign in self:
            campaign.card_count = len(campaign.card_ids)
            campaign.card_total = sum(campaign.card_ids.mapped("points"))

    @api.model_create_multi
    def create(self, vals_list):
        campaigns = super().create(vals_list)
        campaigns._sync_program_dates()
        return campaigns

    def write(self, vals):
        if self.card_ids and {"program_id", "company_id"} & set(vals):
            raise UserError(_("The program cannot be changed once gift cards exist."))
        res = super().write(vals)
        if {"program_id", "date_to", "valid_until"} & set(vals):
            self._sync_program_dates()
        return res

    def _sync_program_dates(self):
        """The program is only usable from the day after the campaign until `valid_until`.

        Odoo itself then refuses the gift cards at the POS outside that window
        (the POS does not load programs whose dates do not include today).
        """
        for campaign in self:
            campaign.program_id.sudo().write({
                "date_from": campaign.valid_from,
                "date_to": campaign.valid_until,
            })
            campaign.card_ids.sudo().with_context(loyalty_no_mail=True).write(
                {"expiration_date": campaign.valid_until}
            )

    # ------------------------------------------------------------------
    # Period helpers
    # ------------------------------------------------------------------

    def _utc_bounds(self):
        """Naive UTC datetimes [start, end) covering the campaign days in the campaign timezone."""
        self.ensure_one()
        tz = pytz.timezone(self.tz)
        start = tz.localize(datetime.combine(self.date_from, time.min))
        end = tz.localize(datetime.combine(self.date_to + timedelta(days=1), time.min))
        return (
            start.astimezone(pytz.utc).replace(tzinfo=None),
            end.astimezone(pytz.utc).replace(tzinfo=None),
        )

    def _is_over(self, now=None):
        self.ensure_one()
        return (now or fields.Datetime.now()) >= self._utc_bounds()[1]

    def _redemption_block_message(self, now=None):
        """Why a gift card of this campaign cannot be used now, or False."""
        self.ensure_one()
        if not self._is_over(now):
            return _(
                "This gift card can only be used from %(date)s, after the cashback campaign.",
                date=format_date(self.env, self.valid_from),
            )
        return False

    # ------------------------------------------------------------------
    # Calculation
    # ------------------------------------------------------------------

    def _excluded_product_ids(self):
        self.ensure_one()
        if not self.excluded_categ_ids:
            return set()
        products = (
            self.env["product.product"]
            .with_context(active_test=False)
            .search([("categ_id", "child_of", self.excluded_categ_ids.ids)])
        )
        return set(products.ids)

    def _get_order_lines(self, partners=None):
        self.ensure_one()
        start, end = self._utc_bounds()
        domain = [
            ("date_order", ">=", start),
            ("date_order", "<", end),
            ("state", "in", PAID_POS_STATES),
            ("partner_id", "!=", False),
            ("company_id", "=", self.company_id.id),
        ]
        if partners is not None:
            domain.append(("partner_id", "child_of", partners.ids))
        if self.pos_config_ids:
            domain.append(("config_id", "in", self.pos_config_ids.ids))
        orders = self.env["pos.order"].sudo().search(domain)
        excluded = self._excluded_product_ids()
        return [
            {
                "order_id": line.order_id.id,
                "partner_id": line.order_id.partner_id.commercial_partner_id.id,
                "cents": round(line.price_subtotal_incl * 100),
            }
            for line in orders.lines
            if line.product_id.id not in excluded
        ]

    def _compute_vouchers(self, partners=None):
        self.ensure_one()
        return calculate_vouchers(
            self._get_order_lines(partners),
            percent=self.percent,
            min_cents=round(self.min_amount * 100),
            threshold_scope=self.threshold_scope,
            rounding=self.rounding,
            reward_type=self.reward_type,
            fixed_cents=round(self.fixed_amount * 100),
        )

    # ------------------------------------------------------------------
    # gift card synchronisation
    # ------------------------------------------------------------------

    def _sync_cards(self, partners=None):
        """Bring the customers' gift cards in line with their campaign purchases.

        One gift card per customer and campaign. During the campaign its value
        follows every purchase and refund. Once the campaign is over, existing
        gift cards are never changed (they may already be in use); only missing
        ones are created, e.g. for offline POS orders synchronised late.
        """
        self.ensure_one()
        if partners is not None:
            partners = partners.commercial_partner_id
        vouchers = {v["partner_id"]: v for v in self._compute_vouchers(partners)}
        cards = self.card_ids.sudo()
        if partners is not None:
            cards = cards.filtered(lambda c: c.partner_id in partners)
        cards_by_partner = {card.partner_id.id: card for card in cards}
        is_over = self._is_over()

        new_cards = []
        for partner_id, voucher in vouchers.items():
            points = voucher["voucher_cents"] / 100
            card = cards_by_partner.get(partner_id)
            if not card:
                new_cards.append({
                    "program_id": self.program_id.id,
                    "partner_id": partner_id,
                    "points": points,
                    "expiration_date": self.valid_until,
                    "tapradar_campaign_id": self.id,
                })
            elif not is_over and card.points != points:
                card.with_context(loyalty_no_mail=True).points = points
        if not is_over:
            # Fully refunded during the campaign: the gift card was never usable, remove it.
            cards.filtered(lambda c: c.partner_id.id not in vouchers).unlink()

        if new_cards:
            self.env["loyalty.card"].sudo().with_context(loyalty_no_mail=not self.send_email).create(new_cards)
        return True

    @api.model
    def _sync_for_pos_orders(self, orders):
        """Called after the POS saved paid orders: update the gift cards of their customers."""
        orders = orders.filtered(lambda o: o.partner_id and o.state in PAID_POS_STATES)
        if not orders:
            return
        campaigns = self.sudo().search([("state", "=", "active"), ("company_id", "in", orders.company_id.ids)])
        for campaign in campaigns:
            start, end = campaign._utc_bounds()
            relevant = orders.filtered(
                lambda o: start <= o.date_order < end
                and o.company_id == campaign.company_id
                and (not campaign.pos_config_ids or o.config_id in campaign.pos_config_ids)
            )
            if relevant:
                campaign._sync_cards(relevant.partner_id)

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def action_activate(self):
        for campaign in self:
            if campaign.state != "draft":
                raise UserError(_("Only draft campaigns can be activated."))
        # The gift cards reach the customers through the TapRadar app.
        self.company_id._tapradar_check_connected()
        self.state = "active"
        # Purchases made before activation (e.g. activated during the campaign) are included too.
        for campaign in self:
            campaign._sync_cards()

    def action_reset_to_draft(self):
        for campaign in self:
            if campaign.card_ids:
                raise UserError(_("Gift cards already exist for %s.", campaign.name))
        self.state = "draft"

    def action_sync(self):
        for campaign in self:
            if campaign.state != "active":
                raise UserError(_("Only active campaigns create gift cards."))
            campaign._sync_cards()
        return True

    def action_preview(self):
        self.ensure_one()
        vouchers = self._compute_vouchers()
        wizard = self.env["tapradar.cashback.preview"].create({
            "campaign_id": self.id,
            "line_ids": [
                (0, 0, {
                    "partner_id": v["partner_id"],
                    "order_count": len(v["order_ids"]),
                    "eligible_amount": v["eligible_cents"] / 100,
                    "voucher_amount": v["voucher_cents"] / 100,
                })
                for v in vouchers
            ],
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Preview: %s", self.name),
            "res_model": "tapradar.cashback.preview",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_view_cards(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Gift Cards"),
            "res_model": "loyalty.card",
            "view_mode": "tree,form",
            "domain": [("tapradar_campaign_id", "=", self.id)],
            "context": {"create": False},
        }

    @api.model
    def _cron_finish(self):
        """Safety net: catch up on any missed order, then close finished campaigns."""
        for campaign in self.search([("state", "=", "active")]):
            try:
                with self.env.cr.savepoint():
                    campaign._sync_cards()
                    if campaign._is_over():
                        campaign.state = "done"
            except Exception:
                _logger.exception("Synchronising gift cards for campaign %s failed", campaign.id)
