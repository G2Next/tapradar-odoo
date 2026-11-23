import json
import time
from datetime import timedelta
from unittest.mock import patch
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

        self.assertIn("tapradar_link_url", self.env["res.partner"]._load_pos_data_fields(self.config))

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
        self.assertIn("tapradar_linked", self.env["res.partner"]._load_pos_data_fields(self.config))

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
            partner_id = self.env["res.partner"].create({
                "name": "Neukunde", "tapradar_link_code": "TR-K7M2-9QPA",
            }).id
        self.env.flush_all()
        self.env.invalidate_all()
        partner = self.env["res.partner"].browse(partner_id)
        self.assertEqual(sent[0]["payload"]["customer_ref"], "odoo-%s" % partner.id)
        self.assertTrue(partner.tapradar_linked)

        # A failed link does not save the customer half-way.
        count = self.env["res.partner"].search_count([])
        with tapradar_api(lambda payload: (200, {"ok": False, "error": "invalid_code"})):
            with self.assertRaises(UserError), self.env.cr.savepoint():
                self.env["res.partner"].create({"name": "Zweiter", "tapradar_link_code": "TR-AAAA"})
        self.assertEqual(self.env["res.partner"].search_count([]), count)

    def test_linked_customer_can_link_again(self):
        """A link removed in the TapRadar portal is renewed with a new code."""
        self.customer.with_company(self.env.company).tapradar_linked = True
        with tapradar_api(lambda payload: (200, {"ok": True, "gift_cards": 1})) as sent:
            self.customer.tapradar_link_code = "TR-AB12-CD34"
        self.assertEqual(sent[0]["payload"]["code"], "TR-AB12-CD34")
        self.env.flush_all()
        self.env.invalidate_all()
        self.assertTrue(self.customer.tapradar_linked)

    def _send_cards(self, answer):
        with tapradar_api(answer):
            self.env["tapradar.cashback.event"]._cron_send()
        self.env.flush_all()
        self.env.invalidate_all()  # read back from the database, not the cache

    @staticmethod
    def _answer(ok, linked=None):
        def answer(payload):
            item = {"ok": True, "linked": linked} if ok else {"ok": False, "error": "not_saved"}
            return 200, {"ok": ok, "results": [{"external_id": card["external_id"], **item} for card in payload["cards"]]}
        return answer

    def test_gift_card_answers_keep_the_linked_flag_right(self):
        self._campaign()
        self._order([(self.meat, 5)], self.customer)
        # Linked with the receipt QR code: Odoo learns it from the next gift card.
        self.assertFalse(self.customer.tapradar_linked)
        self._send_cards(self._answer(True, linked=True))
        self.assertTrue(self.customer.tapradar_linked)
        # Link removed in the TapRadar portal: the flag goes away with the next update.
        self._order([(self.meat, 1)], self.customer)
        self._send_cards(self._answer(True, linked=False))
        self.assertFalse(self.customer.tapradar_linked)

    def test_failed_cards_do_not_touch_the_linked_flag(self):
        self.customer.with_company(self.env.company).tapradar_linked = True
        self._campaign()
        self._order([(self.meat, 5)], self.customer)
        self._send_cards(self._answer(False))
        self.assertTrue(self.customer.tapradar_linked)


@tagged("post_install", "-at_install", "tapradar")
class TestTapradarMemberCard(CashbackCommon):
    CARD = "TRK7M29QPAK3X"
    SCANNED = "TRK7M29QPAK3X482913"  # card number + current 6-digit code

    def _reload(self):
        self.env.flush_all()
        self.env.invalidate_all()  # read back from the database, not the cache

    def test_card_scanned_into_the_code_field(self):
        with tapradar_api(lambda payload: (200, {"ok": True, "gift_cards": 0})) as sent:
            self.customer.tapradar_link_code = " trk7m29 qpak3x-482 913 "
        self.assertEqual(sent[0]["payload"], {"type": "link_member_card", "card": self.SCANNED, "customer_ref": "K-1042"})
        self._reload()
        self.assertEqual(self.customer.tapradar_card, self.CARD)  # stored without the code
        self.assertTrue(self.customer.tapradar_linked)
        self.assertIn("tapradar_card", self.env["res.partner"]._load_pos_data_fields(self.config))

    def test_pos_links_the_card_and_moves_it_from_another_customer(self):
        other = self.env["res.partner"].create({"name": "Vorher", "tapradar_card": self.CARD})
        contact = self.env["res.partner"].create({"name": "Kasse Kontakt", "parent_id": self.customer.id})
        with tapradar_api(lambda payload: (200, {"ok": True, "gift_cards": 2})) as sent:
            result = contact.tapradar_link_member_card(self.SCANNED)
        self.assertEqual(result["partner_id"], self.customer.id)
        self.assertTrue(self.customer._tapradar_points_verified(result["points_token"]))
        self.assertEqual(sent[0]["payload"]["customer_ref"], "K-1042")
        self._reload()
        self.assertEqual(self.customer.tapradar_card, self.CARD)
        self.assertFalse(other.tapradar_card)

    def test_scan_checks_the_live_code_with_tapradar(self):
        Partner = self.env["res.partner"]
        # Unknown card: no request, the till asks for the customer.
        with tapradar_api() as sent:
            self.assertFalse(Partner.tapradar_scan_member_card(self.SCANNED))
        self.assertFalse(sent)

        self.customer.tapradar_card = self.CARD
        with tapradar_api(lambda payload: (200, {"ok": True, "linked": True})) as sent:
            found = Partner.tapradar_scan_member_card(self.SCANNED.lower())
        self.assertEqual(found["partner_id"], self.customer.id)
        self.assertTrue(self.customer._tapradar_points_verified(found["points_token"]))
        self.assertEqual(sent[0]["payload"], {"type": "check_member_card", "card": self.SCANNED})
        self._reload()
        self.assertTrue(self.customer.tapradar_linked)

        # A screenshot: the code is too old, the customer is not selected.
        with tapradar_api(lambda payload: (200, {"ok": False, "error": "code_expired"})):
            with self.assertRaisesRegex(UserError, "code of this TapRadar card has expired"):
                Partner.tapradar_scan_member_card(self.SCANNED)
        # Only the printed number: TapRadar asks for the live card.
        with tapradar_api(lambda payload: (200, {"ok": False, "error": "code_required"})) as sent:
            with self.assertRaisesRegex(UserError, "screenshot of the card does not work"):
                Partner.tapradar_scan_member_card(self.CARD)
        self.assertEqual(sent[0]["payload"]["card"], self.CARD)

    def test_card_errors_are_shown_to_the_cashier(self):
        for error, message in [
            ("invalid_card", "not known for your shop"),
            ("already_linked_other", "already linked with another customer"),
            ("platinum_required", "Platinum plan"),
            ("code_expired", "code of this TapRadar card has expired"),
        ]:
            with tapradar_api(lambda payload, error=error: (200, {"ok": False, "error": error})):
                with self.assertRaisesRegex(UserError, message):
                    self.customer.tapradar_link_member_card(self.SCANNED)
        # The one-time code keeps its own wording.
        with tapradar_api(lambda payload: (200, {"ok": False, "error": "code_expired"})):
            with self.assertRaisesRegex(UserError, "This TapRadar code has expired"):
                self.customer.tapradar_link_code = "TR-K7M2-9QPA"
        self._reload()
        self.assertFalse(self.customer.tapradar_card)
        self.assertFalse(self.customer.tapradar_linked)
        with tapradar_api() as sent:
            with self.assertRaisesRegex(UserError, "not known for your shop"):
                self.customer.tapradar_link_member_card("TRK-ILOU")
        self.assertFalse(sent)


@tagged("post_install", "-at_install", "tapradar")
class TestTapradarLoyalty(CashbackCommon):
    """Loyalty points of the shop's own program, shown on the TapRadar card."""

    def setUp(self):
        super().setUp()
        Program = self.env["loyalty.program"]
        self.program = Program.browse(Program.create_from_template("loyalty")["res_id"])
        self.program.write({"name": "Treuekarte", "portal_point_name": "Punkte"})
        self.reward = self.program.reward_ids[:1]
        self.reward.write({"required_points": 100, "description": "10 € Rabatt"})
        self.Event = self.env["tapradar.cashback.event"]

    def _loyalty(self):
        return self.Event.search([("kind", "=", "loyalty"), ("state", "=", "pending")])

    def test_nothing_is_sent_until_the_shop_turns_it_on(self):
        card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        self.assertFalse(self._loyalty())

        self.env.company.tapradar_loyalty = True
        payloads = [json.loads(event.payload) for event in self._loyalty()]
        self.assertEqual(payloads, [{
            "external_id": str(card.id),
            "customer_ref": "K-1042",
            "program": "Treuekarte",
            "points": 40.0,
            "unit": "Punkte",
            "rewards": [{"description": "10 € Rabatt", "points": 100.0}],
            "active": True,
        }])

    def test_points_are_queued_and_sent_as_loyalty_cards(self):
        self.env.company.tapradar_loyalty = True
        card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        card.points = 353.604  # points earned at the POS
        self.assertEqual([json.loads(e.payload)["points"] for e in self._loyalty()], [353.6])
        # Cards without a customer and gift cards are not loyalty points.
        self.env["loyalty.card"].create({"program_id": self.program.id, "points": 5})
        self.assertEqual(len(self._loyalty()), 1)

        with tapradar_api() as sent:
            self.Event._cron_send()
        self.assertEqual([s["payload"]["type"] for s in sent], ["loyalty_cards"])
        self.assertEqual(sent[0]["payload"]["cards"][0]["points"], 353.6)
        self.assertFalse(self._loyalty())

    def test_reward_and_program_changes_are_resent(self):
        self.env.company.tapradar_loyalty = True
        self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        with tapradar_api():
            self.Event._cron_send()

        self.reward.required_points = 150
        self.assertEqual(json.loads(self._loyalty().payload)["rewards"][0]["points"], 150.0)

        self.program.active = False
        last = json.loads(self._loyalty().payload)
        self.assertEqual((last["active"], last["points"], last["rewards"]), (False, 0.0, []))

    def test_turning_it_off_hides_the_points(self):
        self.env.company.tapradar_loyalty = True
        card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        with tapradar_api():
            self.Event._cron_send()

        self.env.company.tapradar_loyalty = False
        self.assertEqual(json.loads(self._loyalty().payload)["active"], False)
        with tapradar_api():
            self.Event._cron_send()
        card.points = 80  # no longer sent
        self.assertFalse(self._loyalty())

    def test_card_without_customer_is_hidden(self):
        self.env.company.tapradar_loyalty = True
        card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        with tapradar_api():
            self.Event._cron_send()

        card.sudo().partner_id = False
        last = json.loads(self._loyalty().payload)
        # Hidden under the old customer number, so the app stops showing it.
        self.assertEqual((last["customer_ref"], last["active"], last["points"]), ("K-1042", False, 0.0))

    def test_card_moved_to_another_customer_follows_it(self):
        self.env.company.tapradar_loyalty = True
        card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        with tapradar_api():
            self.Event._cron_send()

        card.sudo().partner_id = self.other_customer
        last = json.loads(self._loyalty().payload)
        # Same card id: TapRadar moves it to the new customer number.
        self.assertEqual((last["customer_ref"], last["active"]), ("K-2000", True))

    def test_deleted_card_is_hidden(self):
        self.env.company.tapradar_loyalty = True
        card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 40})
        card.sudo().unlink()  # an admin deletes it
        self.assertEqual(json.loads(self._loyalty().payload)["active"], False)


@tagged("post_install", "-at_install", "tapradar")
class TestTapradarPointsGuard(CashbackCommon):
    """Points of a customer with a TapRadar card are spent only with the live card."""

    CARD = "TRK7M29QPAK3X"
    SCANNED = "TRK7M29QPAK3X482913"

    def setUp(self):
        super().setUp()
        Program = self.env["loyalty.program"]
        self.program = Program.browse(Program.create_from_template("loyalty")["res_id"])
        self.card = self.env["loyalty.card"].create({"program_id": self.program.id, "partner_id": self.customer.id, "points": 200})
        self.customer.tapradar_card = self.CARD
        self.Order = self.env["pos.order"]

    def _scan(self):
        with tapradar_api(lambda payload: (200, {"ok": True, "linked": True})):
            return self.env["res.partner"].tapradar_scan_member_card(self.SCANNED)["points_token"]

    def test_spending_needs_the_live_card(self):
        spent = {str(self.card.id): 100}
        refused = self.Order.tapradar_check_points_spend(spent, False)
        self.assertFalse(refused["ok"])
        self.assertIn("scan the live TapRadar card", refused["message"])
        self.assertTrue(self.Order.tapradar_check_points_spend(spent, self._scan())["ok"])
        # Earning points or spending none needs nothing.
        self.assertTrue(self.Order.tapradar_check_points_spend({}, False)["ok"])

    def test_tokens_cannot_be_forged_reused_for_others_or_kept(self):
        token = self._scan()
        partner_id, expires, signature = token.split(":")
        other = self.other_customer
        other.tapradar_card = "TRK0000000000"
        self.assertFalse(other._tapradar_points_verified(token))
        self.assertFalse(other._tapradar_points_verified("%s:%s:%s" % (other.id, expires, signature)))
        self.assertFalse(self.customer._tapradar_points_verified("%s:%s:%s" % (partner_id, int(expires) + 999, signature)))
        self.assertFalse(self.customer._tapradar_points_verified("garbage"))
        self.assertFalse(self.customer._tapradar_points_verified(None))
        with patch.object(time, "time", return_value=int(expires) + 1):
            self.assertFalse(self.customer._tapradar_points_verified(token))

    def test_customers_without_tapradar_card_are_unchanged(self):
        self.customer.tapradar_card = False
        self.assertTrue(self.Order.tapradar_check_points_spend({str(self.card.id): 100}, False)["ok"])

    def _spend(self, token):
        order = self._order([(self.cheese, 1)], self.customer)
        order.lines[:1].sudo().write({"is_reward_line": True, "coupon_id": self.card.id, "points_cost": 100})
        order.sudo().tapradar_points_token = token
        order.confirm_coupon_programs({str(self.card.id): {
            "points": -100, "program_id": self.program.id, "coupon_id": self.card.id, "barcode": False,
        }})
        return order

    def test_points_are_not_deducted_without_the_card(self):
        order = self._spend(False)
        self.assertEqual(self.card.points, 200)
        self.assertIn("TapRadar card was not scanned", order.internal_note)

    def test_points_are_deducted_with_the_card(self):
        order = self._spend(self._scan())
        self.assertEqual(self.card.points, 100)
        self.assertFalse(order.internal_note)

    def test_the_token_reaches_the_order(self):
        data = self.create_ui_order_data([(self.cheese, 1)], customer=self.customer)
        data["tapradar_points_token"] = "1:2:x"
        order = self.Order.browse(self.Order.sync_from_ui([data])["pos.order"][0]["id"])
        self.assertEqual(order.tapradar_points_token, "1:2:x")

    def test_a_tampered_till_cannot_deduct_points(self):
        # No reward line, only a negative point change: still not deducted.
        order = self._order([(self.cheese, 1)], self.customer)
        order.confirm_coupon_programs({str(self.card.id): {
            "points": -150, "program_id": self.program.id, "coupon_id": self.card.id, "barcode": False,
        }})
        self.assertEqual(self.card.points, 200)

    def test_points_earned_in_the_same_order_are_kept(self):
        order = self._order([(self.cheese, 1)], self.customer)
        order.lines[:1].sudo().write({"is_reward_line": True, "coupon_id": self.card.id, "points_cost": 100})
        order.confirm_coupon_programs({str(self.card.id): {
            "points": -90, "program_id": self.program.id, "coupon_id": self.card.id, "barcode": False,
        }})
        self.assertEqual(self.card.points, 210)  # +10 earned, the 100 spent are kept
