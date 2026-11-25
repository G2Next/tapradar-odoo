from odoo.modules.neutralize import get_neutralization_queries
from odoo.tests import TransactionCase, tagged

from .common import connect_company


@tagged("post_install", "-at_install", "tapradar")
class TestNeutralize(TransactionCase):
    def test_neutralize_disconnects_tapradar(self):
        company = self.env.company
        connect_company(company)
        event = self.env["tapradar.cashback.event"].create({
            "company_id": company.id,
            "external_id": "neutralize-test",
            "payload": "{}",
        })
        self.assertTrue(company._tapradar_connected())

        queries = list(get_neutralization_queries(["tapradar_cashback"]))
        self.assertEqual(len(queries), 1)
        self.env.flush_all()
        self.env.cr.execute(queries[0])
        self.env.invalidate_all()

        self.assertFalse(company._tapradar_connected())
        self.assertFalse(company.sudo().tapradar_api_key)
        self.assertFalse(company.sudo().tapradar_link_secret)
        self.assertFalse(company.tapradar_integration_id)
        self.assertEqual(event.state, "failed")
        self.assertEqual(event.last_error, "Database neutralized")
