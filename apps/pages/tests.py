import time

import pytest
from allauth.account.models import EmailAddress
from allauth.mfa.models import Authenticator
from allauth.mfa.recovery_codes.internal.auth import RecoveryCodes
from allauth.socialaccount.models import SocialAccount
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.messages import get_messages
from django.template.loader import render_to_string
from django.urls import reverse

pytestmark = pytest.mark.django_db


def assert_standard_ad_layout(content):
    assert "data-page-ad-shell" in content
    assert "data-page-content" in content
    assert 'data-ad-rail="left"' in content
    assert 'data-ad-rail="right"' in content
    assert "grid-rows-5" in content
    assert content.count('data-ad-slot="global-left-') == 5
    assert content.count('data-ad-slot="global-right-') == 5
    assert content.count("data-ad-slot=") == 10
    assert content.count("data-ad-empty-slot=") == 1
    assert 'data-ad-empty-slot="global-right-5"' in content
    assert "Get sponsored" in content
    assert content.count("utm_source=awesome_repos") == 9
    assert content.count("utm_medium=side_ad") == 9
    assert "mailto:rasul@lvtd.dev?subject=Sponsor%20Awesome" in content


def test_side_ad_slot_default_sponsor_email():
    content = render_to_string(
        "components/side_ad_slot.html",
        {
            "slot_id": "test-slot",
            "position": "Test rail",
            "headline": "Sponsor Awesome",
            "body": "Reach developers browsing curated GitHub projects.",
            "cta": "Reserve",
        },
    )

    assert "mailto:rasul@lvtd.dev?subject=Sponsor%20Awesome" in content


def mark_password_reauthenticated(client, username):
    session = client.session
    session["account_authentication_methods"] = [
        {"method": "password", "at": time.time(), "username": username}
    ]
    session.save()


def test_login_page_is_github_only(client, settings):
    settings.SOCIALACCOUNT_PROVIDERS = {"github": {"APP": {"client_id": "x", "secret": "y"}}}

    response = client.get(reverse("account_login"))
    assert response.status_code == 200

    content = response.content.decode()
    assert "Continue with GitHub" in content
    assert "/accounts/github/login/" in content
    # Email/password and passkey login are gone.
    assert 'type="password"' not in content
    assert "Sign in with a passkey" not in content


def test_signup_page_is_github_only(client, settings):
    settings.SOCIALACCOUNT_PROVIDERS = {"github": {"APP": {"client_id": "x", "secret": "y"}}}

    response = client.get(reverse("account_signup"))
    assert response.status_code == 200

    content = response.content.decode()
    assert "Sign up with GitHub" in content
    assert "/accounts/github/login/" in content
    # Email/password and passkey signup are gone.
    assert 'type="password"' not in content
    assert "Sign up using a passkey" not in content


def test_email_signup_post_is_disabled(client, settings):
    settings.POSTHOG_API_KEY = ""

    response = client.post(
        reverse("account_signup"),
        data={
            "email": "newuser@example.com",
            "password1": "strong-test-pass-123",
        },
    )

    # GitHub is the only signup path: the POST is rejected and no account is made.
    assert response.status_code == 302
    assert response["Location"] == reverse("account_signup")
    assert not get_user_model().objects.filter(email="newuser@example.com").exists()


def test_dashboard_does_not_show_email_confirmation_reminder(client):
    user = get_user_model().objects.create_user(
        username="unverified",
        email="unverified@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": False},
    )
    client.force_login(user)

    response = client.get(reverse("home"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Your email is not yet confirmed" not in content
    assert "Welcome to Awesome" in content


def test_landing_page_shows_github_button_for_anonymous_users(client, settings):
    settings.SOCIALACCOUNT_PROVIDERS = {"github": {"APP": {"client_id": "x", "secret": "y"}}}

    response = client.get(reverse("repos:search"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "brand/awesome-repos-mark.svg" in content
    assert "brand/apple-touch-icon.png" in content
    assert "brand/awesome-repos-social.png" in content
    assert "Search every repository hiding inside awesome lists." in content
    assert "Browse awesome lists" in content
    # GitHub is the sole auth entry point; the old email-based buttons are gone.
    assert "Continue with GitHub" in content
    assert "/accounts/github/login/" in content
    assert "Start for Free" not in content


def test_landing_page_hides_github_button_for_authenticated_users(client):
    user = get_user_model().objects.create_user(
        username="loggedin",
        email="loggedin@example.com",
        password="strong-test-pass-123",
    )
    client.force_login(user)

    response = client.get(reverse("repos:search"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Continue with GitHub" not in content


def test_public_pages_use_standard_ad_layout(client):
    response = client.get(reverse("repos:search"))

    assert response.status_code == 200
    assert_standard_ad_layout(response.content.decode())


def test_app_pages_use_standard_ad_layout(client):
    user = get_user_model().objects.create_user(
        username="layoutuser",
        email="layoutuser@example.com",
        password="strong-test-pass-123",
    )
    client.force_login(user)

    response = client.get(reverse("home"))

    assert response.status_code == 200
    assert_standard_ad_layout(response.content.decode())


def test_settings_shows_email_confirmation_without_passkey_controls(client):
    user = get_user_model().objects.create_user(
        username="settingsuser",
        email="settingsuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": False},
    )
    client.force_login(user)

    response = client.get(reverse("settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Your email is not yet confirmed" in content
    assert "Add passkey" not in content
    assert reverse("mfa_add_webauthn") not in content
    assert "API key" not in content
    assert "Repository updates" in content
    assert "handleDeleteAccountTab($event)" in content


def test_settings_handles_users_without_allauth_email_address(client):
    user = get_user_model().objects.create_user(
        username="noemailaddress",
        email="noemailaddress@example.com",
        password="strong-test-pass-123",
    )
    client.force_login(user)

    response = client.get(reverse("settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Your email is not yet confirmed" in content
    assert "Add passkey" not in content
    assert reverse("mfa_add_webauthn") not in content
    assert "Repository updates" in content


def test_settings_hides_email_confirmation_when_email_confirmed(client):
    user = get_user_model().objects.create_user(
        username="verifiedsettingsuser",
        email="verifiedsettingsuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    client.force_login(user)

    response = client.get(reverse("settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Your email is not yet confirmed" not in content
    assert "Confirmation needed" not in content
    assert "GitHub connection" in content
    assert "Add passkey" not in content
    assert reverse("mfa_add_webauthn") not in content
    assert "openDeleteAccount()" in content


def test_settings_hides_passkey_management_when_passkey_exists(client):
    user = get_user_model().objects.create_user(
        username="passkeyuser",
        email="passkeyuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.WEBAUTHN,
        data={"name": "Test passkey"},
    )
    client.force_login(user)

    response = client.get(reverse("settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Passkeys" not in content
    assert "Manage passkeys" not in content
    assert "Generate recovery codes" not in content
    assert reverse("mfa_list_webauthn") not in content
    assert reverse("mfa_generate_recovery_codes") not in content


def test_settings_hides_recovery_code_links(client):
    user = get_user_model().objects.create_user(
        username="recoverysettingsuser",
        email="recoverysettingsuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.WEBAUTHN,
        data={"name": "Test passkey"},
    )
    RecoveryCodes.activate(user)
    client.force_login(user)

    response = client.get(reverse("settings"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "View recovery codes" not in content
    assert reverse("mfa_view_recovery_codes") not in content


def test_mfa_index_uses_app_styling(client):
    user = get_user_model().objects.create_user(
        username="mfauser",
        email="mfauser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    client.force_login(user)

    response = client.get(reverse("mfa_index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Passkeys" in content
    assert "Add passkey" in content
    assert "Recovery codes" in content
    assert "Menu:" not in content


def test_mfa_index_links_to_recovery_code_generation_when_passkey_exists(client):
    user = get_user_model().objects.create_user(
        username="mfarecoveryuser",
        email="mfarecoveryuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.WEBAUTHN,
        data={"name": "Test passkey"},
    )
    client.force_login(user)

    response = client.get(reverse("mfa_index"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Generate codes" in content
    assert reverse("mfa_generate_recovery_codes") in content


def test_recovery_codes_generate_page_uses_app_styling_and_creates_codes(client):
    user = get_user_model().objects.create_user(
        username="generatecodesuser",
        email="generatecodesuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    Authenticator.objects.create(
        user=user,
        type=Authenticator.Type.WEBAUTHN,
        data={"name": "Test passkey"},
    )
    assert client.login(username="generatecodesuser", password="strong-test-pass-123")
    mark_password_reauthenticated(client, "generatecodesuser")

    response = client.get(reverse("mfa_generate_recovery_codes"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Generate recovery codes" in content
    assert "Recovery codes are the fallback for passkey-protected accounts." in content
    assert "Menu:" not in content

    response = client.post(reverse("mfa_generate_recovery_codes"))

    assert response.status_code == 302
    assert response["Location"] == reverse("mfa_view_recovery_codes")
    assert Authenticator.objects.filter(
        user=user,
        type=Authenticator.Type.RECOVERY_CODES,
    ).exists()


def test_recovery_codes_page_uses_app_styling(client):
    user = get_user_model().objects.create_user(
        username="viewcodesuser",
        email="viewcodesuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    RecoveryCodes.activate(user)
    assert client.login(username="viewcodesuser", password="strong-test-pass-123")
    mark_password_reauthenticated(client, "viewcodesuser")

    response = client.get(reverse("mfa_view_recovery_codes"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Recovery codes" in content
    assert "Download codes" in content
    assert "Generate new codes" in content
    assert reverse("mfa_download_recovery_codes") in content
    assert reverse("mfa_generate_recovery_codes") in content
    assert "Menu:" not in content


def test_recovery_codes_page_can_require_save_confirmation(client, settings):
    settings.MFA_RECOVERY_CODES_SHOW_ONCE = True
    user = get_user_model().objects.create_user(
        username="viewoncecodesuser",
        email="viewoncecodesuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    RecoveryCodes.activate(user)
    assert client.login(username="viewoncecodesuser", password="strong-test-pass-123")
    mark_password_reauthenticated(client, "viewoncecodesuser")

    response = client.get(reverse("mfa_view_recovery_codes"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "I have saved my recovery codes" in content
    assert "allauth.recoveryCodes.forms.viewForm" in content
    assert "Download codes" not in content


def test_webauthn_add_page_loads_styled_form_and_scripts(client):
    user = get_user_model().objects.create_user(
        username="addpasskeyuser",
        email="addpasskeyuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": True},
    )
    assert client.login(username="addpasskeyuser", password="strong-test-pass-123")
    mark_password_reauthenticated(client, "addpasskeyuser")

    response = client.get(reverse("mfa_add_webauthn"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Add passkey" in content
    assert 'id="mfa_webauthn_add"' in content
    assert "allauth.webauthn.forms.addForm" in content
    assert "mfa/js/webauthn.js" in content
    assert "Menu:" not in content


def test_reauthenticate_page_uses_app_styling(client):
    user = get_user_model().objects.create_user(
        username="reauthuser",
        email="reauthuser@example.com",
        password="strong-test-pass-123",
    )
    client.force_login(user)

    response = client.get(reverse("account_reauthenticate"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Confirm access" in content
    assert "Menu:" not in content


def test_account_email_page_uses_app_styling(client):
    user = get_user_model().objects.create_user(
        username="emailpageuser",
        email="emailpageuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": False},
    )
    client.force_login(user)

    response = client.get(reverse("account_email"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Email addresses" in content
    assert "Re-send verification" in content
    assert "Menu:" not in content


def test_social_connections_page_uses_app_styling(client, settings):
    settings.SOCIALACCOUNT_PROVIDERS = {"github": {"APP": {"client_id": "x", "secret": "y"}}}
    user = get_user_model().objects.create_user(
        username="githubconnectionsuser",
        email="githubconnectionsuser@example.com",
        password="strong-test-pass-123",
    )
    SocialAccount.objects.create(
        user=user,
        provider="github",
        uid="12345",
        extra_data={"login": "octocat"},
    )
    client.force_login(user)

    response = client.get(reverse("socialaccount_connections"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "Connected accounts" in content
    assert "octocat" in content
    assert "Remove selected account" in content
    assert "Connect GitHub" in content
    assert "data-social-account-connections" in content
    assert "Menu:" not in content


def test_settings_resend_confirmation_uses_email_code(client, monkeypatch):
    sent_confirmations = []

    def fake_send_confirmation_mail(self, request, emailconfirmation, signup):
        sent_confirmations.append((emailconfirmation, signup))

    monkeypatch.setattr(
        "awesome_repos.adapters.CustomAccountAdapter.send_confirmation_mail",
        fake_send_confirmation_mail,
    )
    user = get_user_model().objects.create_user(
        username="resenduser",
        email="resenduser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": False},
    )
    client.force_login(user)

    response = client.post(reverse("resend_confirmation"))

    assert response.status_code == 302
    assert response["Location"] == reverse("account_email_verification_sent")
    assert len(list(get_messages(response.wsgi_request))) == 1
    assert len(sent_confirmations) == 1
    emailconfirmation, signup = sent_confirmations[0]
    assert signup is False
    assert emailconfirmation.key == client.session["account_email_verification_code"]["code"]
    assert not EmailAddress.objects.get(user=user, email=user.email).verified


def test_settings_resend_confirmation_requires_post(client):
    user = get_user_model().objects.create_user(
        username="resendgetuser",
        email="resendgetuser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": False},
    )
    client.force_login(user)

    response = client.get(reverse("resend_confirmation"))

    assert response.status_code == 405


def test_settings_resend_confirmation_code_confirms_email(client, monkeypatch):
    sent_confirmations = []

    def fake_send_confirmation_mail(self, request, emailconfirmation, signup):
        sent_confirmations.append(emailconfirmation)

    monkeypatch.setattr(
        "awesome_repos.adapters.CustomAccountAdapter.send_confirmation_mail",
        fake_send_confirmation_mail,
    )
    user = get_user_model().objects.create_user(
        username="confirmresenduser",
        email="confirmresenduser@example.com",
        password="strong-test-pass-123",
    )
    EmailAddress.objects.update_or_create(
        user=user,
        email=user.email,
        defaults={"primary": True, "verified": False},
    )
    client.force_login(user)

    response = client.post(reverse("resend_confirmation"))

    assert response.status_code == 302
    assert len(sent_confirmations) == 1
    confirm_response = client.post(
        reverse("account_email_verification_sent"),
        data={"code": sent_confirmations[0].key},
    )

    assert confirm_response.status_code == 302
    assert EmailAddress.objects.get(user=user, email=user.email).verified is True


def test_mailgun_sender_defaults_are_configurable():
    assert settings.DEFAULT_FROM_EMAIL == "LVTD LLC from Awesome <rasul@lvtd.dev>"
    assert settings.SERVER_EMAIL == "Awesome Errors <rasul@lvtd.dev>"
    assert settings.ANYMAIL["MAILGUN_SENDER_DOMAIN"] == "awesome.lvtd.dev"
