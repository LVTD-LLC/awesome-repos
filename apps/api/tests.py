import json
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from django.http import HttpRequest
from django.test import SimpleTestCase
from django.utils import timezone

from apps.repos.models import AwesomeList, AwesomeListItem, Repository, RepositorySnapshot


class PlaceholderApiTests(SimpleTestCase):
    def test_placeholder(self):
        assert True


class UserInfoApiUnitTests(SimpleTestCase):
    def test_get_user_info_returns_safe_profile_data(self):
        from apps.api.views import get_user_info

        user = SimpleNamespace(
            id=7,
            email="ada@example.com",
            username="ada",
            first_name="Ada",
            last_name="Lovelace",
            date_joined="2026-05-14T00:00:00Z",
            get_full_name=lambda: "Ada Lovelace",
        )
        profile = SimpleNamespace(
            id=11,
            user=user,
            state="signed_up",
        )
        request = HttpRequest()
        request.auth = profile

        response = get_user_info(request)

        assert response["email"] == "ada@example.com"
        assert response["full_name"] == "Ada Lovelace"
        assert response["profile"] == {
            "id": 11,
            "state": "signed_up",
            "has_active_subscription": False,
        }
        assert "key" not in response


def test_openapi_schema_advertises_catalog_endpoints_without_refresh_actions():
    from apps.api.views import api

    paths = api.get_openapi_schema()["paths"]

    assert "/api/repositories" in paths
    assert "/api/repositories/{owner}/{name}" in paths
    assert "/api/awesome-lists" in paths
    assert "/api/awesome-lists/{slug}" in paths
    assert "/api/awesome-lists/{slug}/repositories" in paths
    assert "/api/awesome-lists/{slug}/repository-options" in paths
    assert "get" in paths["/api/awesome-lists"]
    assert "post" not in paths["/api/awesome-lists"]
    assert not any("rescan" in path for path in paths)
    assert not any("discover-missing" in path for path in paths)


def test_api_key_auth_returns_profile_for_valid_key():
    from apps.api.auth import APIKeyHeaderAuth, BearerAPIKeyAuth
    from apps.core.models import Profile

    api_key = "ak_public.secret"

    for auth_class in [APIKeyHeaderAuth, BearerAPIKeyAuth]:
        profile = SimpleNamespace(id=11, check_api_key=Mock(return_value=True))
        with patch("apps.core.api_keys.Profile.objects") as objects:
            objects.select_related.return_value.get.return_value = profile
            response = auth_class().authenticate(HttpRequest(), api_key)

        assert response is profile
        objects.select_related.assert_called_once_with("user")
        objects.select_related.return_value.get.assert_called_once_with(api_key_prefix="ak_public")
        profile.check_api_key.assert_called_once_with(api_key)

    with patch("apps.core.api_keys.Profile.objects") as objects:
        objects.select_related.return_value.get.side_effect = Profile.DoesNotExist
        response = APIKeyHeaderAuth().authenticate(HttpRequest(), "ak_missing.secret")

    assert response is None

    with patch("apps.core.api_keys.Profile.objects") as objects:
        response = APIKeyHeaderAuth().authenticate(HttpRequest(), "bad-key")

    assert response is None
    objects.select_related.assert_not_called()


def _api_key_header(profile):
    return {"HTTP_X_API_KEY": profile.rotate_api_key()}


@pytest.mark.django_db
def test_repository_search_api_uses_existing_filters(client, profile):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        stars=1200,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
    )
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Python web framework",
        language="Python",
        stars=90000,
        commit_count=150,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        github_pushed_at=timezone.now() - timedelta(days=400),
        topics=["django", "web"],
        generated_tags=["web-framework"],
        detected_stacks=["django"],
        package_managers=["poetry"],
        dependency_ecosystems=["python"],
        stack_signals=[
            {
                "slug": "django",
                "label": "Django",
                "category": "web framework",
                "confidence": "high",
                "evidence": [{"path": "pyproject.toml", "dependency": "django"}],
            }
        ],
    )
    RepositorySnapshot.objects.create(
        repository=django_repo,
        captured_at=timezone.now() - timedelta(days=6),
        stars=60000,
        commit_count=100,
    )
    RepositorySnapshot.objects.create(
        repository=django_repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=88000,
        commit_count=140,
    )
    Repository.objects.create(
        full_name="expressjs/express",
        owner="expressjs",
        name="express",
        url="https://github.com/expressjs/express",
        description="Node web framework",
        language="JavaScript",
        stars=65000,
        topics=["node", "web"],
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=django_repo)

    response = client.get(
        "/api/repositories",
        {
            "q": "framework",
            "language": "Python",
            "min_stars": "100",
            "min_age_years": "10",
            "min_velocity_percent": "40",
            "min_star_growth_percent": "40",
            "unmaintained_days": "365",
            "topic": "django",
            "framework": "django",
            "package_manager": "poetry",
            "sort": "stars",
            "sort_direction": "desc",
        },
        **_api_key_header(profile),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["pagination"]["count"] == 1
    assert payload["results"][0]["full_name"] == "django/django"
    assert payload["results"][0]["first_commit_at"] is not None
    assert payload["results"][0]["detected_stacks"] == ["django"]
    assert payload["results"][0]["package_managers"] == ["poetry"]
    assert payload["results"][0]["stack_signals"][0]["label"] == "Django"
    assert payload["results"][0]["awesome_count"] == 1
    assert payload["results"][0]["awesome_lists"][0]["slug"] == "awesome-django"
    assert payload["results"][0]["stars_since_recent"] == 30000
    assert payload["results"][0]["commits_since_recent"] == 50
    assert payload["results"][0]["stars_growth_percent"] == 50
    assert payload["results"][0]["commits_growth_percent"] == 50


@pytest.mark.django_db
def test_repository_detail_api_includes_history(client, profile):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Python web framework",
        language="Python",
        stars=90000,
        forks=32000,
        watchers=500,
        commit_count=100,
        readme="# Django",
        ai_development_signals=[{"tool": "Codex", "path": "AGENTS.md"}],
        uses_ai_for_development=True,
        dependency_files=[{"path": "pyproject.toml", "dependency_count": 1}],
        detected_stacks=["django"],
        package_managers=["poetry"],
        stack_signals=[{"slug": "django", "label": "Django"}],
    )
    RepositorySnapshot.objects.create(
        repository=repository,
        captured_at=timezone.now() - timedelta(days=2),
        stars=89900,
        forks=31900,
        watchers=490,
        commit_count=95,
    )
    RepositorySnapshot.objects.create(
        repository=repository,
        captured_at=timezone.now() - timedelta(days=1),
        stars=90000,
        forks=32000,
        watchers=500,
        commit_count=100,
    )

    response = client.get(
        "/api/repositories/django/django",
        **_api_key_header(profile),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["full_name"] == "django/django"
    assert payload["readme"] == "# Django"
    assert payload["performance"]["has_history"] is True
    assert payload["performance"]["stars_since_first"] == 100
    assert [point["stars"] for point in payload["history"]] == [89900, 90000]
    assert payload["ai_development_signals"] == [{"tool": "Codex", "path": "AGENTS.md"}]
    assert payload["dependency_files"] == [{"path": "pyproject.toml", "dependency_count": 1}]
    assert payload["detected_stacks"] == ["django"]


@pytest.mark.django_db
def test_awesome_list_api_search_detail_and_repository_filters(client, profile):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        description="Curated Django resources",
        topics=["django", "awesome-list"],
        stars=1200,
        readme_repository_count=20,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        last_scanned_at=timezone.now(),
    )
    AwesomeList.objects.create(
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        is_active=False,
    )
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Python web framework",
        language="Python",
        stars=90000,
        forks=32000,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        detected_stacks=["django"],
        package_managers=["poetry"],
    )
    node_repo = Repository.objects.create(
        full_name="expressjs/express",
        owner="expressjs",
        name="express",
        url="https://github.com/expressjs/express",
        description="Node web framework",
        language="JavaScript",
        stars=65000,
        forks=12000,
        first_commit_at=timezone.now() - timedelta(days=365 * 2),
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=django_repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=node_repo)

    search_response = client.get(
        "/api/awesome-lists",
        {"q": "django", "min_age_years": "10", "sort": "oldest"},
        **_api_key_header(profile),
    )

    assert search_response.status_code == 200
    search_payload = search_response.json()
    assert search_payload["pagination"]["count"] == 1
    assert search_payload["totals"]["total_lists"] == 1
    assert search_payload["results"][0]["indexed_repo_count"] == 2
    assert search_payload["results"][0]["first_commit_at"] is not None

    detail_response = client.get(
        "/api/awesome-lists/awesome-django",
        **_api_key_header(profile),
    )

    assert detail_response.status_code == 200
    detail_payload = detail_response.json()
    assert detail_payload["awesome_list"]["slug"] == "awesome-django"
    assert detail_payload["repo_stats"]["total_stars"] == 155000
    assert detail_payload["language_counts"] == [
        {"name": "JavaScript", "count": 1},
        {"name": "Python", "count": 1},
    ]

    repos_response = client.get(
        "/api/awesome-lists/awesome-django/repositories",
        {
            "language": "Python",
            "min_age_years": "10",
            "stack": "django",
            "package_manager": "poetry",
        },
        **_api_key_header(profile),
    )

    assert repos_response.status_code == 200
    repos_payload = repos_response.json()
    assert repos_payload["pagination"]["count"] == 1
    assert repos_payload["results"][0]["full_name"] == "django/django"


@pytest.mark.django_db
def test_superuser_api_can_create_lists_and_queue_refreshes(client, django_user_model, monkeypatch):
    admin = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    admin_api_key = admin.profile.rotate_api_key()
    headers = {"HTTP_X_API_KEY": admin_api_key}
    queued = []

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return "task-1"

    monkeypatch.setattr("apps.api.views.async_task", fake_async_task)
    monkeypatch.setattr("apps.api.views.transaction.on_commit", lambda callback: callback())

    create_response = client.post(
        "/api/awesome-lists",
        data=json.dumps(
            {
                "source_url": "https://github.com/wsvincent/awesome-django",
                "queue_scan": True,
            }
        ),
        content_type="application/json",
        **headers,
    )

    assert create_response.status_code == 201
    awesome_list = AwesomeList.objects.get(slug="awesome-django")
    assert create_response.json()["awesome_list"]["repo_full_name"] == "wsvincent/awesome-django"
    assert queued == [
        (
            "apps.repos.tasks.sync_awesome_list_task",
            (awesome_list.id,),
            {"group": "Scan awesome list"},
        )
    ]

    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )

    list_rescan_response = client.post(
        "/api/awesome-lists/awesome-django/rescan",
        **headers,
    )
    discover_response = client.post(
        "/api/awesome-lists/awesome-django/discover-missing",
        **headers,
    )
    repo_rescan_response = client.post(
        "/api/repositories/django/django/rescan",
        **headers,
    )

    assert list_rescan_response.status_code == 200
    assert list_rescan_response.json()["queued"] is True
    assert discover_response.status_code == 200
    assert repo_rescan_response.status_code == 200
    assert queued[-3:] == [
        (
            "apps.repos.tasks.sync_awesome_list_task",
            (awesome_list.id,),
            {"group": "Scan awesome list"},
        ),
        (
            "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
            (awesome_list.id,),
            {"group": "Manual awesome-list missing repo discovery"},
        ),
        (
            "apps.repos.tasks.refresh_repository_task",
            (repo.id, repo.full_name),
            {"group": "Refresh repositories"},
        ),
    ]


def test_superuser_api_key_auth_eager_loads_user_and_requires_superuser():
    from apps.api.auth import SuperuserAPIKeyHeaderAuth, SuperuserBearerAPIKeyAuth
    from apps.core.models import Profile

    api_key = "ak_public.secret"

    for auth_class in [SuperuserAPIKeyHeaderAuth, SuperuserBearerAPIKeyAuth]:
        superuser_profile = SimpleNamespace(
            id=11,
            user=SimpleNamespace(id=21, is_superuser=True),
            check_api_key=Mock(return_value=True),
        )
        regular_profile = SimpleNamespace(
            id=12,
            user=SimpleNamespace(id=22, is_superuser=False),
            check_api_key=Mock(return_value=True),
        )

        with patch("apps.core.api_keys.Profile.objects") as objects:
            objects.select_related.return_value.get.return_value = superuser_profile
            response = auth_class().authenticate(HttpRequest(), api_key)

        assert response is superuser_profile
        objects.select_related.assert_called_once_with("user")
        objects.select_related.return_value.get.assert_called_once_with(api_key_prefix="ak_public")
        superuser_profile.check_api_key.assert_called_once_with(api_key)

        with patch("apps.core.api_keys.Profile.objects") as objects:
            objects.select_related.return_value.get.return_value = regular_profile
            response = auth_class().authenticate(HttpRequest(), api_key)

        assert response is None
        regular_profile.check_api_key.assert_called_once_with(api_key)

        with patch("apps.core.api_keys.Profile.objects") as objects:
            objects.select_related.return_value.get.side_effect = Profile.DoesNotExist
            response = auth_class().authenticate(HttpRequest(), "ak_missing.secret")

        assert response is None
