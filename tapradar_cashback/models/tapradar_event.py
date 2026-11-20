import json
import logging
import threading

from odoo import api, fields, models
from odoo.exceptions import UserError

from . import tapradar_api

_logger = logging.getLogger(__name__)

BATCH_SIZE = 200
MAX_ATTEMPTS = 10


class TapradarCashbackEvent(models.Model):
    """Outbox of gift card updates for TapRadar.

    Every change of a campaign gift card stores its latest state here; a
    scheduled action sends the queue in batches and retries on failure, so the
    POS never waits for TapRadar.
    """

    _name = "tapradar.cashback.event"
    _description = "TapRadar gift card update"
    _order = "id"

    company_id = fields.Many2one("res.company", required=True, index=True)
    card_id = fields.Many2one("loyalty.card", ondelete="set null", index=True)
    external_id = fields.Char(required=True, index=True)
    payload = fields.Text(required=True)
    state = fields.Selection(
        [("pending", "Pending"), ("sent", "Sent"), ("failed", "Failed")],
        default="pending",
        required=True,
        index=True,
    )
    attempts = fields.Integer(default=0)
    last_error = fields.Char()
    sent_at = fields.Datetime()

    @api.model
    def _enqueue(self, payloads):
        """`payloads`: list of (company, card or empty recordset, payload dict). Keeps one pending row per card."""
        queued = False
        for company, card, payload in payloads:
            if not company._tapradar_connected():
                continue
            pending = self.sudo().search([
                ("company_id", "=", company.id),
                ("external_id", "=", payload["external_id"]),
                ("state", "=", "pending"),
            ], limit=1)
            values = {"payload": json.dumps(payload), "card_id": card.id or False}
            if pending:
                pending.write(values)
            else:
                self.sudo().create({**values, "company_id": company.id, "external_id": payload["external_id"]})
            queued = True
        if queued:
            cron = self.env.ref("tapradar_cashback.ir_cron_tapradar_send", raise_if_not_found=False)
            if cron:
                cron.sudo()._trigger()

    @api.model
    def _enqueue_company_cards(self, company):
        cards = self.env["loyalty.card"].sudo().search([
            ("tapradar_campaign_id", "!=", False),
            ("tapradar_campaign_id.company_id", "=", company.id),
        ])
        cards._tapradar_enqueue()

    @api.model
    def _cron_send(self):
        for company in self.env["res.company"].sudo().search([]):
            if not company._tapradar_connected():
                continue
            while True:
                events = self.sudo().search([
                    ("company_id", "=", company.id),
                    ("state", "=", "pending"),
                ], limit=BATCH_SIZE)
                if not events or not events._send(company):
                    break
                if not getattr(threading.current_thread(), "testing", False):
                    self.env.cr.commit()

    def _send(self, company):
        """Send one batch. Returns True if the batch was delivered (even with rejected cards)."""
        cards = [json.loads(event.payload) for event in self]
        try:
            result = tapradar_api.post(self.env, company.tapradar_api_key, {"type": "gift_cards", "cards": cards})
        except UserError as error:
            _logger.warning("Sending %s gift card updates to TapRadar failed: %s", len(self), error)
            for event in self:
                event.write({
                    "attempts": event.attempts + 1,
                    "last_error": str(error)[:250],
                    "state": "failed" if event.attempts + 1 >= MAX_ATTEMPTS else "pending",
                })
            return False

        by_external_id = {str(item.get("external_id")): item for item in result.get("results", [])}
        now = fields.Datetime.now()
        linked_by_partner = {}
        for event in self:
            item = by_external_id.get(event.external_id, {})
            if item.get("ok"):
                event.write({"state": "sent", "sent_at": now, "last_error": False})
                partner = event.card_id.partner_id.commercial_partner_id
                if partner and isinstance(item.get("linked"), bool):
                    linked_by_partner[partner] = item["linked"]
            else:
                event.write({
                    "attempts": event.attempts + 1,
                    "last_error": item.get("error") or "no answer",
                    # A rejected card will not succeed by retrying the same data.
                    "state": "failed" if str(item.get("error", "")).startswith("invalid_")
                    or event.attempts + 1 >= MAX_ATTEMPTS else "pending",
                })
        self._tapradar_sync_linked(company, linked_by_partner)
        return True

    @api.model
    def _tapradar_sync_linked(self, company, linked_by_partner):
        """TapRadar answers each card with whether its customer number is linked.

        That keeps "Linked with TapRadar" right when a link was made with the
        receipt QR code or removed in the TapRadar portal.
        """
        for partner, linked in linked_by_partner.items():
            partner = partner.sudo().with_company(company)
            if partner.tapradar_linked != linked:
                partner.tapradar_linked = linked

    def action_retry(self):
        self.write({"state": "pending", "attempts": 0})
        self._cron_send()
