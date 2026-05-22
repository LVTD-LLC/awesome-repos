import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse

from apps.core.views import build_absolute_public_url
from apps.repos.models import AwesomeList


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


@pytest.mark.django_db
def test_admin_panel_can_add_awesome_list_and_queue_scan(
    client,
    monkeypatch,
    sync_state_transitions,
):
    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    client.force_login(user)

    queued = []

    def fake_async_task(func_path, awesome_list_id, **kwargs):
        queued.append((func_path, awesome_list_id, kwargs))

    monkeypatch.setattr("apps.core.views.async_task", fake_async_task)
    monkeypatch.setattr("apps.core.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("admin_panel"),
        data={
            "source_url": "https://github.com/wsvincent/awesome-django",
        },
        follow=True,
    )

    assert response.status_code == 200
    awesome_list = AwesomeList.objects.get(source_url="https://github.com/wsvincent/awesome-django")
    assert awesome_list.name == "Awesome Django"
    assert queued == [
        (
            "apps.repos.tasks.sync_awesome_list_task",
            awesome_list.id,
            {"group": "Scan awesome list"},
        )
    ]
    assert "Added Awesome Django and queued a scan." in response.content.decode()


@override_settings(SITE_URL="http://example.com")
def test_build_absolute_public_url_upgrades_non_local_http():
    assert build_absolute_public_url("/api/user") == "https://example.com/api/user"


@override_settings(SITE_URL="http://notlocalhost.example")
def test_build_absolute_public_url_does_not_treat_hostname_substrings_as_local():
    assert build_absolute_public_url("/api/user") == "https://notlocalhost.example/api/user"


@override_settings(SITE_URL="http://localhost:8000")
def test_build_absolute_public_url_preserves_localhost_http():
    assert build_absolute_public_url("/api/user") == "http://localhost:8000/api/user"
