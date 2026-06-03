import hashlib
import hmac
import json
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.conf import settings


class StripeConfigurationError(Exception):
    pass


class StripeRequestError(Exception):
    pass


def stripe_configured():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_AWESOME_ADS_PRICE_ID)


def highlighted_repo_checkout_configured():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_AWESOME_HIGHLIGHTED_REPO_PRICE_ID)


def remove_ads_checkout_configured():
    return bool(settings.STRIPE_SECRET_KEY and settings.STRIPE_AWESOME_REMOVE_ADS_PRICE_ID)


def _stripe_headers():
    if not settings.STRIPE_SECRET_KEY:
        raise StripeConfigurationError("Stripe secret key is not configured.")

    headers = {
        "Authorization": f"Bearer {settings.STRIPE_SECRET_KEY}",
        "Content-Type": "application/x-www-form-urlencoded",
        "Stripe-Version": settings.STRIPE_API_VERSION,
    }
    if settings.STRIPE_CONTEXT:
        headers["Stripe-Context"] = settings.STRIPE_CONTEXT
    return headers


def _stripe_request(method, path, data=None):
    encoded_data = None
    if data is not None:
        encoded_data = urlencode(data).encode("utf-8")

    request = Request(
        f"https://api.stripe.com/v1/{path.lstrip('/')}",
        data=encoded_data,
        headers=_stripe_headers(),
        method=method,
    )
    try:
        with urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise StripeRequestError(body) from exc
    except URLError as exc:
        raise StripeRequestError(str(exc.reason)) from exc


def _create_checkout_session(
    *, price_id, success_url, cancel_url, kind, duration, client_reference_id=""
):
    if not price_id:
        raise StripeConfigurationError(f"Stripe price ID is not configured for {kind}.")

    payload = {
        "mode": "payment",
        "line_items[0][price]": price_id,
        "line_items[0][quantity]": "1",
        "success_url": success_url,
        "cancel_url": cancel_url,
        "customer_creation": "always",
        "allow_promotion_codes": "false",
        "billing_address_collection": "auto",
        "metadata[app]": "awesome",
        "metadata[kind]": kind,
        "metadata[duration]": duration,
        "payment_intent_data[metadata][app]": "awesome",
        "payment_intent_data[metadata][kind]": kind,
    }
    if client_reference_id:
        payload["client_reference_id"] = client_reference_id

    return _stripe_request("POST", "checkout/sessions", payload)


def create_ads_checkout_session(*, success_url, cancel_url, client_reference_id=""):
    return _create_checkout_session(
        price_id=settings.STRIPE_AWESOME_ADS_PRICE_ID,
        success_url=success_url,
        cancel_url=cancel_url,
        kind="sponsor_ads",
        duration="1_month",
        client_reference_id=client_reference_id,
    )


def create_highlighted_repo_checkout_session(*, success_url, cancel_url, client_reference_id=""):
    return _create_checkout_session(
        price_id=settings.STRIPE_AWESOME_HIGHLIGHTED_REPO_PRICE_ID,
        success_url=success_url,
        cancel_url=cancel_url,
        kind="highlighted_repo",
        duration="7_days",
        client_reference_id=client_reference_id,
    )


def create_remove_ads_checkout_session(*, success_url, cancel_url, client_reference_id=""):
    return _create_checkout_session(
        price_id=settings.STRIPE_AWESOME_REMOVE_ADS_PRICE_ID,
        success_url=success_url,
        cancel_url=cancel_url,
        kind="remove_ads",
        duration="lifetime",
        client_reference_id=client_reference_id,
    )


def retrieve_checkout_session(session_id):
    return _stripe_request(
        "GET",
        f"checkout/sessions/{session_id}?expand[]=customer&expand[]=payment_intent",
    )


def verify_webhook_signature(payload, signature_header, secret, tolerance=300):
    if not secret:
        raise StripeConfigurationError("Stripe webhook secret is not configured.")
    if not signature_header:
        return False

    parts = {}
    for item in signature_header.split(","):
        if "=" not in item:
            continue
        key, value = item.split("=", 1)
        parts.setdefault(key, []).append(value)

    try:
        timestamp = int(parts.get("t", [""])[0])
    except ValueError:
        return False

    if abs(time.time() - timestamp) > tolerance:
        return False

    expected = hmac.new(
        secret.encode("utf-8"),
        f"{timestamp}.".encode() + payload,
        hashlib.sha256,
    ).hexdigest()
    return any(hmac.compare_digest(expected, signature) for signature in parts.get("v1", []))
