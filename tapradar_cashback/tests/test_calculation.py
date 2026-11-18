from odoo.tests import BaseCase, tagged

from ..models.campaign import calculate_vouchers


def line(order_id, partner_id, euros):
    return {"order_id": order_id, "partner_id": partner_id, "cents": round(euros * 100)}


@tagged("post_install", "-at_install", "tapradar")
class TestCashbackCalculation(BaseCase):
    def test_500_euro_purchase_earns_50_euro(self):
        self.assertEqual(
            calculate_vouchers([line(1, 7, 300), line(1, 7, 200)], 10, 5000),
            [{"partner_id": 7, "eligible_cents": 50000, "voucher_cents": 5000, "order_ids": [1]}],
        )

    def test_minimum_per_order_and_per_customer(self):
        lines = [line(1, 7, 30), line(2, 7, 30)]
        self.assertEqual(calculate_vouchers(lines, 10, 5000), [])
        per_customer = calculate_vouchers(lines, 10, 5000, threshold_scope="per_customer")
        self.assertEqual(per_customer[0]["voucher_cents"], 600)
        self.assertEqual(calculate_vouchers([line(1, 7, 40)], 10, 5000, threshold_scope="per_customer"), [])

    def test_refunds_reduce_but_never_go_negative(self):
        self.assertEqual(calculate_vouchers([line(1, 7, 500), line(2, 7, -120)], 10, 5000)[0]["voucher_cents"], 3800)
        self.assertEqual(calculate_vouchers([line(1, 7, 100), line(2, 7, -100)], 10, 5000), [])

    def test_rounding(self):
        self.assertEqual(calculate_vouchers([line(1, 7, 473.09)], 10, 5000)[0]["voucher_cents"], 4730)
        self.assertEqual(
            calculate_vouchers([line(1, 7, 473.09)], 10, 5000, rounding="euro_down")[0]["voucher_cents"], 4700
        )
        # 33.33 % of 100 € must be 33.33 €, not 33.32 € through float drift.
        self.assertEqual(calculate_vouchers([line(1, 7, 100)], 33.33, 5000)[0]["voucher_cents"], 3333)

    def test_five_percent(self):
        self.assertEqual(calculate_vouchers([line(1, 7, 500)], 5, 5000)[0]["voucher_cents"], 2500)

    def test_fixed_amount_per_purchase(self):
        lines = [line(1, 7, 60), line(2, 7, 80), line(3, 7, 20)]
        # Two purchases reach 50 €: 2 x 5 €. The 20 € purchase earns nothing.
        self.assertEqual(calculate_vouchers(lines, 0, 5000, reward_type="fixed", fixed_cents=500)[0]["voucher_cents"],
                         1000)
        # A 50 € refund leaves 90 €, which covers the minimum only once.
        refunded = lines + [line(4, 7, -50)]
        self.assertEqual(
            calculate_vouchers(refunded, 0, 5000, reward_type="fixed", fixed_cents=500)[0]["voucher_cents"], 500
        )

    def test_fixed_amount_once_per_customer(self):
        lines = [line(1, 7, 30), line(2, 7, 30)]
        result = calculate_vouchers(lines, 0, 5000, threshold_scope="per_customer", reward_type="fixed",
                                    fixed_cents=1000)
        self.assertEqual(result[0]["voucher_cents"], 1000)

    def test_invalid_reward(self):
        with self.assertRaises(ValueError):
            calculate_vouchers([], 0, 5000)
        with self.assertRaises(ValueError):
            calculate_vouchers([], 10, 5000, reward_type="fixed", fixed_cents=0)
