"""Tests for GitHub social signup wiring.

Covers the three pieces that make the "Sign up with GitHub" button work:
the provider config (settings), the adapter that auto-fills a username, and
the context processor + templates that surface the button in the UI.
"""

import pytest
from allauth.socialaccount.models import SocialLogin
from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import RequestFactory, override_settings
from django.urls import reverse
from django.utils.module_loading import import_string

from apps.core.context_processors import available_social_providers
from awesome_repos.settings import build_github_provider_config

User = get_user_model()

# Mirror how settings.py registers the provider when GITHUB_CLIENT_ID is set,
# using the real production config builder so these fixtures track settings.py.
GITHUB_PROVIDER_CONFIG = {"github": build_github_provider_config("test-id", "test-secret")}


def _social_account_adapter():
    return import_string(settings.SOCIALACCOUNT_ADAPTER)()


def _account_adapter():
    return import_string(settings.ACCOUNT_ADAPTER)()


def _populate_user(email):
    """Run the social adapter the way allauth does mid-signup.

    allauth hands the adapter a fresh (unsaved) user plus a ``data`` dict of the
    fields extracted from the provider, and reads the email from ``data``.
    """
    adapter = _social_account_adapter()
    sociallogin = SocialLogin(user=User())
    return adapter.populate_user(RequestFactory().get("/"), sociallogin, {"email": email})


class TestGithubProviderConfig:
    """Assert against the real settings.py config builder so a regression in
    the scope or email flags (e.g. dropping user:email) fails the suite."""

    def test_github_provider_requests_user_email_scope(self):
        """user:email scope is required to read private GitHub emails at signup."""
        github = build_github_provider_config("id", "secret")

        assert "user:email" in github["SCOPE"]

    def test_github_provider_enables_verified_email_signup(self):
        github = build_github_provider_config("id", "secret")

        assert github["VERIFIED_EMAIL"] is True
        assert github["EMAIL_AUTHENTICATION"] is True

    def test_github_provider_passes_through_app_credentials(self):
        github = build_github_provider_config("my-id", "my-secret")

        assert github["APP"] == {"client_id": "my-id", "secret": "my-secret"}


class TestSocialSignupRedirect:
    def test_signup_redirects_to_settings_for_starred_repo_import_cta(self):
        request = RequestFactory().get("/accounts/github/login/callback/")

        assert _account_adapter().get_signup_redirect_url(request) == reverse("settings")


class TestSocialConnectRedirect:
    def test_connect_redirects_to_settings_after_successful_connection(self):
        request = RequestFactory().get("/accounts/github/login/callback/")

        assert _social_account_adapter().get_connect_redirect_url(request, None) == reverse(
            "settings"
        )


@pytest.mark.django_db
class TestSocialUsernamePopulation:
    def test_username_derived_from_email_local_part(self):
        user = _populate_user("octocat@example.com")

        assert user.username == "octocat"

    def test_username_strips_non_word_characters(self):
        user = _populate_user("octo.cat+spam@example.com")

        assert user.username == "octocatspam"

    def test_username_is_made_unique_on_collision(self):
        User.objects.create_user(username="octocat", email="taken@example.com")

        user = _populate_user("octocat@example.com")

        assert user.username == "octocat1"

    def test_username_falls_back_when_email_local_part_empty(self):
        user = _populate_user("!!!@example.com")

        assert user.username.startswith("user")
        assert len(user.username) > len("user")


@pytest.mark.django_db
class TestAvailableSocialProvidersContext:
    @override_settings(SOCIALACCOUNT_PROVIDERS=GITHUB_PROVIDER_CONFIG)
    def test_github_is_advertised_when_configured(self):
        context = available_social_providers(RequestFactory().get("/"))

        assert "github" in context["available_social_providers"]
        assert context["has_social_providers"] is True

    @override_settings(SOCIALACCOUNT_PROVIDERS={})
    def test_no_providers_when_unconfigured(self):
        context = available_social_providers(RequestFactory().get("/"))

        assert context["available_social_providers"] == []
        assert context["has_social_providers"] is False


@pytest.mark.django_db
class TestSocialButtonsRender:
    """Render the real login/signup pages through the views so the form,
    context processors, and allauth provider tags all participate."""

    @override_settings(SOCIALACCOUNT_PROVIDERS=GITHUB_PROVIDER_CONFIG)
    def test_signup_page_shows_github_button(self, client):
        content = client.get(reverse("account_signup")).content.decode()

        assert "GitHub" in content
        assert "/accounts/github/login/" in content

    @override_settings(SOCIALACCOUNT_PROVIDERS=GITHUB_PROVIDER_CONFIG)
    def test_login_page_shows_github_button(self, client):
        content = client.get(reverse("account_login")).content.decode()

        assert "GitHub" in content
        assert "/accounts/github/login/" in content

    @override_settings(SOCIALACCOUNT_PROVIDERS={})
    def test_signup_page_hides_button_without_provider(self, client):
        content = client.get(reverse("account_signup")).content.decode()

        assert "/accounts/github/login/" not in content
        assert "isn't configured yet" in content
