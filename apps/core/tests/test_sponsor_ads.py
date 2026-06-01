import json

import pytest
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from apps.core.models import SponsorAdPurchase
from apps.core.payments import create_ads_checkout_session


@pytest.mark.django_db
class TestSponsorAdsCheckout:
    @override_settings(STRIPE_SECRET_KEY="sk_test", STRIPE_AWESOME_ADS_PRICE_ID="price_test")
    def test_checkout_payload_does_not_send_customer_update_without_customer(
        self,
        monkeypatch,
    ):
        captured = {}

        def fake_stripe_request(method, path, data):
            captured.update({"method": method, "path": path, "data": data})
            return {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay"}

        monkeypatch.setattr("apps.core.payments._stripe_request", fake_stripe_request)

        create_ads_checkout_session(success_url="https://example.com/success", cancel_url="/")

        assert captured["path"] == "checkout/sessions"
        assert captured["data"]["customer_creation"] == "always"
        assert "customer_update[name]" not in captured["data"]

    @override_settings(STRIPE_SECRET_KEY="sk_test", STRIPE_AWESOME_ADS_PRICE_ID="price_test")
    def test_checkout_creates_purchase_and_redirects_to_stripe(self, client, monkeypatch):
        monkeypatch.setattr(
            "apps.core.views.create_ads_checkout_session",
            lambda **kwargs: {"id": "cs_test_123", "url": "https://checkout.stripe.com/c/pay"},
        )

        response = client.post(reverse("sponsor_checkout"))

        assert response.status_code == 302
        assert response.url == "https://checkout.stripe.com/c/pay"
        assert SponsorAdPurchase.objects.filter(stripe_checkout_session_id="cs_test_123").exists()

    @override_settings(STRIPE_SECRET_KEY="sk_test", STRIPE_AWESOME_ADS_PRICE_ID="price_test")
    def test_success_page_still_renders_when_notification_email_fails(
        self,
        client,
        monkeypatch,
    ):
        SponsorAdPurchase.objects.create(
            stripe_checkout_session_id="cs_test_paid",
            status=SponsorAdPurchase.Status.CHECKOUT_STARTED,
        )
        monkeypatch.setattr(
            "apps.core.views.retrieve_checkout_session",
            lambda session_id: {
                "id": session_id,
                "payment_status": "paid",
                "amount_total": 100000,
                "currency": "usd",
                "customer_details": {"email": "buyer@example.com"},
                "metadata": {"app": "awesome"},
            },
        )
        monkeypatch.setattr(
            "apps.core.views.notify_sponsor_payment",
            lambda purchase: (_ for _ in ()).throw(RuntimeError("smtp down")),
        )

        response = client.get(reverse("sponsor_success"), {"session_id": "cs_test_paid"})

        assert response.status_code == 200
        assert b"Send us your ad details" in response.content

    def test_success_form_requires_startup_name(self, client, monkeypatch):
        purchase = SponsorAdPurchase.objects.create(
            stripe_checkout_session_id="cs_test_paid",
            status=SponsorAdPurchase.Status.PAID,
            buyer_email="buyer@example.com",
            amount_total=100000,
            currency="usd",
        )
        monkeypatch.setattr("apps.core.views.stripe_configured", lambda: False)
        logo = SimpleUploadedFile(
            "logo.gif",
            b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )

        response = client.post(
            reverse("sponsor_success"),
            {
                "session_id": purchase.stripe_checkout_session_id,
                "startup_name": "",
                "short_description": "Reliable agent workflows for busy teams.",
                "logo": logo,
            },
        )

        assert response.status_code == 200
        purchase.refresh_from_db()
        assert purchase.status == SponsorAdPurchase.Status.PAID
        assert b"This field is required" in response.content

    def test_success_form_saves_active_ad_details(self, client, monkeypatch):
        purchase = SponsorAdPurchase.objects.create(
            stripe_checkout_session_id="cs_test_paid",
            status=SponsorAdPurchase.Status.PAID,
            buyer_email="buyer@example.com",
            amount_total=100000,
            currency="usd",
        )
        monkeypatch.setattr("apps.core.views.stripe_configured", lambda: False)
        logo = SimpleUploadedFile(
            "logo.gif",
            b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )

        response = client.post(
            reverse("sponsor_success"),
            {
                "session_id": purchase.stripe_checkout_session_id,
                "startup_name": "Acme AI",
                "short_description": "Reliable agent workflows for busy teams.",
                "logo": logo,
            },
        )

        assert response.status_code == 302
        purchase.refresh_from_db()
        assert purchase.status == SponsorAdPurchase.Status.ACTIVE
        assert purchase.startup_name == "Acme AI"
        assert purchase.short_description == "Reliable agent workflows for busy teams."
        assert purchase.details_submitted_at is not None

    def test_active_sponsor_ad_caches_empty_result(self, django_assert_num_queries):
        from apps.core.context_processors import active_sponsor_ad

        cache.delete("awesome:active_sponsor_ad")

        with django_assert_num_queries(1):
            assert active_sponsor_ad(None) == {"awesome_sponsor_ad": None}
        with django_assert_num_queries(0):
            assert active_sponsor_ad(None) == {"awesome_sponsor_ad": None}

    def test_active_sponsor_ad_excludes_active_purchase_without_startup_name(self):
        from apps.core.context_processors import active_sponsor_ad

        cache.delete("awesome:active_sponsor_ad")
        SponsorAdPurchase.objects.create(
            stripe_checkout_session_id="cs_test_blank_active",
            status=SponsorAdPurchase.Status.ACTIVE,
            buyer_email="buyer@example.com",
            short_description="Reliable agent workflows for busy teams.",
        )

        assert active_sponsor_ad(None) == {"awesome_sponsor_ad": None}

    def test_notification_is_deduplicated_with_atomic_claim(self, monkeypatch):
        purchase = SponsorAdPurchase.objects.create(
            stripe_checkout_session_id="cs_test_notify",
            status=SponsorAdPurchase.Status.PAID,
            buyer_email="buyer@example.com",
            amount_total=100000,
            currency="usd",
        )
        sent = []
        monkeypatch.setattr("apps.core.views.send_mail", lambda **kwargs: sent.append(kwargs))

        from apps.core.views import notify_sponsor_payment

        notify_sponsor_payment(purchase)
        purchase.refresh_from_db()
        notify_sponsor_payment(purchase)

        assert len(sent) == 1
        assert purchase.notification_sent_at is not None

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    def test_webhook_rejects_invalid_signature(self, client):
        response = client.post(
            reverse("stripe_webhook"),
            data=json.dumps({"type": "checkout.session.completed"}),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=bad",
        )

        assert response.status_code == 403

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    def test_webhook_marks_purchase_paid_and_sends_notification(self, client, monkeypatch):
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_paid",
                    "payment_status": "paid",
                    "amount_total": 100000,
                    "currency": "usd",
                    "customer": "cus_123",
                    "payment_intent": "pi_123",
                    "customer_details": {"email": "buyer@example.com", "name": "Buyer"},
                    "metadata": {"app": "awesome"},
                }
            },
        }
        monkeypatch.setattr(
            "apps.core.views.verify_webhook_signature",
            lambda *args, **kwargs: True,
        )
        notified = []
        monkeypatch.setattr(
            "apps.core.views.notify_sponsor_payment",
            lambda purchase: notified.append(purchase.id),
        )

        response = client.post(
            reverse("stripe_webhook"),
            data=json.dumps(event),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        assert response.status_code == 200
        purchase = SponsorAdPurchase.objects.get(stripe_checkout_session_id="cs_test_paid")
        assert purchase.status == SponsorAdPurchase.Status.PAID
        assert purchase.buyer_email == "buyer@example.com"
        assert notified == [purchase.id]
