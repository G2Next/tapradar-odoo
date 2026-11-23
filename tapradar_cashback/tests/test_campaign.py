from datetime import datetime, time, timedelta
from unittest.mock import patch

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests import tagged
from odoo.tools import mute_logger

from odoo.addons.point_of_sale.tests.common import TestPoSCommon

from .common import connect_company


class CashbackCommon(TestPoSCommon):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = cls.basic_config
        cls.deposit_categ = cls.env["product.category"].create({"name": "Deposits"})
        cls.meat = cls.create_product("Schwein Karreerose", cls.categ_basic, 100.0)
        cls.cheese = cls.create_product("Käse", cls.categ_basic, 20.0)
        cls.crate = cls.create_product("Crate deposit", cls.deposit_categ, 10.0)
        cls.customer.ref = "K-1042"
        cls.other_customer.ref = "K-2000"
        cls.today = fields.Date.today()
        connect_company(cls.env.company)

    def setUp(self):
        super().setUp()
        self.open_new_session()

    def _program(self):
        # Same as creating a gift card program in the UI.
        Program = self.env["loyalty.program"]
        return Program.browse(Program.create_from_template("gift_card")["res_id"])

    def _campaign(self, activate=True, **values):
        campaign = self.env["tapradar.cashback.campaign"].create({
            "name": "Savings Days",
            "date_from": self.today,
            "date_to": self.today,
            "tz": "UTC",
            "program_id": self._program().id,
            "valid_until": self.today + timedelta(days=30),
            "excluded_categ_ids": [(6, 0, self.deposit_categ.ids)],
            **values,
        })
        if activate:
            campaign.action_activate()
        return campaign

    def _order(self, lines, customer):
        data = self.create_ui_order_data(lines, customer=customer)
        result = self.env["pos.order"].sync_from_ui([data])
        return self.env["pos.order"].browse(result["pos.order"][0]["id"])

    def _card(self, campaign, partner):
        return campaign.card_ids.filtered(lambda c: c.partner_id == partner)

    def _after_campaign(self, campaign):
        return patch.object(
            fields.Datetime, "now",
            return_value=datetime.combine(campaign.date_to + timedelta(days=1), time(1, 0)),
        )



@tagged("post_install", "-at_install", "tapradar")
class TestCashbackCampaign(CashbackCommon):
    def test_gift_card_created_right_after_purchase(self):
        campaign = self._campaign()
        # 5 x 100 € + 3 crates of deposit (not counted): 50 € gift card.
        self._order([(self.meat, 5), (self.crate, 3)], self.customer)
        card = self._card(campaign, self.customer)
        self.assertEqual(card.points, 50.0)
        self.assertEqual(card.program_id, campaign.program_id)
        self.assertEqual(card.expiration_date, campaign.valid_until)
        self.assertTrue(card.code)

        # The next purchase adds to the same gift card.
        self._order([(self.meat, 1)], self.customer)
        self.assertEqual(self._card(campaign, self.customer).points, 60.0)
        self.assertEqual(len(campaign.card_ids), 1)

        # Below the minimum, or without a customer: nothing.
        self._order([(self.cheese, 2)], self.other_customer)
        self._order([(self.meat, 3)], False)
        self.assertFalse(self._card(campaign, self.other_customer))
        self.assertEqual(len(campaign.card_ids), 1)

    def test_refunds_reduce_and_remove_the_gift_card(self):
        campaign = self._campaign()
        self._order([(self.meat, 5)], self.customer)
        self._order([(self.meat, -1)], self.customer)
        self.assertEqual(self._card(campaign, self.customer).points, 40.0)
        self._order([(self.meat, -4)], self.customer)
        self.assertFalse(self._card(campaign, self.customer))

    def test_gift_card_cannot_be_used_during_the_campaign(self):
        campaign = self._campaign()
        self._order([(self.meat, 5)], self.customer)
        card = self._card(campaign, self.customer)

        # Odoo itself: the program only starts the day after the campaign.
        self.assertEqual(campaign.program_id.date_from, self.today + timedelta(days=1))
        self.assertEqual(campaign.program_id.date_to, campaign.valid_until)
        self.assertNotIn(campaign.program_id, self.config._get_program_ids())

        # Scanning the code at the POS gives a clear message.
        result = self.config.use_coupon_code(
            card.code, fields.Datetime.to_string(fields.Datetime.now()), self.customer.id, self.config.pricelist_id.id
        )
        self.assertFalse(result["successful"])
        self.assertIn("can only be used from", result["payload"]["error_message"])

        # And the server refuses to spend it when the order is validated.
        result = self.env["pos.order"].validate_coupon_programs({str(card.id): -10.0}, [])
        self.assertFalse(result["successful"])
        self.assertEqual(result["payload"]["removed_coupons"], [card.id])

        with self._after_campaign(campaign):
            self.assertFalse(campaign._redemption_block_message())

    def test_valid_until_is_set_by_the_admin(self):
        campaign = self._campaign()
        self._order([(self.meat, 1)], self.customer)
        new_end = self.today + timedelta(days=90)
        campaign.valid_until = new_end
        self.assertEqual(campaign.program_id.date_to, new_end)
        self.assertEqual(self._card(campaign, self.customer).expiration_date, new_end)
        with self.assertRaises(ValidationError):
            campaign.valid_until = campaign.date_to

    def test_fixed_amount_campaign(self):
        campaign = self._campaign(reward_type="fixed", fixed_amount=5.0)
        self._order([(self.meat, 1)], self.customer)
        self._order([(self.meat, 1)], self.customer)
        self._order([(self.cheese, 1)], self.customer)  # 20 €: below the minimum
        self.assertEqual(self._card(campaign, self.customer).points, 10.0)
        with self.assertRaises(ValidationError):
            campaign.fixed_amount = 0

    def test_orders_outside_the_campaign_are_ignored(self):
        yesterday = self.today - timedelta(days=1)
        campaign = self._campaign(date_from=yesterday, date_to=yesterday)
        self._order([(self.meat, 5)], self.customer)
        self.assertFalse(campaign.card_ids)

    def test_activation_includes_earlier_purchases(self):
        campaign = self._campaign(activate=False)
        self._order([(self.meat, 2)], self.customer)
        self.assertFalse(campaign.card_ids)
        campaign.action_activate()
        self.assertEqual(self._card(campaign, self.customer).points, 20.0)

    def test_pos_filter(self):
        other_config = self.config.copy({"name": "Other shop"})
        campaign = self._campaign(pos_config_ids=[(6, 0, other_config.ids)])
        self._order([(self.meat, 5)], self.customer)
        self.assertFalse(campaign.card_ids)

    def test_cron_closes_finished_campaign_without_touching_cards(self):
        campaign = self._campaign()
        self._order([(self.meat, 5)], self.customer)
        card = self._card(campaign, self.customer)
        with self._after_campaign(campaign):
            card.points = 20.0  # e.g. partially used after the campaign
            self.env["tapradar.cashback.campaign"]._cron_finish()
        self.assertEqual(campaign.state, "done")
        self.assertEqual(card.points, 20.0)
        with self.assertRaises(UserError):
            campaign.action_reset_to_draft()

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self._campaign(date_to=self.today - timedelta(days=1))
        coupons = self.env["loyalty.program"].create({"name": "Coupons", "program_type": "coupons"})
        with self.assertRaises(ValidationError):
            self._campaign(program_id=coupons.id)
        campaign = self._campaign()
        with self.assertRaises(Exception), mute_logger("odoo.sql_db"), self.cr.savepoint():
            self._campaign(program_id=campaign.program_id.id)
