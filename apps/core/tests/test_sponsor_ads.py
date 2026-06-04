import json
from urllib.error import URLError

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
    def test_checkout_wraps_stripe_network_errors(self, monkeypatch):
        from apps.core.payments import StripeRequestError

        def raise_network_error(*args, **kwargs):
            raise URLError("dns failure")

        monkeypatch.setattr("apps.core.payments.urlopen", raise_network_error)

        with pytest.raises(StripeRequestError, match="dns failure"):
            create_ads_checkout_session(success_url="https://example.com/success", cancel_url="/")

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

    def test_success_form_saves_active_ad_details(self, auth_client, user, monkeypatch):
        purchase = SponsorAdPurchase.objects.create(
            stripe_checkout_session_id="cs_test_paid",
            status=SponsorAdPurchase.Status.PAID,
            buyer_email="buyer@example.com",
            amount_total=100000,
            currency="usd",
        )
        events = []
        monkeypatch.setattr("apps.core.views.stripe_configured", lambda: False)
        monkeypatch.setattr(
            "apps.core.views.queue_track_event",
            lambda **kwargs: events.append(kwargs),
        )
        logo = SimpleUploadedFile(
            "logo.gif",
            b"GIF87a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff,\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;",
            content_type="image/gif",
        )

        response = auth_client.post(
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
        assert events == [
            {
                "event_name": "sponsor_ad_details_submitted",
                "profile_id": user.profile.id,
                "distinct_id": "stripe_checkout:cs_test_paid",
                "properties": {
                    "product": "sponsor_ads",
                    "transaction_id": "cs_test_paid",
                },
                "source_function": "sponsor_success",
            }
        ]

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
    def test_webhook_marks_purchase_paid_and_sends_notification(
        self,
        client,
        django_user_model,
        monkeypatch,
    ):
        user = django_user_model.objects.create_user(username="buyer", password="pw")
        profile = user.profile
        events = []
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
                    "client_reference_id": str(user.id),
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
        monkeypatch.setattr(
            "apps.core.views.queue_track_event",
            lambda **kwargs: events.append(kwargs),
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
        assert events == [
            {
                "event_name": "purchase_completed",
                "profile_id": profile.id,
                "distinct_id": "stripe_checkout:cs_test_paid",
                "properties": {
                    "product": "sponsor_ads",
                    "value": 1000,
                    "currency": "usd",
                    "transaction_id": "cs_test_paid",
                },
                "source_function": "sponsor_ads checkout completion",
            }
        ]


@pytest.mark.django_db
class TestHighlightedRepoCheckout:
    @override_settings(
        STRIPE_SECRET_KEY="sk_test",
        STRIPE_AWESOME_HIGHLIGHTED_REPO_PRICE_ID="price_highlighted",
    )
    def test_highlighted_checkout_payload_uses_highlighted_repo_product(self, monkeypatch):
        from apps.core.payments import create_highlighted_repo_checkout_session

        captured = {}

        def fake_stripe_request(method, path, data):
            captured.update({"method": method, "path": path, "data": data})
            return {"id": "cs_test_highlight", "url": "https://checkout.stripe.com/c/pay"}

        monkeypatch.setattr("apps.core.payments._stripe_request", fake_stripe_request)

        create_highlighted_repo_checkout_session(
            success_url="https://example.com/highlight/success",
            cancel_url="/",
        )

        assert captured["path"] == "checkout/sessions"
        assert captured["data"]["line_items[0][price]"] == "price_highlighted"
        assert captured["data"]["metadata[kind]"] == "highlighted_repo"
        assert captured["data"]["metadata[duration]"] == "7_days"

    @override_settings(
        STRIPE_SECRET_KEY="sk_test",
        STRIPE_AWESOME_HIGHLIGHTED_REPO_PRICE_ID="price_highlighted",
    )
    def test_highlighted_checkout_creates_purchase_and_redirects_to_stripe(
        self, client, monkeypatch
    ):
        from apps.core.models import HighlightedRepoPurchase

        monkeypatch.setattr(
            "apps.core.views.create_highlighted_repo_checkout_session",
            lambda **kwargs: {"id": "cs_test_highlight", "url": "https://checkout.stripe.com/c/pay"},
        )

        response = client.post(reverse("highlighted_repo_checkout"))

        assert response.status_code == 302
        assert response.url == "https://checkout.stripe.com/c/pay"
        assert HighlightedRepoPurchase.objects.filter(
            stripe_checkout_session_id="cs_test_highlight"
        ).exists()

    def test_highlighted_success_form_saves_active_repo_details(
        self,
        auth_client,
        user,
        monkeypatch,
    ):
        from apps.core.models import HighlightedRepoPurchase

        purchase = HighlightedRepoPurchase.objects.create(
            stripe_checkout_session_id="cs_test_highlight_paid",
            status=HighlightedRepoPurchase.Status.PAID,
            buyer_email="buyer@example.com",
            amount_total=50000,
            currency="usd",
        )
        events = []
        monkeypatch.setattr("apps.core.views.highlighted_repo_checkout_configured", lambda: False)
        monkeypatch.setattr(
            "apps.core.views.queue_track_event",
            lambda **kwargs: events.append(kwargs),
        )

        response = auth_client.post(
            reverse("highlighted_repo_success"),
            {
                "session_id": purchase.stripe_checkout_session_id,
                "repo_full_name": "LVTD-LLC/awesome",
                "repo_url": "https://github.com/LVTD-LLC/awesome",
                "short_description": "Search every repo hiding inside awesome lists.",
            },
        )

        assert response.status_code == 302
        purchase.refresh_from_db()
        assert purchase.status == HighlightedRepoPurchase.Status.ACTIVE
        assert purchase.repo_full_name == "LVTD-LLC/awesome"
        assert purchase.active_until is not None
        assert events == [
            {
                "event_name": "highlighted_repo_details_submitted",
                "profile_id": user.profile.id,
                "distinct_id": "stripe_checkout:cs_test_highlight_paid",
                "properties": {
                    "product": "highlighted_repo",
                    "transaction_id": "cs_test_highlight_paid",
                },
                "source_function": "highlighted_repo_success",
            }
        ]

    def test_highlighted_success_form_does_not_reset_active_window(self, client, monkeypatch):
        from datetime import timedelta

        from django.utils import timezone

        from apps.core.models import HighlightedRepoPurchase

        original_details_time = timezone.now() - timedelta(days=3)
        purchase = HighlightedRepoPurchase.objects.create(
            stripe_checkout_session_id="cs_test_highlight_active",
            status=HighlightedRepoPurchase.Status.ACTIVE,
            buyer_email="buyer@example.com",
            amount_total=50000,
            currency="usd",
            repo_full_name="LVTD-LLC/awesome",
            repo_url="https://github.com/LVTD-LLC/awesome",
            short_description="Original placement.",
            details_submitted_at=original_details_time,
        )
        monkeypatch.setattr("apps.core.views.highlighted_repo_checkout_configured", lambda: False)

        response = client.post(
            reverse("highlighted_repo_success"),
            {
                "session_id": purchase.stripe_checkout_session_id,
                "repo_full_name": "LVTD-LLC/awesome",
                "repo_url": "https://github.com/LVTD-LLC/awesome",
                "short_description": "Updated copy without extending the placement.",
            },
        )

        assert response.status_code == 302
        purchase.refresh_from_db()
        assert purchase.status == HighlightedRepoPurchase.Status.ACTIVE
        assert purchase.short_description == "Updated copy without extending the placement."
        assert purchase.details_submitted_at == original_details_time

    def test_active_highlighted_repo_excludes_expired_purchase(self):
        from datetime import timedelta

        from django.utils import timezone

        from apps.core.context_processors import active_highlighted_repo
        from apps.core.models import HighlightedRepoPurchase

        cache.delete("awesome:active_highlighted_repo")
        HighlightedRepoPurchase.objects.create(
            stripe_checkout_session_id="cs_test_old_highlight",
            status=HighlightedRepoPurchase.Status.ACTIVE,
            repo_full_name="old/repo",
            repo_url="https://github.com/old/repo",
            short_description="Expired placement.",
            details_submitted_at=timezone.now() - timedelta(days=8),
        )

        assert active_highlighted_repo(None) == {"awesome_highlighted_repo": None}

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    def test_webhook_routes_highlighted_repo_payment(self, client, django_user_model, monkeypatch):
        from apps.core.models import HighlightedRepoPurchase, SponsorAdPurchase

        user = django_user_model.objects.create_user(username="buyer", password="pw")
        profile = user.profile
        events = []
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_highlight_paid",
                    "payment_status": "paid",
                    "amount_total": 50000,
                    "currency": "usd",
                    "customer_details": {"email": "buyer@example.com"},
                    "client_reference_id": str(user.id),
                    "metadata": {"app": "awesome", "kind": "highlighted_repo"},
                }
            },
        }
        monkeypatch.setattr(
            "apps.core.views.verify_webhook_signature",
            lambda *args, **kwargs: True,
        )
        notified = []
        monkeypatch.setattr(
            "apps.core.views.notify_highlighted_repo_payment",
            lambda purchase: notified.append(purchase.id),
        )
        monkeypatch.setattr(
            "apps.core.views.queue_track_event",
            lambda **kwargs: events.append(kwargs),
        )

        response = client.post(
            reverse("stripe_webhook"),
            data=json.dumps(event),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        assert response.status_code == 200
        assert HighlightedRepoPurchase.objects.filter(
            stripe_checkout_session_id="cs_test_highlight_paid",
            status=HighlightedRepoPurchase.Status.PAID,
        ).exists()
        assert not SponsorAdPurchase.objects.filter(
            stripe_checkout_session_id="cs_test_highlight_paid"
        ).exists()
        assert len(notified) == 1
        assert events == [
            {
                "event_name": "purchase_completed",
                "profile_id": profile.id,
                "distinct_id": "stripe_checkout:cs_test_highlight_paid",
                "properties": {
                    "product": "highlighted_repo",
                    "value": 500,
                    "currency": "usd",
                    "transaction_id": "cs_test_highlight_paid",
                },
                "source_function": "highlighted_repo checkout completion",
            }
        ]


@pytest.mark.django_db
class TestRemoveAdsCheckout:
    @override_settings(
        STRIPE_SECRET_KEY="sk_test",
        STRIPE_AWESOME_REMOVE_ADS_PRICE_ID="price_remove_ads",
    )
    def test_remove_ads_checkout_payload_uses_remove_ads_product(self, monkeypatch):
        from apps.core.payments import create_remove_ads_checkout_session

        captured = {}

        def fake_stripe_request(method, path, data):
            captured.update({"method": method, "path": path, "data": data})
            return {"id": "cs_test_remove_ads", "url": "https://checkout.stripe.com/c/pay"}

        monkeypatch.setattr("apps.core.payments._stripe_request", fake_stripe_request)

        create_remove_ads_checkout_session(
            success_url="https://example.com/settings?remove_ads=success",
            cancel_url="https://example.com/settings",
            client_reference_id="123",
        )

        assert captured["path"] == "checkout/sessions"
        assert captured["data"]["line_items[0][price]"] == "price_remove_ads"
        assert captured["data"]["metadata[kind]"] == "remove_ads"
        assert captured["data"]["metadata[duration]"] == "lifetime"
        assert captured["data"]["client_reference_id"] == "123"

    @override_settings(
        STRIPE_SECRET_KEY="sk_test",
        STRIPE_AWESOME_REMOVE_ADS_PRICE_ID="price_remove_ads",
    )
    def test_remove_ads_checkout_redirects_authenticated_user(
        self, client, django_user_model, monkeypatch
    ):
        user = django_user_model.objects.create_user(username="buyer", password="pw")
        client.force_login(user)
        monkeypatch.setattr(
            "apps.core.views.create_remove_ads_checkout_session",
            lambda **kwargs: {"id": "cs_test_remove_ads", "url": "https://checkout.stripe.com/c/pay"},
        )

        response = client.post(reverse("remove_ads_checkout"))

        assert response.status_code == 302
        assert response.url == "https://checkout.stripe.com/c/pay"

    @override_settings(STRIPE_WEBHOOK_SECRET="whsec_test")
    def test_webhook_enables_remove_ads_on_profile(self, client, django_user_model, monkeypatch):
        user = django_user_model.objects.create_user(username="buyer", password="pw")
        profile = user.profile
        events = []
        event = {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "id": "cs_test_remove_ads_paid",
                    "amount_total": 400,
                    "currency": "usd",
                    "payment_status": "paid",
                    "client_reference_id": str(user.id),
                    "metadata": {"app": "awesome", "kind": "remove_ads"},
                }
            },
        }
        monkeypatch.setattr(
            "apps.core.views.verify_webhook_signature",
            lambda *args, **kwargs: True,
        )
        monkeypatch.setattr(
            "apps.core.views.queue_track_event",
            lambda **kwargs: events.append(kwargs),
        )

        response = client.post(
            reverse("stripe_webhook"),
            data=json.dumps(event),
            content_type="application/json",
            HTTP_STRIPE_SIGNATURE="t=1,v1=ok",
        )

        assert response.status_code == 200
        profile.refresh_from_db()
        assert profile.remove_ads is True
        assert events == [
            {
                "event_name": "purchase_completed",
                "profile_id": profile.id,
                "distinct_id": "stripe_checkout:cs_test_remove_ads_paid",
                "properties": {
                    "product": "remove_ads",
                    "value": 4,
                    "currency": "usd",
                    "transaction_id": "cs_test_remove_ads_paid",
                },
                "source_function": "remove_ads checkout completion",
            }
        ]

    @override_settings(
        STRIPE_SECRET_KEY="sk_test",
        STRIPE_AWESOME_REMOVE_ADS_PRICE_ID="price_remove_ads",
    )
    def test_remove_ads_success_requires_remove_ads_checkout_kind(
        self, client, django_user_model, monkeypatch
    ):
        user = django_user_model.objects.create_user(username="buyer", password="pw")
        profile = user.profile
        client.force_login(user)
        monkeypatch.setattr(
            "apps.core.views.retrieve_checkout_session",
            lambda session_id: {
                "id": session_id,
                "payment_status": "paid",
                "client_reference_id": str(user.id),
                "metadata": {"app": "awesome", "kind": "highlighted_repo"},
            },
        )

        response = client.get(
            reverse("settings"),
            {"remove_ads": "success", "session_id": "cs_paid_highlight"},
        )

        assert response.status_code == 200
        profile.refresh_from_db()
        assert profile.remove_ads is False

    @override_settings(
        STRIPE_SECRET_KEY="sk_test",
        STRIPE_AWESOME_REMOVE_ADS_PRICE_ID="price_remove_ads",
    )
    def test_remove_ads_success_requires_current_user_checkout_session(
        self, client, django_user_model, monkeypatch
    ):
        buyer = django_user_model.objects.create_user(username="buyer", password="pw")
        other_user = django_user_model.objects.create_user(username="other", password="pw")
        profile = buyer.profile
        client.force_login(buyer)
        monkeypatch.setattr(
            "apps.core.views.retrieve_checkout_session",
            lambda session_id: {
                "id": session_id,
                "payment_status": "paid",
                "client_reference_id": str(other_user.id),
                "metadata": {"app": "awesome", "kind": "remove_ads"},
            },
        )

        response = client.get(
            reverse("settings"),
            {"remove_ads": "success", "session_id": "cs_paid_other_user"},
        )

        assert response.status_code == 200
        profile.refresh_from_db()
        other_user.profile.refresh_from_db()
        assert profile.remove_ads is False
        assert other_user.profile.remove_ads is False

    def test_remove_ads_profile_hides_sponsor_and_highlighted_ads(
        self, django_user_model, rf, monkeypatch
    ):
        from apps.core.context_processors import active_highlighted_repo, active_sponsor_ad

        user = django_user_model.objects.create_user(username="buyer", password="pw")
        profile = user.profile
        profile.remove_ads = True
        profile.save(update_fields=["remove_ads", "updated_at"])
        request = rf.get("/settings")
        request.user = user

        with monkeypatch.context() as m:
            m.setattr("apps.core.context_processors.cache.get", lambda *args, **kwargs: None)
            assert active_sponsor_ad(request) == {"awesome_sponsor_ad": None}
            assert active_highlighted_repo(request) == {"awesome_highlighted_repo": None}
