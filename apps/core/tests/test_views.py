import pytest
from django.test import override_settings
from django.urls import reverse

from apps.core.views import build_absolute_public_url


@pytest.mark.django_db
class TestHomeView:
    def test_home_view_status_code(self, auth_client):
        url = reverse("home")
        response = auth_client.get(url)
        assert response.status_code == 200

    def test_home_view_uses_correct_template(self, auth_client):
        url = reverse("home")
        response = auth_client.get(url)
        assert "pages/home.html" in [t.name for t in response.templates]

    

    def test_rotate_api_key_stores_hash_and_shows_key_once(self, auth_client, profile):
        response = auth_client.post(reverse("rotate_api_key"), follow=True)
        content = response.content.decode()
        profile.refresh_from_db()

        assert response.status_code == 200
        assert profile.api_key_prefix
        assert profile.api_key_hash
        assert profile.api_key_hash not in content
        assert "Copy this key now" in content
        assert profile.api_key_prefix in content

        response = auth_client.get(reverse("settings"))
        content = response.content.decode()

        assert "Copy this key now" not in content
        assert profile.api_key_prefix in content


@override_settings(SITE_URL="http://example.com")
def test_build_absolute_public_url_upgrades_non_local_http():
    assert build_absolute_public_url("/api/user") == "https://example.com/api/user"


@override_settings(SITE_URL="http://notlocalhost.example")
def test_build_absolute_public_url_does_not_treat_hostname_substrings_as_local():
    assert build_absolute_public_url("/api/user") == "https://notlocalhost.example/api/user"


@override_settings(SITE_URL="http://localhost:8000")
def test_build_absolute_public_url_preserves_localhost_http():
    assert build_absolute_public_url("/api/user") == "http://localhost:8000/api/user"
