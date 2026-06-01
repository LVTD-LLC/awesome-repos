import re
from datetime import timedelta

import pytest
from allauth.socialaccount.models import SocialAccount, SocialToken
from django.contrib.auth import get_user_model
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

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

    def test_github_starred_import_defaults_off(self, profile):
        assert profile.github_starred_repos_import_enabled is False

    def test_settings_shows_clear_import_cta_when_github_connected_and_import_off(
        self,
        auth_client,
        profile,
    ):
        account = SocialAccount.objects.create(
            user=profile.user,
            provider="github",
            uid="github-user",
        )
        SocialToken.objects.create(account=account, token="user-token")

        response = auth_client.get(reverse("settings"))
        content = response.content.decode()

        assert response.status_code == 200
        assert "Import starred repos" in content
        assert "Manual import" in content
        assert "Starts when you import" in content
        assert "Daily refresh enabled" not in content

    def test_settings_does_not_show_github_account_without_token(
        self,
        auth_client,
        profile,
    ):
        SocialAccount.objects.create(
            user=profile.user,
            provider="github",
            uid="github-user",
            extra_data={"login": "missing-token"},
        )

        response = auth_client.get(reverse("settings"))
        content = response.content.decode()

        assert response.status_code == 200
        assert "Not connected" in content
        assert "@missing-token" not in content
        assert "Import starred repos" not in content

    def test_settings_does_not_show_expired_github_token_as_connected(
        self,
        auth_client,
        profile,
    ):
        account = SocialAccount.objects.create(
            user=profile.user,
            provider="github",
            uid="github-user",
            extra_data={"login": "expired-token"},
        )
        SocialToken.objects.create(
            account=account,
            token="expired-token",
            expires_at=timezone.now() - timedelta(days=1),
        )

        response = auth_client.get(reverse("settings"))
        content = response.content.decode()

        assert response.status_code == 200
        assert "Not connected" in content
        assert "@expired-token" not in content
        assert "Import starred repos" not in content

    def test_import_starred_repositories_enables_profile_and_queues_task(
        self,
        auth_client,
        profile,
        monkeypatch,
    ):
        account = SocialAccount.objects.create(
            user=profile.user,
            provider="github",
            uid="github-user",
        )
        SocialToken.objects.create(account=account, token="user-token")
        queued = []

        def fake_async_task(func_path, profile_id, **kwargs):
            queued.append((func_path, profile_id, kwargs))

        monkeypatch.setattr("apps.core.views.async_task", fake_async_task)
        monkeypatch.setattr("apps.core.views.transaction.on_commit", lambda callback: callback())

        response = auth_client.post(reverse("import_starred_repositories"), follow=True)

        profile.refresh_from_db()
        assert response.status_code == 200
        assert profile.github_starred_repos_import_enabled is True
        assert profile.github_starred_repos_last_error == ""
        assert queued == [
            (
                "apps.repos.tasks.import_starred_repositories_task",
                profile.id,
                {
                    "refresh_existing": True,
                    "group": "Import GitHub starred repositories",
                },
            )
        ]
        assert (
            "Enabled daily GitHub starred repository refresh and queued your first import."
            in response.content.decode()
        )

    def test_import_starred_repositories_shows_refresh_message_when_already_enabled(
        self,
        auth_client,
        profile,
        monkeypatch,
    ):
        account = SocialAccount.objects.create(
            user=profile.user,
            provider="github",
            uid="github-user",
        )
        SocialToken.objects.create(account=account, token="user-token")
        profile.github_starred_repos_import_enabled = True
        profile.save(update_fields=["github_starred_repos_import_enabled", "updated_at"])
        queued = []

        def fake_async_task(func_path, profile_id, **kwargs):
            queued.append((func_path, profile_id, kwargs))

        monkeypatch.setattr("apps.core.views.async_task", fake_async_task)
        monkeypatch.setattr("apps.core.views.transaction.on_commit", lambda callback: callback())

        response = auth_client.post(reverse("import_starred_repositories"), follow=True)

        profile.refresh_from_db()
        assert response.status_code == 200
        assert profile.github_starred_repos_import_enabled is True
        assert queued == [
            (
                "apps.repos.tasks.import_starred_repositories_task",
                profile.id,
                {
                    "refresh_existing": True,
                    "group": "Import GitHub starred repositories",
                },
            )
        ]
        content = response.content.decode()
        assert "Queued your GitHub starred repository refresh." in content
        assert "queued your first import" not in content

    def test_disable_starred_repositories_import_turns_off_daily_refresh(
        self,
        auth_client,
        profile,
    ):
        profile.github_starred_repos_import_enabled = True
        profile.github_starred_repos_last_error = "Previous sync error"
        profile.save(
            update_fields=[
                "github_starred_repos_import_enabled",
                "github_starred_repos_last_error",
                "updated_at",
            ]
        )

        response = auth_client.post(reverse("disable_starred_repository_import"), follow=True)

        profile.refresh_from_db()
        assert response.status_code == 200
        assert profile.github_starred_repos_import_enabled is False
        assert profile.github_starred_repos_last_error == ""
        assert "Disabled daily GitHub starred repository refresh." in response.content.decode()


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
    repos_link = rf'<a href="{re.escape(reverse("repos:search"))}"[^>]*>\s*Repos\s*</a>'
    lists_link = rf'<a href="{re.escape(reverse("repos:list"))}"[^>]*>\s*Lists\s*</a>'
    settings_link = rf'<a href="{re.escape(reverse("settings"))}"[^>]*>\s*Settings\s*</a>'
    assert re.search(repos_link, content)
    assert re.search(lists_link, content)
    assert re.search(settings_link, content)
    assert not re.search(r"<a\b[^>]*>\s*Dashboard\s*</a>", content)
    assert not re.search(r"<a\b[^>]*>\s*Request list\s*</a>", content)


@override_settings(SITE_URL="http://example.com")
def test_build_absolute_public_url_upgrades_non_local_http():
    assert build_absolute_public_url("/api/user") == "https://example.com/api/user"


@override_settings(SITE_URL="http://notlocalhost.example")
def test_build_absolute_public_url_does_not_treat_hostname_substrings_as_local():
    assert build_absolute_public_url("/api/user") == "https://notlocalhost.example/api/user"


@override_settings(SITE_URL="http://localhost:8000")
def test_build_absolute_public_url_preserves_localhost_http():
    assert build_absolute_public_url("/api/user") == "http://localhost:8000/api/user"
