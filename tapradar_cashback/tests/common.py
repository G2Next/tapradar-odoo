import json
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

INTEGRATION_ID = "3f2b8c1e-5d4a-4b7e-9a10-2c3d4e5f6a7b"
API = "odoo.addons.tapradar_cashback.models.tapradar_api.requests.post"


def connect_company(company):
    """Mark a company as connected to TapRadar without calling the network."""
    company.sudo().write({
        "tapradar_api_key": "trk_" + "a1" * 24,
        "tapradar_link_secret": "s3cret",
        "tapradar_integration_id": INTEGRATION_ID,
        "tapradar_shop_name": "Feinkost Muster",
        "tapradar_link_base": "https://www.tapradar.app/l/odoo",
        "tapradar_data_consent": True,
    })


def response(status=200, body=None):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = body or {}
    return mock


@contextmanager
def tapradar_api(handler=None):
    """Patch the HTTP call. `handler(payload) -> (status, body)`; default: accept every card."""
    sent = []

    def fake_post(url, json=None, headers=None, timeout=None):
        sent.append({"url": url, "payload": json, "headers": headers})
        if handler:
            return response(*handler(json))
        if json["type"] == "gift_cards":
            return response(200, {"ok": True, "results": [
                {"external_id": card["external_id"], "ok": True, "linked": False} for card in json["cards"]
            ]})
        return response(200, {"ok": True})

    with patch(API, side_effect=fake_post):
        yield sent


def queued(env):
    return [json.loads(event.payload) for event in env["tapradar.cashback.event"].search([("state", "=", "pending")])]
