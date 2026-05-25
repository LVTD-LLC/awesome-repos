import re

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


@pytest.mark.django_db
def test_admin_panel_can_retry_awesome_list_scan(
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
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        last_error="Previous scan failed.",
    )
    queued = []

    def fake_async_task(func_path, awesome_list_id, **kwargs):
        queued.append((func_path, awesome_list_id, kwargs))

    monkeypatch.setattr("apps.core.views.async_task", fake_async_task)
    monkeypatch.setattr("apps.core.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("admin_panel"),
        data={
            "action": "retry_awesome_list",
            "awesome_list_id": awesome_list.id,
        },
        follow=True,
    )

    assert response.status_code == 200
    assert queued == [
        (
            "apps.repos.tasks.sync_awesome_list_task",
            awesome_list.id,
            {"group": "Scan awesome list"},
        )
    ]
    assert "Queued a retry scan for Awesome Django." in response.content.decode()


@pytest.mark.django_db
def test_admin_panel_shows_github_rate_limit_card(client, monkeypatch, sync_state_transitions):
    user = get_user_model().objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    client.force_login(user)
    monkeypatch.setattr(
        "apps.core.views.github_rate_limit_status",
        lambda: {
            "ok": True,
            "token_configured": True,
            "core": {
                "limit": 5000,
                "used": 125,
                "remaining": 4875,
                "reset_at": None,
            },
            "error": "",
        },
    )

    response = client.get(reverse("admin_panel"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "GitHub API status" in content
    assert "4875" in content
    assert "5000" in content


@pytest.mark.django_db
def test_admin_panel_bounds_recent_awesome_lists_height(
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
    for index in range(3):
        AwesomeList.objects.create(
            name=f"Awesome List {index}",
            slug=f"awesome-list-{index}",
            source_url=f"https://github.com/example/awesome-list-{index}",
            repo_full_name=f"example/awesome-list-{index}",
        )
    monkeypatch.setattr(
        "apps.core.views.github_rate_limit_status",
        lambda: {
            "ok": False,
            "error": "",
        },
    )

    response = client.get(reverse("admin_panel"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Recent awesome lists" in content
    assert "max-h-96 space-y-4 overflow-y-auto pr-2" in content


@pytest.mark.django_db
def test_admin_panel_nav_links_to_repository_and_list_pages(
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
    monkeypatch.setattr(
        "apps.core.views.github_rate_limit_status",
        lambda: {
            "ok": False,
            "error": "",
        },
    )

    response = client.get(reverse("admin_panel"))
    content = response.content.decode()

    assert response.status_code == 200
    assert f'href="{reverse("repos:search")}"' in content
    assert f'href="{reverse("repos:list")}"' in content
    repos_link = rf'<a href="{re.escape(reverse("repos:search"))}"[^>]*>\s*Repos\s*</a>'
    lists_link = rf'<a href="{re.escape(reverse("repos:list"))}"[^>]*>\s*Lists\s*</a>'
    assert re.search(repos_link, content)
    assert re.search(lists_link, content)
    assert not re.search(r">\s*Dashboard\s*<", content)
    assert not re.search(r">\s*Settings\s*<", content)


@override_settings(SITE_URL="http://example.com")
def test_build_absolute_public_url_upgrades_non_local_http():
    assert build_absolute_public_url("/api/user") == "https://example.com/api/user"


@override_settings(SITE_URL="http://notlocalhost.example")
def test_build_absolute_public_url_does_not_treat_hostname_substrings_as_local():
    assert build_absolute_public_url("/api/user") == "https://notlocalhost.example/api/user"


@override_settings(SITE_URL="http://localhost:8000")
def test_build_absolute_public_url_preserves_localhost_http():
    assert build_absolute_public_url("/api/user") == "http://localhost:8000/api/user"
