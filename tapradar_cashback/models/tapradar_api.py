import hashlib
import hmac
import logging
from urllib.parse import urlencode

import requests

from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)

DEFAULT_API_URL = "https://xpadukswcayehvmpouib.supabase.co/functions/v1/odoo-cashback"
TIMEOUT = 15


def api_url(env):
    return env["ir.config_parameter"].sudo().get_param("tapradar_cashback.api_url", DEFAULT_API_URL)


def post(env, api_key, payload):
    """POST to the TapRadar Odoo endpoint. Returns the decoded JSON or raises UserError."""
    try:
        response = requests.post(
            api_url(env),
            json=payload,
            headers={"Authorization": "Bearer %s" % api_key},
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        raise UserError(env._("TapRadar cannot be reached: %s", error)) from error
    if response.status_code == 401:
        raise UserError(env._("TapRadar rejected the integration key. Create a new key in your TapRadar business account."))
    if response.status_code == 429:
        raise UserError(env._("Too many attempts. Please wait a few minutes."))
    try:
        body = response.json()
    except ValueError:
        body = {}
    if response.status_code != 200:
        raise UserError(env._("TapRadar answered with an error (%(status)s): %(error)s",
                          status=response.status_code, error=body.get("error", "")))
    return body


def link_signature(secret, integration_id, customer_ref):
    message = ("%s:%s" % (integration_id, customer_ref)).encode()
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()[:32]


def receipt_link(base, integration_id, secret, customer_ref):
    """The URL in the receipt QR code that links a customer number to a TapRadar account."""
    query = urlencode({"i": integration_id, "c": customer_ref, "s": link_signature(secret, integration_id, customer_ref)})
    return "%s?%s" % (base, query)
