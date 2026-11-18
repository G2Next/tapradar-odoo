from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from odoo.exceptions import UserError
from odoo.tests import tagged

from ..models.tapradar_api import link_signature
from .common import INTEGRATION_ID, queued, tapradar_api
from .test_campaign import CashbackCommon


@tagged("post_install", "-at_install", "tapradar")
class TestTapradarConnection(CashbackCommon):
    def test_campaign_needs_tapradar(self):
        self.env.company.action_tapradar_disconnect()
        campaign = self._campaign(activate=False)
        with self.assertRaisesRegex(UserError, "Connect TapRadar first"):
            campaign.action_activate()

    def test_connect_with_integration_key(self):
        company = self.env.company
        company.action_tapradar_disconnect()
        company.sudo().write({"tapradar_api_key": " trk_" + "b2" * 24 + " ", "tapradar_data_consent": False})
        with self.assertRaisesRegex(UserError, "agree"):
            company.action_tapradar_connect()

        company.tapradar_data_consent = True
        ping = {"ok": True, "integration_id": INTEGRATION_ID, "organization_name": "Feinkost Muster",
                "link_secret": "new-secret", "receipt_link_base": "https://www.tapradar.app/l/odoo"}
        with tapradar_api(lambda payload: (200, ping)) as sent:
            company.action_tapradar_connect()
        self.assertEqual(sent[0]["payload"], {"type": "ping"})
        self.assertEqual(sent[0]["headers"]["Authorization"], "Bearer trk_" + "b2" * 24)
        self.assertEqual(company.tapradar_shop_name, "Feinkost Muster")
        self.assertEqual(company.sudo().tapradar_link_secret, "new-secret")
        self.assertTrue(company._tapradar_connected())

        with tapradar_api(lambda payload: (401, {"error": "invalid_key"})):
            with self.assertRaisesRegex(UserError, "rejected the integration key"):
                company.action_tapradar_connect()

    def test_gift_cards_are_queued_and_sent(self):
        campaign = self._campaign()
        self._order([(self.meat, 5)], self.customer)
        card = self._card(campaign, self.customer)

        payloads = queued(self.env)
        self.assertEqual(payloads, [{
            "external_id": str(card.id),
            "customer_ref": "K-1042",
            "code": card.code,
            "title": "Savings Days",
            "balance": 50.0,
            "currency": campaign.currency_id.name,
            "valid_from": str(self.today + timedelta(days=1)),
            "valid_until": str(campaign.valid_until),
            "status": "ready",
            "timezone": "UTC",
        }])

        # A second purchase updates the same pending update instead of adding one.
        self._order([(self.meat, 1)], self.customer)
        self.assertEqual([p["balance"] for p in queued(self.env)], [60.0])

        with tapradar_api() as sent:
            self.env["tapradar.cashback.event"]._cron_send()
        self.assertEqual(sent[0]["payload"]["type"], "gift_cards")
        self.assertEqual(sent[0]["payload"]["cards"][0]["balance"], 60.0)
        self.assertFalse(queued(self.env))
        self.assertEqual(self.env["tapradar.cashback.event"].search([]).state, "sent")

    def test_redeemed_and_refunded_cards_reach_tapradar(self):
        campaign = self._campaign()
        self._order([(self.meat, 5)], self.customer)
        card = self._card(campaign, self.customer)
        with tapradar_api():
            self.env["tapradar.cashback.event"]._cron_send()

        card.points = 0  # redeemed at the POS
        self.assertEqual(queued(self.env)[0]["status"], "used")

        card.points = 50
        self._order([(self.meat, -5)], self.customer)  # full refund during the campaign removes the card
        self.assertFalse(self._card(campaign, self.customer))
        last = queued(self.env)[0]
        self.assertEqual((last["status"], last["balance"]), ("expired", 0.0))

    def test_failures_are_retried(self):
        self._campaign()
        self._order([(self.meat, 5)], self.customer)
        Event = self.env["tapradar.cashback.event"]

        with tapradar_api(lambda payload: (500, {"error": "internal_error"})):
            Event._cron_send()
        event = Event.search([])
        self.assertEqual((event.state, event.attempts), ("pending", 1))

        with tapradar_api(lambda payload: (200, {"results": [
            {"external_id": c["external_id"], "ok": False, "error": "invalid_code"} for c in payload["cards"]
        ]})):
            Event._cron_send()
        self.assertEqual((event.state, event.last_error), ("failed", "invalid_code"))

    def test_customers_without_number_use_the_odoo_id(self):
        self.customer.ref = False
        campaign = self._campaign()
        self._order([(self.meat, 5)], self.customer)
        self.assertTrue(campaign.card_ids)
        self.assertEqual([p["customer_ref"] for p in queued(self.env)], ["odoo-%s" % self.customer.id])
        self.assertIn("c=odoo-%s" % self.customer.id, self.customer.tapradar_link_url)

    def test_receipt_qr_link(self):
        url = self.customer.tapradar_link_url
        parts = urlparse(url)
        query = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", "https://www.tapradar.app/l/odoo")
        self.assertEqual(query["i"], INTEGRATION_ID)
        self.assertEqual(query["c"], "K-1042")
        # Same algorithm as public._customer_link_signature in TapRadar (checked against Postgres).
        self.assertEqual(link_signature("s3cret", INTEGRATION_ID, "K-1042 ä"), "4167d5370cdaf01e35f79c35c40a72d2")
        self.assertEqual(query["s"], link_signature("s3cret", INTEGRATION_ID, "K-1042"))

        self.assertIn("tapradar_link_url", self.pos_session._loader_params_res_partner()["search_params"]["fields"])

        self.env.company.tapradar_receipt_qr = False
        self.customer.invalidate_recordset(["tapradar_link_url"])
        self.assertFalse(self.customer.tapradar_link_url)


@tagged("post_install", "-at_install", "tapradar")
class TestTapradarCheckoutCode(CashbackCommon):
    def test_link_with_code_from_the_app(self):
        with tapradar_api(lambda payload: (200, {"ok": True, "gift_cards": 1})) as sent:
            self.customer.tapradar_link_code = " TR-K7M2-9QPA "
        self.assertEqual(sent[0]["payload"], {"type": "link_customer", "code": "TR-K7M2-9QPA", "customer_ref": "K-1042"})
        self.env.flush_all()
        self.env.invalidate_all()  # read back from the database, not the cache
        self.assertTrue(self.customer.tapradar_linked)
        self.assertFalse(self.customer.tapradar_link_code)  # never stored
        self.assertIn("tapradar_linked", self.pos_session._loader_params_res_partner()["search_params"]["fields"])

    def test_contact_person_links_the_company(self):
        contact = self.env["res.partner"].create({"name": "Kasse Kontakt", "parent_id": self.customer.id})
        with tapradar_api(lambda payload: (200, {"ok": True, "gift_cards": 0})) as sent:
            contact.tapradar_link_code = "TR-K7M2-9QPA"
        self.assertEqual(sent[0]["payload"]["customer_ref"], "K-1042")
        self.assertTrue(self.customer.tapradar_linked)

    def test_link_errors_are_shown_to_the_cashier(self):
        cases = {
            "invalid_code": "not valid",
            "code_expired": "expired",
            "customer_ref_taken": "already linked with another TapRadar account",
            "already_linked_other": "already linked with another customer",
            "something_new": "could not link",
        }
        for error, message in cases.items():
            with self.subTest(error=error), tapradar_api(lambda payload, e=error: (200, {"ok": False, "error": e})):
                with self.assertRaisesRegex(UserError, message):
                    self.customer.tapradar_link_code = "TR-K7M2-9QPA"
        self.assertFalse(self.customer.tapradar_linked)

        with tapradar_api(lambda payload: (429, {"ok": False, "error": "rate_limited"})):
            with self.assertRaisesRegex(UserError, "Too many attempts"):
                self.customer.tapradar_link_code = "TR-K7M2-9QPA"

    def test_empty_code_and_disconnected_shop(self):
        with tapradar_api() as sent:
            self.customer.write({"tapradar_link_code": "  ", "phone": "123"})
        self.assertFalse(sent)
        self.env.company.action_tapradar_disconnect()
        with self.assertRaisesRegex(UserError, "Connect TapRadar first"):
            self.customer.tapradar_link_code = "TR-K7M2-9QPA"

    def test_new_customer_from_the_pos_editor(self):
        with tapradar_api(lambda payload: (200, {"ok": True, "gift_cards": 0})) as sent:
            partner_id = self.env["res.partner"].create_from_ui({
                "id": False, "name": "Neukunde", "tapradar_link_code": "TR-K7M2-9QPA",
            })
        self.env.flush_all()
        self.env.invalidate_all()
        partner = self.env["res.partner"].browse(partner_id)
        self.assertEqual(sent[0]["payload"]["customer_ref"], "odoo-%s" % partner.id)
        self.assertTrue(partner.tapradar_linked)

        # A failed link does not save the customer half-way.
        count = self.env["res.partner"].search_count([])
        with tapradar_api(lambda payload: (200, {"ok": False, "error": "invalid_code"})):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self.env["res.partner"].create_from_ui({"id": False, "name": "Zweiter", "tapradar_link_code": "TR-AAAA"})
        self.assertEqual(self.env["res.partner"].search_count([]), count)
