import base64
import json
import re
from datetime import UTC, datetime, timedelta
from io import StringIO
from types import SimpleNamespace

import pytest
from allauth.socialaccount.models import SocialAccount, SocialToken
from defusedxml.common import EntitiesForbidden
from django.contrib import admin as django_admin
from django.core.cache import cache
from django.core.management import call_command
from django.db import IntegrityError, connection
from django.http import QueryDict
from django.template import Context, Template
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.repos.admin import AwesomeListRequestAdmin
from apps.repos.embeddings import (
    build_repository_embedding_payload,
    build_repository_embedding_text,
    save_repository_embedding,
)
from apps.repos.forms import AwesomeListCreateForm, AwesomeListRequestForm
from apps.repos.models import (
    REPOSITORY_EMBEDDING_DIMENSIONS,
    AwesomeList,
    AwesomeListItem,
    AwesomeListRequest,
    AwesomeListSnapshot,
    Repository,
    RepositoryEmbedding,
    RepositoryLike,
    RepositorySnapshot,
    UserStarredRepository,
)
from apps.repos.services import (
    GitHubAPIError,
    active_awesome_list_source_repository_name_set,
    add_repository_to_awesome_list,
    annotate_repository_recent_growth_metrics,
    attach_awesome_list_commit_count,
    awesome_list_history_chart_data,
    awesome_list_repository_history_chart_data,
    awesome_list_repository_queryset,
    detect_ai_development_signals,
    detect_awesome_list_candidate,
    discover_missing_awesome_list_repositories,
    extract_github_repos,
    extract_homepage_url_from_description,
    fetch_github_commit_count,
    fetch_github_commit_count_and_first_commit_at,
    fetch_json,
    fetch_repository_readme,
    fetch_repository_readme_data,
    fetch_repository_tree_items,
    fetch_user_starred_repositories,
    github_rate_limit_status,
    github_repository_sync_token_for_index,
    github_repository_sync_token_pool,
    import_starred_repositories_for_profile,
    is_same_repository_url_or_subpath,
    minimum_age_cutoff,
    normalize_homepage_url,
    parse_github_repo_url,
    refresh_repositories,
    repository_history_chart_data,
    repository_homepage_url,
    repository_performance_summary,
    repository_search_queryset,
    similar_repositories_for_repository,
    sync_awesome_list,
    sync_repository_stack_detection,
    update_awesome_list_metadata,
    upsert_repository_from_github,
)
from apps.repos.stack_detection import (
    dependency_file_candidates,
    detect_repository_stack,
    parse_pom_xml,
    parse_python_setup,
)
from apps.repos.tags import (
    build_repository_tagging_payload,
    generate_repository_tags,
    normalize_repository_tags,
    repository_tagging_model_id,
    save_repository_tags,
    sync_repository_tags,
)
from apps.repos.tasks import (
    daily_repository_refresh_limit,
    enqueue_starred_repository_imports_task,
    refresh_repositories_task,
    refresh_repository_task,
    tag_repositories_task,
)
from apps.repos.views import (
    awesome_list_directory_totals,
    public_repository_filter_options,
    repository_filter_remove_querystring,
    repository_json_value_counts,
)

LOC_MEM_CACHES = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


def assert_option_label_with_count(content: str | bytes, label: str, count: int) -> None:
    text = content.decode() if isinstance(content, bytes) else content
    assert re.search(rf"{re.escape(label)}\s*\({count}\)", text)


def assert_repository_detail_link(content: str, full_name: str) -> None:
    path = f"/repos/{full_name}/"
    assert re.search(
        rf'<a\b(?=[^>]*\bhref="{re.escape(path)}")'
        r'(?=[^>]*\bclass="[^"]*\btext-lg\b[^"]*\bfont-bold\b)[^>]*>',
        content,
    )


@pytest.fixture(autouse=True)
def disable_repository_tagging(settings, monkeypatch):
    settings.REPOSITORY_TAGGING_ENABLED = False
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count", lambda *args, **kwargs: 123
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        lambda *args, **kwargs: (123, datetime(2005, 7, 13, tzinfo=UTC)),
    )


@pytest.mark.parametrize(
    ("url", "full_name"),
    [
        (
            "https://github.com/awesome-selfhosted/awesome-selfhosted",
            "awesome-selfhosted/awesome-selfhosted",
        ),
        ("https://github.com/wsvincent/awesome-django.git", "wsvincent/awesome-django"),
    ],
)
def test_parse_github_repo_url(url, full_name):
    assert parse_github_repo_url(url) == full_name


def test_extract_github_repos_dedupes_and_skips_non_repo_paths():
    markdown = """
    - [Django](https://github.com/django/django)
    - [Django stars](https://github.com/django/django/stargazers)
    - [Paperless](https://github.com/paperless-ngx/paperless-ngx#readme)
    - duplicate https://github.com/django/django
    """
    assert extract_github_repos(markdown) == ["django/django", "paperless-ngx/paperless-ngx"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://example.com/docs", "https://example.com/docs"),
        ("www.example.com/docs", "https://www.example.com/docs"),
        ("example.com", "https://example.com"),
        ("javascript:alert(1)", ""),
        ("not a url", ""),
        (f"https://example.com/{'a' * 220}", ""),
    ],
)
def test_normalize_homepage_url_allows_safe_http_links(url, expected):
    assert normalize_homepage_url(url) == expected


def test_extract_homepage_url_from_description_uses_first_safe_link():
    description = "Framework docs live at https://docs.example.com/."

    assert extract_homepage_url_from_description(description) == "https://docs.example.com/"


def test_repository_homepage_url_prefers_github_homepage_over_description_link():
    payload = github_repo_payload()
    payload["homepage"] = "https://www.djangoproject.com/"
    payload["description"] = "Docs at https://docs.djangoproject.com/."

    assert repository_homepage_url(payload) == "https://www.djangoproject.com/"


def test_repository_homepage_url_falls_back_to_description_link():
    payload = github_repo_payload()
    payload["homepage"] = ""
    payload["description"] = "Docs at https://docs.djangoproject.com/."

    assert repository_homepage_url(payload) == "https://docs.djangoproject.com/"


def test_repository_homepage_url_ignores_description_link_to_same_github_repo():
    payload = github_repo_payload()
    payload["homepage"] = ""
    payload["description"] = "Mirror of https://github.com/django/django."

    assert repository_homepage_url(payload) == ""


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/django/django",
        "https://github.com/django/django?tab=readme-ov-file",
        "https://github.com/django/django#readme",
        "https://github.com/django/django/releases",
        "https://github.com/django/django/issues",
        "https://github.com/django/django/blob/main/README.md",
        "https://github.com/django/django/wiki",
    ],
)
def test_is_same_repository_url_or_subpath_matches_repo_pages(url):
    assert is_same_repository_url_or_subpath(url, "https://github.com/django/django")


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/django/django-cms",
        "https://github.com/django",
        "https://docs.djangoproject.com/",
    ],
)
def test_is_same_repository_url_or_subpath_ignores_other_sites_and_repos(url):
    assert not is_same_repository_url_or_subpath(url, "https://github.com/django/django")


@pytest.mark.parametrize(
    "repo_page_url",
    [
        "https://github.com/django/django/releases",
        "https://github.com/django/django/issues",
        "https://github.com/django/django/blob/main/README.md",
    ],
)
def test_repository_homepage_url_ignores_description_link_to_github_repo_subpath(
    repo_page_url,
):
    payload = github_repo_payload()
    payload["homepage"] = ""
    payload["description"] = f"Release notes live at {repo_page_url}."

    assert repository_homepage_url(payload) == ""


@pytest.mark.django_db
def test_detect_awesome_list_candidate_uses_readme_links():
    readme = (
        "# Awesome Django\n\n"
        "- [Django](https://github.com/django/django)\n"
        "- [Channels](https://github.com/django/channels)\n"
        "- [Wagtail](https://github.com/wagtail/wagtail)\n"
    )
    result = detect_awesome_list_candidate(
        github_repo_payload(full_name="wsvincent/awesome-django"),
        readme,
    )

    assert result["is_candidate"] is True
    assert result["detected_repo_count"] == 3
    assert "awesome_readme_title" in result["reasons"]
    assert "awesome_repo_name" in result["reasons"]


@pytest.mark.django_db
def test_detect_awesome_list_candidate_marks_tracked_source_repo():
    AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )

    result = detect_awesome_list_candidate(
        github_repo_payload(full_name="wsvincent/awesome-django"),
        "",
        active_source_full_names=active_awesome_list_source_repository_name_set(),
    )

    assert result["is_candidate"] is True
    assert result["detected_repo_count"] == 0
    assert result["reasons"] == ["tracked_awesome_list_source"]


@pytest.mark.django_db
def test_detect_awesome_list_candidate_uses_preloaded_sources_without_queries(
    django_assert_num_queries,
):
    AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )
    active_source_full_names = active_awesome_list_source_repository_name_set()

    with django_assert_num_queries(0):
        result = detect_awesome_list_candidate(
            github_repo_payload(full_name="wsvincent/awesome-django"),
            "",
            active_source_full_names=active_source_full_names,
        )

    assert result["is_candidate"] is True
    assert result["reasons"] == ["tracked_awesome_list_source"]


@pytest.mark.django_db
def test_awesome_list_form_derives_name_and_unique_slug_from_url():
    AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/old/awesome-django",
    )

    form = AwesomeListCreateForm(data={"source_url": "https://github.com/wsvincent/awesome-django"})

    assert form.is_valid()
    awesome_list = form.save()

    assert awesome_list.name == "Awesome Django"
    assert awesome_list.slug == "awesome-django-2"


@pytest.mark.django_db
def test_awesome_list_request_form_records_normalized_repo_details():
    form = AwesomeListRequestForm(
        data={
            "source_url": "https://github.com/wsvincent/awesome-django",
            "requester_email": "PERSON@example.com",
            "note": "Useful Django resources.",
        }
    )

    assert form.is_valid(), form.errors
    list_request = form.save()

    assert list_request.source_url == "https://github.com/wsvincent/awesome-django"
    assert list_request.repo_full_name == "wsvincent/awesome-django"
    assert list_request.requester_email == "person@example.com"
    assert list_request.note == "Useful Django resources."
    assert list_request.status == AwesomeListRequest.Status.PENDING


@pytest.mark.django_db
def test_awesome_list_request_form_rejects_tracked_lists_by_repo_name():
    AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/example/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )

    form = AwesomeListRequestForm(
        data={"source_url": "https://github.com/wsvincent/awesome-django"}
    )

    assert not form.is_valid()
    assert "already tracked" in form.errors["source_url"][0]


@pytest.mark.django_db
def test_awesome_list_request_form_rejects_duplicate_requests_by_repo_name():
    AwesomeListRequest.objects.create(
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )

    form = AwesomeListRequestForm(
        data={"source_url": "https://github.com/wsvincent/awesome-django.git"}
    )

    assert not form.is_valid()
    assert "already been submitted" in form.errors["source_url"][0]


@pytest.mark.django_db
def test_awesome_list_request_admin_clears_reviewed_at_when_reset_to_pending():
    list_request = AwesomeListRequest.objects.create(
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        status=AwesomeListRequest.Status.ADDED,
    )
    model_admin = AwesomeListRequestAdmin(AwesomeListRequest, django_admin.site)

    model_admin.save_model(None, list_request, None, change=True)
    list_request.refresh_from_db()

    assert list_request.reviewed_at is not None

    list_request.status = AwesomeListRequest.Status.PENDING
    model_admin.save_model(None, list_request, None, change=True)
    list_request.refresh_from_db()

    assert list_request.reviewed_at is None


@pytest.mark.django_db
def test_sync_awesome_list_marks_empty_scan_as_error(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Empty List",
        slug="empty-list",
        source_url="https://github.com/example/empty-list",
        repo_full_name="example/empty-list",
    )

    monkeypatch.setattr(
        "apps.repos.services.fetch_awesome_readme",
        lambda full_name, **kwargs: ("# Empty\n", {"full_name": full_name, "description": ""}),
    )

    result = sync_awesome_list(awesome_list)
    awesome_list.refresh_from_db()

    assert result["discovered"] == 0
    assert result["synced"] == 0
    assert awesome_list.last_error == "No GitHub repository links found in README."
    assert awesome_list.snapshots.count() == 0


def github_awesome_list_payload(
    full_name="wsvincent/awesome-django",
    stars=1200,
    forks=100,
    watchers=25,
    commits_count=350,
):
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": {"login": owner},
        "name": name,
        "html_url": f"https://github.com/{full_name}",
        "description": "Curated Django resources.",
        "topics": ["django", "awesome-list"],
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": 7,
        "subscribers_count": watchers,
        "watchers_count": watchers,
        "default_branch": "main",
        "archived": False,
        "disabled": False,
        "created_at": "2015-01-01T00:00:00Z",
        "updated_at": "2026-05-20T00:00:00Z",
        "pushed_at": "2026-05-21T00:00:00Z",
        "commits_count": commits_count,
    }


@pytest.mark.django_db
def test_sync_awesome_list_stores_list_activity_metadata(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )
    markdown = """
    - [Django](https://github.com/django/django)
    - [Channels](https://github.com/django/channels)
    """
    monkeypatch.setattr(
        "apps.repos.services.fetch_awesome_readme",
        lambda full_name, **kwargs: (markdown, github_awesome_list_payload(full_name)),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        lambda *args, **kwargs: (350, datetime(2015, 1, 2, tzinfo=UTC)),
    )

    def fake_upsert(full_name, *, active_source_full_names=None, github_access_token=None):
        owner, name = full_name.split("/", 1)
        return Repository.objects.create(
            full_name=full_name,
            owner=owner,
            name=name,
            url=f"https://github.com/{full_name}",
            stars=10,
        )

    monkeypatch.setattr("apps.repos.services.upsert_repository_from_github", fake_upsert)

    result = sync_awesome_list(awesome_list, limit=1)
    awesome_list.refresh_from_db()

    assert result["discovered"] == 1
    assert result["synced"] == 1
    assert awesome_list.description == "Curated Django resources."
    assert awesome_list.topics == ["django", "awesome-list"]
    assert awesome_list.stars == 1200
    assert awesome_list.forks == 100
    assert awesome_list.watchers == 25
    assert awesome_list.open_issues == 7
    assert awesome_list.commits_count == 350
    assert awesome_list.first_commit_at == datetime(2015, 1, 2, tzinfo=UTC)
    assert awesome_list.readme_repository_count == 2
    assert awesome_list.default_branch == "main"
    assert awesome_list.github_pushed_at is not None
    assert awesome_list.last_error == ""
    assert "commits_count" not in awesome_list.raw
    assert "first_commit_at" not in awesome_list.raw
    assert awesome_list.items.count() == 1
    snapshot = awesome_list.snapshots.get()
    assert snapshot.repo_full_name == "wsvincent/awesome-django"
    assert snapshot.stars == 1200
    assert snapshot.forks == 100
    assert snapshot.watchers == 25
    assert snapshot.open_issues == 7
    assert snapshot.commits_count == 350
    assert snapshot.readme_repository_count == 2
    assert snapshot.default_branch == "main"
    assert snapshot.first_commit_at == datetime(2015, 1, 2, tzinfo=UTC)


@pytest.mark.django_db
def test_sync_awesome_list_uses_sync_tokens_for_list_and_repositories(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )
    captured = {"readme": [], "commit_count": [], "upserts": []}
    markdown = "- [Django](https://github.com/django/django)"

    monkeypatch.setattr(
        "apps.repos.services.github_repository_sync_token_pool",
        lambda: ["primary-token", "user-token"],
    )

    def fake_fetch_awesome_readme(full_name, *, token=None):
        captured["readme"].append((full_name, token))
        return markdown, github_awesome_list_payload(full_name)

    def fake_attach_commit_count(
        full_name,
        meta,
        *,
        existing_first_commit_at=None,
        token=None,
    ):
        captured["commit_count"].append((full_name, token))

    def fake_upsert(full_name, *, active_source_full_names=None, github_access_token=None):
        captured["upserts"].append((full_name, github_access_token))
        owner, name = full_name.split("/", 1)
        return Repository.objects.create(
            full_name=full_name,
            owner=owner,
            name=name,
            url=f"https://github.com/{full_name}",
        )

    monkeypatch.setattr("apps.repos.services.fetch_awesome_readme", fake_fetch_awesome_readme)
    monkeypatch.setattr(
        "apps.repos.services.attach_awesome_list_commit_count",
        fake_attach_commit_count,
    )
    monkeypatch.setattr("apps.repos.services.upsert_repository_from_github", fake_upsert)

    result = sync_awesome_list(awesome_list)

    assert result["synced"] == 1
    assert captured == {
        "readme": [("wsvincent/awesome-django", "primary-token")],
        "commit_count": [("wsvincent/awesome-django", "primary-token")],
        "upserts": [("django/django", "user-token")],
    }


@pytest.mark.django_db
def test_update_awesome_list_metadata_preserves_missing_commit_count():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        commits_count=350,
        first_commit_at=datetime(2015, 1, 2, tzinfo=UTC),
    )
    awesome_list.commits_count = None
    awesome_list.first_commit_at = None

    update_awesome_list_metadata(
        awesome_list,
        {
            "full_name": "wsvincent/awesome-django",
            "description": "Curated Django resources.",
            "stargazers_count": 1200,
            "forks_count": 100,
            "open_issues_count": 7,
            "default_branch": "main",
        },
        repo_full_name="wsvincent/awesome-django",
        readme_repository_count=42,
        scanned_at=timezone.now(),
    )

    awesome_list.refresh_from_db()
    assert awesome_list.commits_count == 350
    assert awesome_list.first_commit_at == datetime(2015, 1, 2, tzinfo=UTC)
    assert awesome_list.readme_repository_count == 42
    snapshot = awesome_list.snapshots.get()
    assert snapshot.commits_count == 350
    assert snapshot.first_commit_at == datetime(2015, 1, 2, tzinfo=UTC)


@pytest.mark.django_db
def test_discover_missing_awesome_list_repositories_skips_existing_repos(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python",
        source_url="https://github.com/vinta/awesome-python",
        repo_full_name="vinta/awesome-python",
    )
    existing_unlinked = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=100,
    )
    existing_linked = Repository.objects.create(
        full_name="pallets/flask",
        owner="pallets",
        name="flask",
        url="https://github.com/pallets/flask",
        stars=50,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=existing_linked)

    markdown = """
    - [Django](https://github.com/django/django)
    - [Flask](https://github.com/pallets/flask)
    - [HTTPX](https://github.com/encode/httpx)
    """
    monkeypatch.setattr(
        "apps.repos.services.fetch_awesome_readme",
        lambda full_name, **kwargs: (
            markdown,
            {"full_name": full_name, "description": "Python resources"},
        ),
    )

    result = discover_missing_awesome_list_repositories(awesome_list)

    assert result["discovered"] == 3
    assert result["missing"] == ["encode/httpx"]
    assert result["linked_existing"] == 1
    assert result["skipped_existing"] == 1
    assert AwesomeListItem.objects.filter(
        awesome_list=awesome_list, repository=existing_unlinked
    ).exists()


@pytest.mark.django_db
def test_add_repository_to_awesome_list_skips_existing_repo_refresh(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=100,
    )

    def fail_upsert(full_name):
        raise AssertionError(f"should not refresh existing repository {full_name}")

    monkeypatch.setattr("apps.repos.services.upsert_repository_from_github", fail_upsert)

    result = add_repository_to_awesome_list(awesome_list, "django/django")

    assert result["repository_created"] is False
    assert result["link_created"] is True
    repo.refresh_from_db()
    assert repo.stars == 100


def github_repo_payload(full_name="django/django", stars=80000, forks=32000, watchers=1200):
    owner, name = full_name.split("/", 1)
    return {
        "full_name": full_name,
        "owner": {"login": owner},
        "name": name,
        "html_url": f"https://github.com/{full_name}",
        "description": "The Web framework for perfectionists with deadlines.",
        "homepage": "https://www.djangoproject.com/",
        "language": "Python",
        "license": {"spdx_id": "BSD-3-Clause", "name": "BSD 3-Clause License"},
        "topics": ["django", "python", "web"],
        "stargazers_count": stars,
        "forks_count": forks,
        "open_issues_count": 128,
        "subscribers_count": watchers,
        "watchers_count": stars,
        "default_branch": "main",
        "archived": False,
        "disabled": False,
        "fork": False,
        "created_at": "2005-07-13T00:00:00Z",
        "updated_at": "2026-05-20T00:00:00Z",
        "pushed_at": "2026-05-21T00:00:00Z",
    }


def attach_github_token(profile, token="user-token", uid="12345"):
    account = SocialAccount.objects.create(
        user=profile.user,
        provider="github",
        uid=uid,
    )
    return SocialToken.objects.create(account=account, token=token)


def test_fetch_user_starred_repositories_requests_star_metadata(monkeypatch):
    captured_requests = []

    def fake_fetch_json_page(url, *, token=None, accept=None):
        captured_requests.append({"url": url, "token": token, "accept": accept})
        return (
            [
                {
                    "starred_at": "2026-05-01T12:00:00Z",
                    "repo": github_repo_payload("django/django"),
                }
            ],
            "",
        )

    monkeypatch.setattr("apps.repos.services.fetch_json_page", fake_fetch_json_page)

    starred_repositories = fetch_user_starred_repositories("user-star-token")

    assert captured_requests == [
        {
            "url": "https://api.github.com/user/starred?per_page=100&page=1",
            "token": "user-star-token",
            "accept": "application/vnd.github.star+json",
        }
    ]
    assert starred_repositories == [
        {
            "repository": github_repo_payload("django/django"),
            "starred_at": datetime(2026, 5, 1, 12, tzinfo=UTC),
        }
    ]


@pytest.mark.django_db
def test_import_starred_repositories_links_existing_and_syncs_new_with_user_token(
    monkeypatch,
    profile,
):
    attach_github_token(profile, token="user-star-token")
    existing = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=100,
    )
    starred_at = datetime(2026, 5, 1, tzinfo=UTC)
    monkeypatch.setattr(
        "apps.repos.services.fetch_user_starred_repositories",
        lambda token, limit=None: [
            {"repository": github_repo_payload("django/django"), "starred_at": starred_at},
            {"repository": github_repo_payload("encode/httpx"), "starred_at": None},
        ],
    )
    captured_syncs = []

    def fake_upsert(full_name, *, active_source_full_names=None, github_access_token=None):
        captured_syncs.append((full_name, github_access_token))
        owner, name = full_name.split("/", 1)
        return Repository.objects.create(
            full_name=full_name,
            owner=owner,
            name=name,
            url=f"https://github.com/{full_name}",
            stars=50,
        )

    monkeypatch.setattr("apps.repos.services.upsert_repository_from_github", fake_upsert)

    result = import_starred_repositories_for_profile(profile, refresh_existing=False)

    profile.refresh_from_db()
    assert result["discovered"] == 2
    assert result["linked"] == 2
    assert result["created_links"] == 2
    assert result["repositories_created"] == 1
    assert captured_syncs == [("encode/httpx", "user-star-token")]
    assert profile.github_starred_repos_import_enabled is True
    assert profile.github_starred_repos_last_imported_at is not None
    assert profile.github_starred_repos_last_error == ""
    assert UserStarredRepository.objects.filter(profile=profile, repository=existing).exists()
    assert (
        UserStarredRepository.objects.get(
            profile=profile,
            repository__full_name="django/django",
        ).starred_at
        == starred_at
    )


@pytest.mark.django_db
def test_import_starred_repositories_refreshes_existing_repos_with_user_token(monkeypatch, profile):
    attach_github_token(profile, token="user-refresh-token")
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=100,
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_user_starred_repositories",
        lambda token, limit=None: [
            {"repository": github_repo_payload("django/django"), "starred_at": None},
        ],
    )
    captured_syncs = []

    def fake_upsert(full_name, *, active_source_full_names=None, github_access_token=None):
        captured_syncs.append((full_name, github_access_token))
        return repository

    monkeypatch.setattr("apps.repos.services.upsert_repository_from_github", fake_upsert)

    result = import_starred_repositories_for_profile(profile, refresh_existing=True)

    assert result["repositories_refreshed"] == 1
    assert result["repositories_created"] == 0
    assert captured_syncs == [("django/django", "user-refresh-token")]
    starred_link = UserStarredRepository.objects.get(profile=profile, repository=repository)
    assert starred_link.last_synced_at is not None


@pytest.mark.django_db
def test_import_starred_repositories_records_rate_limit_as_partial_import(monkeypatch, profile):
    attach_github_token(profile, token="user-rate-token")
    monkeypatch.setattr(
        "apps.repos.services.fetch_user_starred_repositories",
        lambda token, limit=None: [
            {"repository": github_repo_payload("django/django"), "starred_at": None},
            {"repository": github_repo_payload("encode/httpx"), "starred_at": None},
        ],
    )

    def fail_rate_limit(full_name, *, active_source_full_names=None, github_access_token=None):
        raise GitHubAPIError("rate limit exceeded", status_code=403, rate_limit_remaining="0")

    monkeypatch.setattr("apps.repos.services.upsert_repository_from_github", fail_rate_limit)

    result = import_starred_repositories_for_profile(profile, refresh_existing=True)

    profile.refresh_from_db()
    assert result["stopped_for_rate_limit"] is True
    assert result["failure_count"] == 1
    assert result["linked"] == 0
    assert profile.github_starred_repos_last_error.startswith(
        "Import stopped early because GitHub rate limit was reached"
    )


@pytest.mark.django_db
def test_enqueue_starred_repository_imports_task_queues_only_opted_in_profiles(
    monkeypatch,
    profile,
    django_user_model,
):
    profile.github_starred_repos_import_enabled = True
    profile.save(update_fields=["github_starred_repos_import_enabled", "updated_at"])
    attach_github_token(profile, uid="opted-in")
    opted_out_user = django_user_model.objects.create_user(
        username="opted-out",
        email="opted-out@example.com",
        password="password123",
    )
    attach_github_token(opted_out_user.profile, uid="opted-out")
    no_token_user = django_user_model.objects.create_user(
        username="no-token",
        email="no-token@example.com",
        password="password123",
    )
    no_token_user.profile.github_starred_repos_import_enabled = True
    no_token_user.profile.save(update_fields=["github_starred_repos_import_enabled", "updated_at"])
    queued = []

    def fake_async_task(func_path, profile_id, **kwargs):
        queued.append((func_path, profile_id, kwargs))
        return f"task-{profile_id}"

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    result = enqueue_starred_repository_imports_task(limit_per_user=5)

    assert result["queued"] == 1
    assert queued == [
        (
            "apps.repos.tasks.import_starred_repositories_task",
            profile.id,
            {
                "limit": 5,
                "refresh_existing": True,
                "group": "Import GitHub starred repositories",
            },
        )
    ]


@pytest.mark.django_db
def test_starred_repository_search_is_scoped_to_current_user(
    auth_client,
    profile,
    django_user_model,
):
    own_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=100,
    )
    other_repo = Repository.objects.create(
        full_name="encode/httpx",
        owner="encode",
        name="httpx",
        url="https://github.com/encode/httpx",
        stars=50,
    )
    other_user = django_user_model.objects.create_user(
        username="other",
        email="other@example.com",
        password="password123",
    )
    UserStarredRepository.objects.create(profile=profile, repository=own_repo)
    UserStarredRepository.objects.create(profile=other_user.profile, repository=other_repo)

    response = auth_client.get(reverse("repos:starred"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "Search your starred repositories." in content
    assert "django/django" in content
    assert "encode/httpx" not in content


@pytest.mark.django_db
def test_starred_repository_search_skips_full_snapshot_metrics(
    auth_client,
    profile,
    monkeypatch,
):
    from apps.repos import views as repo_views

    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=75,
    )
    UserStarredRepository.objects.create(profile=profile, repository=repo)
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=50,
    )
    original_repository_search_queryset = repo_views.repository_search_queryset
    calls = []

    def capture_repository_search_queryset(params, *args, **kwargs):
        calls.append(kwargs.get("include_snapshot_metrics"))
        return original_repository_search_queryset(params, *args, **kwargs)

    monkeypatch.setattr(
        repo_views,
        "repository_search_queryset",
        capture_repository_search_queryset,
    )

    response = auth_client.get(reverse("repos:starred"))

    assert response.status_code == 200
    assert calls == [False]
    assert b"1 history point" in response.content


@pytest.mark.django_db
def test_starred_repository_search_sorts_by_starred_at(auth_client, profile):
    newest = Repository.objects.create(
        full_name="owner/newest",
        owner="owner",
        name="newest",
        url="https://github.com/owner/newest",
        stars=10,
    )
    oldest = Repository.objects.create(
        full_name="owner/oldest",
        owner="owner",
        name="oldest",
        url="https://github.com/owner/oldest",
        stars=1000,
    )
    unknown = Repository.objects.create(
        full_name="owner/unknown",
        owner="owner",
        name="unknown",
        url="https://github.com/owner/unknown",
        stars=5000,
    )
    UserStarredRepository.objects.create(
        profile=profile,
        repository=oldest,
        starred_at=datetime(2026, 4, 1, 12, tzinfo=UTC),
    )
    UserStarredRepository.objects.create(
        profile=profile,
        repository=newest,
        starred_at=datetime(2026, 5, 2, 12, tzinfo=UTC),
    )
    UserStarredRepository.objects.create(profile=profile, repository=unknown)

    response = auth_client.get(reverse("repos:starred"), {"sort": "starred"})
    content = response.content.decode()

    assert response.status_code == 200
    assert [repo.full_name for repo in response.context["page_obj"].object_list] == [
        "owner/newest",
        "owner/oldest",
        "owner/unknown",
    ]
    assert "Sort: Recently starred" in content
    assert "starred 2026-05-02" in content
    assert "starred 2026-04-01" in content


@pytest.mark.django_db
def test_starred_sort_label_is_scoped_to_starred_search(client):
    Repository.objects.create(
        full_name="owner/repo",
        owner="owner",
        name="repo",
        url="https://github.com/owner/repo",
        stars=10,
    )

    response = client.get(reverse("repos:search"), {"sort": "starred"})

    assert response.status_code == 200
    assert "Sort: Recently starred" not in response.content.decode()


@pytest.mark.django_db
def test_starred_repository_search_uses_shared_repository_filters(auth_client, profile):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    matching_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Django web framework",
        language="Python",
        topics=["django", "python"],
        generated_tags=["web-framework"],
        detected_stacks=["django"],
        package_managers=["poetry"],
        stars=80000,
        forks=32000,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        github_pushed_at=timezone.now(),
        uses_ai_for_development=True,
    )
    other_starred_repo = Repository.objects.create(
        full_name="nodejs/node",
        owner="nodejs",
        name="node",
        url="https://github.com/nodejs/node",
        description="JavaScript runtime",
        language="JavaScript",
        topics=["javascript", "runtime"],
        generated_tags=["server-runtime"],
        stars=110000,
        forks=40000,
        first_commit_at=timezone.now() - timedelta(days=365 * 2),
        github_pushed_at=timezone.now() - timedelta(days=500),
        is_archived=True,
    )
    unstarred_match = Repository.objects.create(
        full_name="example/django-tool",
        owner="example",
        name="django-tool",
        url="https://github.com/example/django-tool",
        description="Django web framework",
        language="Python",
        topics=["django"],
        generated_tags=["web-framework"],
        stars=90000,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        github_pushed_at=timezone.now(),
        uses_ai_for_development=True,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=matching_repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=unstarred_match)
    UserStarredRepository.objects.create(profile=profile, repository=matching_repo)
    UserStarredRepository.objects.create(profile=profile, repository=other_starred_repo)

    response = auth_client.get(
        reverse("repos:starred"),
        {
            "q": "django",
            "list": "awesome-django",
            "language": "Python",
            "topic": "django",
            "generated_tag": "web-framework",
            "stack": "django",
            "package_manager": "poetry",
            "min_stars": "50",
            "updated_days": "30",
            "min_age_years": "10",
            "archived": "no",
            "ai_development": "yes",
            "sort": "forks",
        },
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "django/django" in content
    assert "nodejs/node" not in content
    assert "example/django-tool" not in content
    assert_option_label_with_count(content, "Awesome Django", 1)
    assert_option_label_with_count(content, "web-framework", 1)
    assert "Sort: Forks" in content
    assert 'aria-label="Remove Sort filter: Forks"' in content
    assert 'aria-label="Remove Framework filter: django"' in content
    framework_chip = re.search(
        r'<a\s+href="([^"]*)"\s+class="[^"]*"\s+aria-label="Remove Framework filter: django"',
        content,
    )
    assert framework_chip is not None
    assert "stack=django" not in framework_chip.group(1)
    assert response.context["page_obj"].paginator.count == 1


def test_repository_filter_remove_querystring_resets_page_and_coupled_params():
    params = QueryDict(
        "page=2&q=django&language=Python&framework=django&stack=django&sort=forks&sort_direction=asc"
    )

    querystring = repository_filter_remove_querystring(params, "framework")

    assert "page=2" not in querystring
    assert "framework=django" not in querystring
    assert "stack=django" not in querystring
    assert "q=django" in querystring
    assert "language=Python" in querystring
    assert "sort=forks" in querystring
    assert "sort_direction=asc" in querystring

    sort_querystring = repository_filter_remove_querystring(params, "sort")

    assert "sort=forks" not in sort_querystring
    assert "sort_direction=asc" not in sort_querystring
    assert "q=django" in sort_querystring


def stub_repository_readme(monkeypatch, content="# Django\n"):
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        lambda full_name, **kwargs: {
            "ok": True,
            "readme": content,
            "readme_path": "README.md",
            "readme_url": f"https://raw.githubusercontent.com/{full_name}/main/README.md",
            "readme_last_error": "",
        },
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_tree_items",
        lambda full_name, default_branch, **kwargs: [],
    )


@pytest.mark.django_db
def test_upsert_repository_from_github_records_snapshot(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=80000, forks=32000, watchers=1200),
    )
    stub_repository_readme(monkeypatch)

    repo = upsert_repository_from_github("django/django")

    snapshot = RepositorySnapshot.objects.get(repository=repo)
    assert repo.stars == 80000
    assert repo.forks == 32000
    assert repo.watchers == 1200
    assert repo.commit_count == 123
    assert repo.first_commit_at == datetime(2005, 7, 13, tzinfo=UTC)
    assert snapshot.stars == repo.stars
    assert snapshot.forks == repo.forks
    assert snapshot.watchers == repo.watchers
    assert snapshot.commit_count == repo.commit_count
    assert snapshot.first_commit_at == repo.first_commit_at
    assert snapshot.captured_at == repo.last_synced_at


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_homepage_from_github_api(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(),
    )
    stub_repository_readme(monkeypatch)

    repo = upsert_repository_from_github("django/django")

    snapshot = RepositorySnapshot.objects.get(repository=repo)
    assert repo.homepage_url == "https://www.djangoproject.com/"
    assert snapshot.homepage_url == repo.homepage_url


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_homepage_from_description_link(monkeypatch):
    def fake_fetch_json(url, **kwargs):
        payload = github_repo_payload()
        payload["homepage"] = ""
        payload["description"] = "Docs at https://docs.djangoproject.com/."
        return payload

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)
    stub_repository_readme(monkeypatch)

    repo = upsert_repository_from_github("django/django")

    snapshot = RepositorySnapshot.objects.get(repository=repo)
    assert repo.homepage_url == "https://docs.djangoproject.com/"
    assert snapshot.homepage_url == repo.homepage_url


@pytest.mark.django_db
def test_upsert_repository_from_github_records_snapshot_for_each_refresh(monkeypatch):
    payloads = [
        github_repo_payload(stars=10, forks=3, watchers=1),
        github_repo_payload(stars=15, forks=4, watchers=2),
    ]

    def fake_fetch_json(url, **kwargs):
        return payloads.pop(0)

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)
    stub_repository_readme(monkeypatch)

    repo = upsert_repository_from_github("django/django")
    repo = upsert_repository_from_github("django/django")

    assert repo.stars == 15
    assert list(
        repo.snapshots.order_by("created_at").values_list("stars", "forks", "watchers")
    ) == [(10, 3, 1), (15, 4, 2)]


@pytest.mark.django_db
def test_upsert_repository_from_github_rolls_back_when_snapshot_fails(monkeypatch):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=10,
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    stub_repository_readme(monkeypatch, content="# Updated Django\n")

    def fail_snapshot(repository, *, captured_at=None, source="github_api"):
        raise RuntimeError("snapshot failed")

    monkeypatch.setattr("apps.repos.services.record_repository_snapshot", fail_snapshot)

    with pytest.raises(RuntimeError, match="snapshot failed"):
        upsert_repository_from_github("django/django")

    repo.refresh_from_db()
    assert repo.stars == 10
    assert repo.last_synced_at is None
    assert RepositorySnapshot.objects.filter(repository=repo).count() == 0


def test_fetch_repository_readme_data_decodes_github_contents_metadata(monkeypatch):
    readme = "# Django\n\nThe Web framework."
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: {
            "encoding": "base64",
            "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
            "path": "README.md",
            "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
        },
    )

    result = fetch_repository_readme_data("django/django")

    assert result == {
        "ok": True,
        "readme": readme,
        "readme_path": "README.md",
        "readme_url": "https://raw.githubusercontent.com/django/django/main/README.md",
        "readme_last_error": "",
    }


def test_detect_ai_development_signals_identifies_common_agent_files():
    signals = detect_ai_development_signals(
        [
            {"path": "AGENTS.md", "type": "blob"},
            {"path": "docs/CONTRIBUTING.md", "type": "blob"},
            {"path": ".github/copilot-instructions.md", "type": "blob"},
            {"path": ".github/instructions/python.instructions.md", "type": "blob"},
            {"path": ".cursor", "type": "tree"},
            {"path": ".cursor/rules/backend.mdc", "type": "blob"},
            {"path": ".windsurf/rules/style.md", "type": "blob"},
            {"path": ".gemini/settings.json", "type": "blob"},
            {"path": ".devin/config.json", "type": "blob"},
            {"path": ".clinerules/testing.md", "type": "blob"},
            {"path": ".aider.conf.yml", "type": "blob"},
            {"path": ".coderabbit.yml", "type": "tree"},
        ]
    )

    signal_paths = {signal["path"] for signal in signals}
    assert "AGENTS.md" in signal_paths
    assert ".github/copilot-instructions.md" in signal_paths
    assert ".github/instructions/python.instructions.md" in signal_paths
    assert ".cursor" in signal_paths
    assert ".cursor/rules/backend.mdc" in signal_paths
    assert ".windsurf/rules/style.md" in signal_paths
    assert ".gemini/settings.json" in signal_paths
    assert ".devin/config.json" in signal_paths
    assert ".clinerules/testing.md" in signal_paths
    assert ".aider.conf.yml" in signal_paths
    assert "docs/CONTRIBUTING.md" not in signal_paths
    assert ".coderabbit.yml" not in signal_paths
    assert len(signal_paths) == len(signals)


def test_dependency_file_candidates_detect_manifests_and_skip_vendor_dirs():
    candidates = dependency_file_candidates(
        [
            {"path": "pyproject.toml", "type": "blob", "size": 200, "url": "blob:pyproject"},
            {
                "path": "frontend/package.json",
                "type": "blob",
                "size": 200,
                "url": "blob:package",
            },
            {
                "path": "frontend/node_modules/react/package.json",
                "type": "blob",
                "size": 200,
                "url": "blob:vendor",
            },
            {"path": "Cargo.toml", "type": "blob", "size": 200, "url": "blob:cargo"},
            {"path": "README.md", "type": "blob", "size": 200, "url": "blob:readme"},
        ]
    )

    assert [candidate["path"] for candidate in candidates] == [
        "Cargo.toml",
        "pyproject.toml",
        "frontend/package.json",
    ]


def test_detect_repository_stack_parses_manifests_and_records_evidence():
    contents = {
        "pyproject.toml": """
            [project]
            dependencies = ["Django>=5", "djangorestframework"]

            [tool.poetry]
            name = "example"
        """,
        "frontend/package.json": json.dumps(
            {
                "packageManager": "pnpm@9.0.0",
                "dependencies": {"next": "15.0.0", "react": "19.0.0"},
                "devDependencies": {"tailwindcss": "4.0.0"},
            }
        ),
    }
    tree_items = [
        {
            "path": path,
            "type": "blob",
            "size": len(content),
            "url": f"blob:{path}",
        }
        for path, content in contents.items()
    ]

    result = detect_repository_stack(
        tree_items,
        fetch_file_text=lambda candidate: contents[candidate["path"]],
    )

    assert result["ok"] is True
    assert result["dependency_ecosystems"] == ["javascript", "python"]
    assert result["package_managers"] == ["pnpm", "poetry"]
    assert result["detected_stacks"] == [
        "django",
        "django-rest-framework",
        "nextjs",
        "react",
        "tailwindcss",
    ]
    django_signal = next(signal for signal in result["stack_signals"] if signal["slug"] == "django")
    assert django_signal["confidence"] == "high"
    assert django_signal["evidence"] == [
        {"path": "pyproject.toml", "dependency": "django", "kind": "manifest"}
    ]
    assert {dependency_file["path"] for dependency_file in result["dependency_files"]} == {
        "pyproject.toml",
        "frontend/package.json",
    }


def test_parse_python_setup_only_reads_dependency_fields():
    result = parse_python_setup(
        """
        from setuptools import setup

        setup(
            name="example-project",
            author="Jane Smith",
            license="BSD-3-Clause",
            install_requires=["Django>=5", "fastapi[standard]"],
            extras_require={"dev": ["pytest", "ruff>=0.15"]},
            classifiers=["Framework :: Django"],
        )
        """
    )

    assert result["dependencies"] == ["django", "fastapi", "pytest", "ruff"]


def test_parse_python_setup_cfg_reads_options_dependency_sections():
    result = parse_python_setup(
        """
        [metadata]
        name = example-project
        author = Jane Smith

        [options]
        install_requires =
            Django>=5
            fastapi[standard]

        [options.extras_require]
        dev =
            pytest
            ruff>=0.15
        """
    )

    assert result["dependencies"] == ["django", "fastapi", "pytest", "ruff"]


def test_parse_pom_xml_rejects_entity_expansion():
    with pytest.raises(EntitiesForbidden):
        parse_pom_xml(
            """<?xml version="1.0"?>
            <!DOCTYPE project [
              <!ENTITY a "aaaaaaaaaa">
              <!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">
            ]>
            <project>
              <dependencies>
                <dependency><artifactId>&b;</artifactId></dependency>
              </dependencies>
            </project>
            """
        )


def test_package_manager_label_template_filter_formats_slugs():
    rendered = Template("{% load repo_stack_tags %}{{ manager|package_manager_label }}").render(
        Context({"manager": "go-modules"})
    )

    assert rendered == "Go modules"


def test_fetch_repository_tree_items_rejects_truncated_github_trees(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: {
            "truncated": True,
            "tree": [{"path": "AGENTS.md", "type": "blob"}],
        },
    )

    with pytest.raises(RuntimeError, match="GitHub tree for django/django is truncated"):
        fetch_repository_tree_items("django/django", "main")


def test_fetch_github_commit_count_uses_last_link_page(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fetch_github_commit_count_and_first_commit_at,
    )
    captured = {}

    class DummyResponse:
        headers = {
            "Link": (
                "<https://api.github.com/repositories/1/commits?sha=main&per_page=1&page=2>;"
                ' rel="next", '
                "<https://api.github.com/repositories/1/commits?sha=main&per_page=1&page=456>;"
                ' rel="last"'
            )
        }

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'[{"sha": "abc"}]'

    def fake_urlopen(request, timeout=30):
        captured["url"] = request.full_url
        return DummyResponse()

    monkeypatch.setattr("apps.repos.services.urlopen", fake_urlopen)

    assert fetch_github_commit_count("owner/repo", "main") == 456
    assert captured["url"].startswith("https://api.github.com/repos/owner/repo/commits?")
    assert "per_page=1" in captured["url"]


def test_fetch_github_commit_count_and_first_commit_at_uses_oldest_page(monkeypatch):
    requested_urls = []

    class DummyResponse:
        def __init__(self, *, body: bytes, headers: dict):
            self._body = body
            self.headers = headers

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return self._body

    def fake_urlopen(request, timeout=30):
        requested_urls.append(request.full_url)
        if "page=456" in request.full_url:
            return DummyResponse(
                body=(
                    b'[{"commit": {"author": {"date": "2007-04-10T12:00:00Z"}, '
                    b'"committer": {"date": "2008-04-10T12:00:00Z"}}}]'
                ),
                headers={},
            )
        return DummyResponse(
            body=b'[{"commit": {"committer": {"date": "2026-05-20T12:00:00Z"}}}]',
            headers={
                "Link": (
                    "<https://api.github.com/repositories/1/commits?sha=main&per_page=1&page=2>;"
                    ' rel="next", '
                    "<https://api.github.com/repositories/1/commits?sha=main&per_page=1&page=456>;"
                    ' rel="last"'
                )
            },
        )

    monkeypatch.setattr("apps.repos.services.urlopen", fake_urlopen)

    commit_count, first_commit_at = fetch_github_commit_count_and_first_commit_at(
        "owner/repo",
        "main",
    )

    assert commit_count == 456
    assert first_commit_at == datetime(2007, 4, 10, 12, tzinfo=UTC)
    assert any("page=456" in url for url in requested_urls)


def test_minimum_age_cutoff_uses_calendar_years(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.timezone.now",
        lambda: datetime(2024, 2, 29, 12, 30, 15, 123456, tzinfo=UTC),
    )

    assert minimum_age_cutoff({"min_age_years": "1"}) == datetime(
        2023,
        2,
        28,
        12,
        30,
        15,
        tzinfo=UTC,
    )


@pytest.mark.django_db
def test_backfill_first_commit_dates_command_updates_existing_rows(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        default_branch="main",
    )
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        default_branch="main",
    )
    fetched = {
        "wsvincent/awesome-django": (350, datetime(2015, 1, 2, tzinfo=UTC)),
        "django/django": (90000, datetime(2005, 7, 13, tzinfo=UTC)),
    }

    monkeypatch.setattr(
        "apps.repos.management.commands.backfill_first_commit_dates."
        "fetch_github_commit_count_and_first_commit_at",
        lambda full_name, default_branch, **kwargs: fetched[full_name],
    )

    output = StringIO()
    call_command("backfill_first_commit_dates", stdout=output)

    awesome_list.refresh_from_db()
    repository.refresh_from_db()
    assert awesome_list.commits_count == 350
    assert awesome_list.first_commit_at == datetime(2015, 1, 2, tzinfo=UTC)
    assert repository.commit_count == 90000
    assert repository.first_commit_at == datetime(2005, 7, 13, tzinfo=UTC)
    assert "'updated': 1" in output.getvalue()


def test_fetch_github_commit_count_counts_single_unpaginated_page(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fetch_github_commit_count_and_first_commit_at,
    )

    class DummyResponse:
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'[{"sha": "abc"}]'

    monkeypatch.setattr("apps.repos.services.urlopen", lambda request, timeout=30: DummyResponse())

    assert fetch_github_commit_count("owner/repo", "main") == 1


def test_fetch_github_commit_count_requires_default_branch(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fetch_github_commit_count_and_first_commit_at,
    )

    with pytest.raises(ValueError, match="default branch"):
        fetch_github_commit_count("owner/repo", "")


def test_attach_awesome_list_commit_count_is_explicit_about_commit_fetch(monkeypatch):
    calls = []

    def fake_fetch_commit_activity(full_name, default_branch, *, token=None):
        calls.append((full_name, default_branch, token))
        return 456, datetime(2008, 4, 10, tzinfo=UTC)

    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fake_fetch_commit_activity,
    )
    meta = {"default_branch": "trunk"}

    attach_awesome_list_commit_count("owner/repo", meta)

    assert calls == [("owner/repo", "trunk", None)]
    assert meta["commits_count"] == 456
    assert meta["first_commit_at"] == datetime(2008, 4, 10, tzinfo=UTC)


def test_attach_awesome_list_commit_count_skips_missing_default_branch(monkeypatch):
    def fail_fetch_commit_activity(full_name, default_branch):
        raise AssertionError("commit count should not be fetched without a default branch")

    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fail_fetch_commit_activity,
    )
    meta = {}

    attach_awesome_list_commit_count("owner/repo", meta)

    assert "commits_count" not in meta
    assert "first_commit_at" not in meta


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_readme(monkeypatch):
    readme = "# Django\nThe Web framework.\n"

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "path": "README.md",
                "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
            }
        return github_repo_payload(stars=80000, forks=32000, watchers=1200)

    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        fake_fetch_json,
    )

    repo = upsert_repository_from_github("django/django")

    assert repo.readme == readme
    assert repo.readme_path == "README.md"
    assert repo.readme_url == ("https://raw.githubusercontent.com/django/django/main/README.md")
    assert repo.readme_synced_at == repo.last_synced_at
    assert repo.readme_last_error == ""


@pytest.mark.django_db
def test_upsert_repository_from_github_marks_awesome_list_candidates(monkeypatch):
    readme = (
        "# Awesome Django\n\n"
        "- [Django](https://github.com/django/django)\n"
        "- [Channels](https://github.com/django/channels)\n"
        "- [Wagtail](https://github.com/wagtail/wagtail)\n"
    )

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "path": "README.md",
                "download_url": (
                    "https://raw.githubusercontent.com/wsvincent/awesome-django/main/README.md"
                ),
            }
        if "/git/trees/" in url:
            return {"tree": []}
        payload = github_repo_payload(full_name="wsvincent/awesome-django")
        payload["topics"] = ["django", "awesome-list"]
        return payload

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)

    repo = upsert_repository_from_github("wsvincent/awesome-django")

    assert repo.is_awesome_list_candidate is True
    assert repo.awesome_list_detected_repo_count == 3
    assert repo.awesome_list_detection_reasons == [
        "github_topic_awesome_list",
        "awesome_readme_title",
        "awesome_repo_name",
    ]


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_ai_development_signals(monkeypatch):
    readme = "# Django\nThe Web framework.\n"

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "path": "README.md",
                "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
            }
        if "/git/trees/" in url:
            return {
                "tree": [
                    {"path": "AGENTS.md", "type": "blob"},
                    {"path": ".github/copilot-instructions.md", "type": "blob"},
                ]
            }
        return github_repo_payload(stars=80000, forks=32000, watchers=1200)

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)

    repo = upsert_repository_from_github("django/django")

    assert repo.uses_ai_for_development is True
    assert {signal["path"] for signal in repo.ai_development_signals} == {
        "AGENTS.md",
        ".github/copilot-instructions.md",
    }


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_stack_detection(monkeypatch):
    readme = "# Django\nThe Web framework.\n"
    pyproject = """
        [project]
        dependencies = ["django>=5", "fastapi"]

        [tool.poetry]
        name = "django"
    """
    package_json = json.dumps(
        {
            "packageManager": "pnpm@9.0.0",
            "dependencies": {"next": "15.0.0", "react": "19.0.0"},
        }
    )

    def blob_payload(content):
        return {
            "encoding": "base64",
            "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
            "size": len(content),
        }

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "path": "README.md",
                "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
            }
        if "/git/trees/" in url:
            return {
                "tree": [
                    {
                        "path": "pyproject.toml",
                        "type": "blob",
                        "size": len(pyproject),
                        "url": "blob:pyproject",
                    },
                    {
                        "path": "frontend/package.json",
                        "type": "blob",
                        "size": len(package_json),
                        "url": "blob:package",
                    },
                ]
            }
        if url == "blob:pyproject":
            return blob_payload(pyproject)
        if url == "blob:package":
            return blob_payload(package_json)
        return github_repo_payload(stars=80000, forks=32000, watchers=1200)

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)

    repo = upsert_repository_from_github("django/django")

    assert repo.dependency_ecosystems == ["javascript", "python"]
    assert repo.package_managers == ["pnpm", "poetry"]
    assert repo.detected_stacks == ["django", "fastapi", "nextjs", "react"]
    assert repo.stack_detected_at == repo.last_synced_at
    assert repo.stack_detection_last_error == ""
    assert {dependency_file["path"] for dependency_file in repo.dependency_files} == {
        "frontend/package.json",
        "pyproject.toml",
    }
    assert (
        next(signal for signal in repo.stack_signals if signal["slug"] == "django")["label"]
        == "Django"
    )


@pytest.mark.django_db
def test_upsert_repository_from_github_preserves_readme_when_refresh_fails(monkeypatch):
    previous_readme_synced_at = timezone.now() - timedelta(days=1)
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        readme="# Existing README\n",
        readme_path="README.md",
        readme_url="https://raw.githubusercontent.com/django/django/main/README.md",
        readme_synced_at=previous_readme_synced_at,
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        lambda full_name, **kwargs: {
            "ok": False,
            "readme": "",
            "readme_path": "",
            "readme_url": "",
            "readme_last_error": "404 Not Found",
        },
    )

    repo = upsert_repository_from_github(repo.full_name)

    assert repo.stars == 15
    assert repo.readme == "# Existing README\n"
    assert repo.readme_path == "README.md"
    assert repo.readme_url == ("https://raw.githubusercontent.com/django/django/main/README.md")
    assert repo.readme_last_error == "404 Not Found"
    assert repo.readme_synced_at == previous_readme_synced_at


@pytest.mark.django_db
def test_upsert_repository_from_github_preserves_ai_signals_when_tree_fetch_fails(monkeypatch):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        uses_ai_for_development=True,
        ai_development_signals=[
            {
                "path": "AGENTS.md",
                "kind": "file",
                "tool": "Agent instructions",
                "signal": "agent_instructions",
            }
        ],
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        lambda full_name, **kwargs: {
            "ok": False,
            "readme": "",
            "readme_path": "",
            "readme_url": "",
            "readme_last_error": "404 Not Found",
        },
    )

    def fail_tree_fetch(full_name, default_branch, **kwargs):
        raise RuntimeError("tree failed")

    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_tree_items",
        fail_tree_fetch,
    )

    repo = upsert_repository_from_github(repo.full_name)

    assert repo.uses_ai_for_development is True
    assert repo.ai_development_signals[0]["path"] == "AGENTS.md"


@pytest.mark.django_db
def test_upsert_repository_from_github_preserves_stack_detection_when_tree_fetch_fails(
    monkeypatch,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        dependency_files=[{"path": "pyproject.toml"}],
        dependency_ecosystems=["python"],
        package_managers=["poetry"],
        detected_stacks=["django"],
        stack_signals=[{"slug": "django", "label": "Django"}],
        stack_detected_at=timezone.now() - timedelta(days=1),
    )
    previous_stack_detected_at = repo.stack_detected_at
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        lambda full_name, **kwargs: {
            "ok": False,
            "readme": "",
            "readme_path": "",
            "readme_url": "",
            "readme_last_error": "404 Not Found",
        },
    )

    def fail_tree_fetch(full_name, default_branch, **kwargs):
        raise RuntimeError("tree failed")

    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_tree_items",
        fail_tree_fetch,
    )

    repo = upsert_repository_from_github(repo.full_name)

    assert repo.detected_stacks == ["django"]
    assert repo.package_managers == ["poetry"]
    assert repo.stack_signals == [{"slug": "django", "label": "Django"}]
    assert repo.stack_detected_at == previous_stack_detected_at
    assert repo.stack_detection_last_error == "tree failed"


@pytest.mark.django_db
def test_sync_repository_stack_detection_updates_existing_repository(monkeypatch):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        default_branch="main",
    )
    pyproject = '[project]\ndependencies = ["django"]\n'

    def fake_fetch_json(url, **kwargs):
        if "/git/trees/" in url:
            return {
                "tree": [
                    {
                        "path": "pyproject.toml",
                        "type": "blob",
                        "size": len(pyproject),
                        "url": "blob:pyproject",
                    }
                ]
            }
        if url == "blob:pyproject":
            return {
                "encoding": "base64",
                "content": base64.b64encode(pyproject.encode("utf-8")).decode("ascii"),
                "size": len(pyproject),
            }
        raise AssertionError(url)

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)

    result = sync_repository_stack_detection(repository)

    repository.refresh_from_db()
    assert result["detected_stacks"] == ["django"]
    assert repository.detected_stacks == ["django"]
    assert repository.stack_detected_at is not None


@pytest.mark.django_db
def test_sync_repository_stack_detection_passes_github_token(monkeypatch):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        default_branch="main",
    )
    captured = {}

    def fake_fetch_repository_stack_detection(full_name, default_branch, **kwargs):
        captured["full_name"] = full_name
        captured["default_branch"] = default_branch
        captured["token"] = kwargs.get("token")
        return {
            "ok": True,
            "dependency_files": [],
            "dependency_ecosystems": [],
            "package_managers": [],
            "detected_stacks": [],
            "stack_signals": [],
        }

    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_stack_detection",
        fake_fetch_repository_stack_detection,
    )

    result = sync_repository_stack_detection(repository, token="ghp_test")

    assert result["ok"] is True
    assert captured == {
        "full_name": "django/django",
        "default_branch": "main",
        "token": "ghp_test",
    }


@pytest.mark.django_db
def test_detect_repository_stacks_command_prefers_unsynced_repositories():
    synced = Repository.objects.create(
        full_name="django/synced",
        owner="django",
        name="synced",
        url="https://github.com/django/synced",
        default_branch="main",
        stack_detected_at=timezone.now(),
    )
    unsynced = Repository.objects.create(
        full_name="django/unsynced",
        owner="django",
        name="unsynced",
        url="https://github.com/django/unsynced",
        default_branch="main",
    )

    output = StringIO()
    call_command("detect_repository_stacks", "--all", "--dry-run", "--limit", "2", stdout=output)

    lines = [line for line in output.getvalue().splitlines() if line.startswith("Would inspect")]
    assert lines == [
        f"Would inspect {unsynced.full_name}",
        f"Would inspect {synced.full_name}",
    ]


@pytest.mark.django_db
def test_detect_repository_stacks_command_passes_github_token(monkeypatch):
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        default_branch="main",
    )
    captured = {}

    def fake_sync_repository_stack_detection(repository, **kwargs):
        captured["repository"] = repository.full_name
        captured["token"] = kwargs.get("token")
        return {
            "ok": True,
            "detected_stacks": ["django"],
            "dependency_files": [{"path": "pyproject.toml"}],
        }

    monkeypatch.setattr(
        "apps.repos.management.commands.detect_repository_stacks.sync_repository_stack_detection",
        fake_sync_repository_stack_detection,
    )
    monkeypatch.setattr(
        "apps.repos.management.commands.detect_repository_stacks.github_token",
        lambda: "env-token",
    )

    call_command("detect_repository_stacks", "--github-token", "cli-token", stdout=StringIO())

    assert captured == {"repository": "django/django", "token": "cli-token"}


@pytest.mark.django_db
def test_upsert_repository_from_github_preserves_commit_count_when_fetch_fails(
    monkeypatch,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        commit_count=42,
        first_commit_at=datetime(2005, 7, 13, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    stub_repository_readme(monkeypatch)

    def fail_commit_activity(full_name, default_branch, **kwargs):
        raise RuntimeError("commit activity failed")

    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fail_commit_activity,
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count",
        fail_commit_activity,
    )

    repo = upsert_repository_from_github(repo.full_name)
    snapshot = RepositorySnapshot.objects.get(repository=repo)

    assert repo.stars == 15
    assert repo.commit_count == 42
    assert repo.first_commit_at == datetime(2005, 7, 13, tzinfo=UTC)
    assert snapshot.commit_count == 42
    assert snapshot.first_commit_at == datetime(2005, 7, 13, tzinfo=UTC)


@pytest.mark.django_db
def test_upsert_repository_from_github_uses_single_commit_count_call_when_first_commit_exists(
    monkeypatch,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        commit_count=42,
        first_commit_at=datetime(2005, 7, 13, tzinfo=UTC),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    stub_repository_readme(monkeypatch)

    def fail_commit_activity(full_name, default_branch, **kwargs):
        raise AssertionError("first commit date should not be refetched")

    monkeypatch.setattr(
        "apps.repos.services.fetch_github_commit_count_and_first_commit_at",
        fail_commit_activity,
    )
    monkeypatch.setattr("apps.repos.services.fetch_github_commit_count", lambda *args, **kwargs: 43)

    repo = upsert_repository_from_github(repo.full_name)

    assert repo.commit_count == 43
    assert repo.first_commit_at == datetime(2005, 7, 13, tzinfo=UTC)


@pytest.mark.django_db
def test_upsert_repository_from_github_can_refresh_metadata_without_readme(monkeypatch):
    previous_readme_synced_at = timezone.now() - timedelta(days=1)
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        readme="# Existing README\n",
        readme_path="README.md",
        readme_url="https://raw.githubusercontent.com/django/django/main/README.md",
        readme_synced_at=previous_readme_synced_at,
        readme_last_error="old README error",
        stars=10,
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: github_repo_payload(stars=15, forks=4, watchers=2),
    )

    def fail_readme_fetch(full_name, **kwargs):
        raise AssertionError(f"should not fetch README for {full_name}")

    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        fail_readme_fetch,
    )

    repo = upsert_repository_from_github(repo.full_name, include_readme=False)

    assert repo.stars == 15
    assert repo.readme == "# Existing README\n"
    assert repo.readme_path == "README.md"
    assert repo.readme_url == "https://raw.githubusercontent.com/django/django/main/README.md"
    assert repo.readme_last_error == "old README error"
    assert repo.readme_synced_at == previous_readme_synced_at
    assert RepositorySnapshot.objects.filter(repository=repo).count() == 1


@pytest.mark.django_db
def test_upsert_repository_from_github_preserves_ai_signals_when_tree_is_truncated(
    monkeypatch,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        uses_ai_for_development=True,
        ai_development_signals=[
            {
                "path": "AGENTS.md",
                "kind": "file",
                "tool": "Agent instructions",
                "signal": "agent_instructions",
            }
        ],
    )

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"# Django\n").decode("ascii"),
                "path": "README.md",
                "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
            }
        if "/git/trees/" in url:
            return {
                "truncated": True,
                "tree": [],
            }
        return github_repo_payload(stars=15, forks=4, watchers=2)

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)

    repo = upsert_repository_from_github(repo.full_name)

    assert repo.stars == 15
    assert repo.uses_ai_for_development is True
    assert repo.ai_development_signals[0]["path"] == "AGENTS.md"


@pytest.mark.django_db
def test_enqueue_awesome_list_missing_repo_syncs_task_queues_daily_budget(
    monkeypatch,
    settings,
):
    settings.GITHUB_DAILY_DISCOVERY_REPOSITORY_LIMIT = 1
    active = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeList.objects.create(
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        is_active=False,
    )
    queued = []

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return f"task-{len(queued)}"

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    from apps.repos.tasks import enqueue_awesome_list_missing_repo_syncs_task

    result = enqueue_awesome_list_missing_repo_syncs_task(limit_per_list=5)

    assert result == {
        "queued": 1,
        "task_ids": ["task-1"],
        "daily_limit": 1,
    }
    assert queued == [
        (
            "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
            (active.id,),
            {
                "limit": 5,
                "daily_limit": 1,
                "group": "Daily awesome-list missing repo discovery",
            },
        )
    ]


@pytest.mark.django_db
def test_enqueue_missing_repositories_for_awesome_list_task_queues_missing_repos(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python",
        source_url="https://github.com/vinta/awesome-python",
    )
    queued = []

    def fake_discover(awesome_list, limit=None):
        assert limit == 10
        return {
            "awesome_list": awesome_list.slug,
            "discovered": 3,
            "missing": ["django/django", "encode/httpx"],
            "missing_count": 2,
            "linked_existing": 1,
            "skipped_existing": 0,
        }

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return f"task-{len(queued)}"

    monkeypatch.setattr(
        "apps.repos.tasks.AwesomeList.discover_missing_repositories_from_source",
        fake_discover,
    )
    monkeypatch.setattr(
        "apps.repos.tasks._try_reserve_daily_missing_repository_slot",
        lambda daily_limit: True,
    )
    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    from apps.repos.tasks import enqueue_missing_repositories_for_awesome_list_task

    result = enqueue_missing_repositories_for_awesome_list_task(awesome_list.id, limit=10)

    assert result["queued"] == 2
    assert result["task_ids"] == ["task-1", "task-2"]
    assert result["daily_limit"] == 250
    assert result["budget_exhausted"] is False
    assert queued == [
        (
            "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
            (awesome_list.id, "django/django"),
            {"group": "Add missing awesome-list repos"},
        ),
        (
            "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
            (awesome_list.id, "encode/httpx"),
            {"group": "Add missing awesome-list repos"},
        ),
    ]


@pytest.mark.django_db
def test_enqueue_missing_repositories_for_awesome_list_task_assigns_sync_tokens(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python",
        source_url="https://github.com/vinta/awesome-python",
    )
    queued = []

    monkeypatch.setattr(
        "apps.repos.tasks.AwesomeList.discover_missing_repositories_from_source",
        lambda awesome_list, limit=None: {
            "awesome_list": awesome_list.slug,
            "discovered": 2,
            "missing": ["django/django", "encode/httpx"],
            "missing_count": 2,
            "linked_existing": 0,
            "skipped_existing": 0,
        },
    )
    monkeypatch.setattr(
        "apps.repos.tasks._try_reserve_daily_missing_repository_slot",
        lambda daily_limit: True,
    )
    monkeypatch.setattr(
        "apps.repos.tasks.github_repository_sync_token_pool",
        lambda: ["primary-token", "user-token"],
    )

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return f"task-{len(queued)}"

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    from apps.repos.tasks import enqueue_missing_repositories_for_awesome_list_task

    result = enqueue_missing_repositories_for_awesome_list_task(awesome_list.id)

    assert result["queued"] == 2
    assert queued == [
        (
            "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
            (awesome_list.id, "django/django"),
            {
                "github_token_index": 0,
                "group": "Add missing awesome-list repos",
            },
        ),
        (
            "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
            (awesome_list.id, "encode/httpx"),
            {
                "github_token_index": 1,
                "group": "Add missing awesome-list repos",
            },
        ),
    ]


@pytest.mark.django_db
def test_enqueue_missing_repositories_for_awesome_list_task_truncates_logged_ids(
    monkeypatch,
):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python",
        source_url="https://github.com/vinta/awesome-python",
    )
    missing = [f"owner/repo-{index}" for index in range(30)]
    log_events = []

    def fake_discover(awesome_list, limit=None):
        return {
            "awesome_list": awesome_list.slug,
            "discovered": len(missing),
            "missing": missing,
            "missing_count": len(missing),
            "linked_existing": 0,
            "skipped_existing": 0,
        }

    def fake_async_task(func_path, *args, **kwargs):
        return f"task-{args[1]}"

    class FakeLogger:
        def info(self, event, **kwargs):
            log_events.append((event, kwargs))

        def error(self, event, **kwargs):
            log_events.append((event, kwargs))

    monkeypatch.setattr(
        "apps.repos.tasks.AwesomeList.discover_missing_repositories_from_source",
        fake_discover,
    )
    monkeypatch.setattr(
        "apps.repos.tasks._try_reserve_daily_missing_repository_slot",
        lambda daily_limit: True,
    )
    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)
    monkeypatch.setattr("apps.repos.tasks.logger", FakeLogger())

    from apps.repos.tasks import enqueue_missing_repositories_for_awesome_list_task

    result = enqueue_missing_repositories_for_awesome_list_task(awesome_list.id)
    finished_event = [
        kwargs
        for event, kwargs in log_events
        if event == "awesome_list_missing_repo_discovery_task_finished"
    ][0]

    assert result["queued"] == 30
    assert len(result["task_ids"]) == 30
    assert len(result["missing"]) == 30
    assert finished_event["result"]["queued"] == 30
    assert len(finished_event["result"]["task_ids"]) == 25
    assert len(finished_event["result"]["missing"]) == 25


@pytest.mark.django_db
def test_enqueue_missing_repositories_for_awesome_list_task_stops_at_daily_budget(
    monkeypatch,
):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python-budget",
        source_url="https://github.com/vinta/awesome-python",
    )
    queued = []
    budget_results = iter([True, False])

    monkeypatch.setattr(
        "apps.repos.tasks.AwesomeList.discover_missing_repositories_from_source",
        lambda awesome_list, limit=None: {
            "awesome_list": awesome_list.slug,
            "discovered": 2,
            "missing": ["django/django", "encode/httpx"],
            "missing_count": 2,
            "linked_existing": 0,
            "skipped_existing": 0,
        },
    )
    monkeypatch.setattr(
        "apps.repos.tasks._try_reserve_daily_missing_repository_slot",
        lambda daily_limit: next(budget_results),
    )

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return f"task-{len(queued)}"

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    from apps.repos.tasks import enqueue_missing_repositories_for_awesome_list_task

    result = enqueue_missing_repositories_for_awesome_list_task(
        awesome_list.id,
        daily_limit=1,
    )

    assert result["queued"] == 1
    assert result["task_ids"] == ["task-1"]
    assert result["daily_limit"] == 1
    assert result["budget_exhausted"] is True
    assert queued == [
        (
            "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
            (awesome_list.id, "django/django"),
            {"group": "Add missing awesome-list repos"},
        )
    ]


@pytest.mark.django_db
def test_add_missing_repository_to_awesome_list_task_persists_last_error(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )

    def fail_add_repository(awesome_list, repo_full_name, *, github_access_token=None):
        raise RuntimeError(f"GitHub failed for {repo_full_name}")

    monkeypatch.setattr("apps.repos.tasks.add_repository_to_awesome_list", fail_add_repository)

    from apps.repos.tasks import add_missing_repository_to_awesome_list_task

    with pytest.raises(RuntimeError, match="GitHub failed for django/django"):
        add_missing_repository_to_awesome_list_task(awesome_list.id, "django/django")

    awesome_list.refresh_from_db()
    assert awesome_list.last_error == "GitHub failed for django/django"


@pytest.mark.django_db
def test_add_missing_repository_to_awesome_list_task_passes_sync_token(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    captured = {}

    def fake_add_repository(awesome_list, repo_full_name, *, github_access_token=None):
        captured["awesome_list"] = awesome_list
        captured["repo_full_name"] = repo_full_name
        captured["github_access_token"] = github_access_token
        return {
            "awesome_list": awesome_list.slug,
            "repository": repo_full_name,
            "repository_created": True,
            "link_created": True,
        }

    monkeypatch.setattr("apps.repos.tasks.add_repository_to_awesome_list", fake_add_repository)
    monkeypatch.setattr(
        "apps.repos.tasks.github_repository_sync_token_for_index",
        lambda index: "user-token",
    )

    from apps.repos.tasks import add_missing_repository_to_awesome_list_task

    result = add_missing_repository_to_awesome_list_task(
        awesome_list.id,
        "django/django",
        github_token_index=3,
    )

    assert result["repository"] == "django/django"
    assert captured == {
        "awesome_list": awesome_list,
        "repo_full_name": "django/django",
        "github_access_token": "user-token",
    }


@pytest.mark.django_db
def test_fetch_json_uses_github_token(monkeypatch):
    captured = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok":true}'

    def fake_urlopen(request, timeout=30):
        captured["headers"] = dict(request.header_items())
        return DummyResponse()

    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    monkeypatch.setattr("apps.repos.services.urlopen", fake_urlopen)

    assert captured == {}
    assert fetch_json("https://api.github.com/repos/example/example") == {"ok": True}
    headers = {k.lower(): v for k, v in captured["headers"].items()}
    assert headers["authorization"] == "Bearer ghp_test_token"


@pytest.mark.django_db
def test_github_repository_sync_token_pool_keeps_configured_token_first(
    monkeypatch,
    settings,
    profile,
    django_user_model,
):
    settings.GITHUB_REPOSITORY_SYNC_USE_USER_TOKENS = True
    monkeypatch.setenv("GITHUB_TOKEN", "primary-token")
    profile.github_starred_repos_import_enabled = True
    profile.save(update_fields=["github_starred_repos_import_enabled", "updated_at"])
    attach_github_token(profile, token="user-token-a", uid="github-a")
    attach_github_token(profile, token="primary-token", uid="github-duplicate-primary")

    other_user = django_user_model.objects.create_user(
        username="other",
        email="other@example.com",
        password="password123",
    )
    other_user.profile.github_starred_repos_import_enabled = True
    other_user.profile.save(update_fields=["github_starred_repos_import_enabled", "updated_at"])
    attach_github_token(other_user.profile, token="user-token-b", uid="github-b")

    opted_out_user = django_user_model.objects.create_user(
        username="opted-out-sync",
        email="opted-out-sync@example.com",
        password="password123",
    )
    attach_github_token(opted_out_user.profile, token="opted-out-token", uid="github-opted-out")

    expired_account = SocialAccount.objects.create(
        user=profile.user,
        provider="github",
        uid="github-expired",
    )
    SocialToken.objects.create(
        account=expired_account,
        token="expired-token",
        expires_at=timezone.now() - timedelta(minutes=5),
    )

    assert github_repository_sync_token_pool() == [
        "primary-token",
        "user-token-a",
        "user-token-b",
    ]
    assert github_repository_sync_token_for_index(0) == "primary-token"
    assert github_repository_sync_token_for_index(1) == "user-token-a"
    assert github_repository_sync_token_for_index(2) == "user-token-b"
    assert github_repository_sync_token_for_index(3) == "primary-token"


@pytest.mark.django_db
def test_github_repository_sync_token_pool_can_disable_user_tokens(
    monkeypatch,
    settings,
    profile,
):
    settings.GITHUB_REPOSITORY_SYNC_USE_USER_TOKENS = False
    monkeypatch.setenv("GITHUB_TOKEN", "primary-token")
    attach_github_token(profile, token="user-token-a", uid="github-a")

    assert github_repository_sync_token_pool() == ["primary-token"]
    assert github_repository_sync_token_for_index(1) == "primary-token"


def test_github_rate_limit_status_formats_core_limit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: {
            "resources": {
                "core": {
                    "limit": 5000,
                    "used": 123,
                    "remaining": 4877,
                    "reset": 1779449000,
                }
            }
        },
    )

    status = github_rate_limit_status()

    assert status["ok"] is True
    assert status["token_configured"] is True
    assert status["core"]["limit"] == 5000
    assert status["core"]["used"] == 123
    assert status["core"]["remaining"] == 4877
    assert status["core"]["reset_at"] is not None


def test_fetch_repository_readme_decodes_github_contents_payload(monkeypatch):
    readme = "# Django\n\nThe Web framework."
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url, **kwargs: {
            "encoding": "base64",
            "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
        },
    )

    assert fetch_repository_readme("django/django") == readme


def github_repo_api_payload():
    return {
        "full_name": "django/django",
        "owner": {"login": "django"},
        "name": "django",
        "html_url": "https://github.com/django/django",
        "description": "The Web framework",
        "homepage": "https://www.djangoproject.com/",
        "language": "Python",
        "license": {"spdx_id": "BSD-3-Clause"},
        "topics": ["django", "web"],
        "stargazers_count": 80000,
        "forks_count": 32000,
        "open_issues_count": 100,
        "subscribers_count": 2000,
        "default_branch": "main",
        "archived": False,
        "disabled": False,
        "fork": False,
        "created_at": "2005-07-21T00:00:00Z",
        "updated_at": "2026-05-22T00:00:00Z",
        "pushed_at": "2026-05-22T00:00:00Z",
    }


@pytest.mark.django_db(transaction=True)
def test_upsert_repository_from_github_syncs_embedding_from_readme(monkeypatch, settings):
    settings.OPENROUTER_API_KEY = "or-test"
    settings.REPOSITORY_EMBEDDINGS_ENABLED = True
    captured = {}

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            captured["readme_fetch_in_atomic"] = connection.in_atomic_block
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"# Django\n").decode("ascii"),
            }
        captured["metadata_fetch_in_atomic"] = connection.in_atomic_block
        return github_repo_api_payload()

    def fake_sync_repository_embedding(repository, readme_text):
        captured["repo"] = repository.full_name
        captured["description"] = repository.description
        captured["readme_text"] = readme_text
        captured["embedding_sync_in_atomic"] = connection.in_atomic_block

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)
    monkeypatch.setattr(
        "apps.repos.services.sync_repository_embedding",
        fake_sync_repository_embedding,
    )

    repo = upsert_repository_from_github("django/django")

    assert repo.description == "The Web framework"
    assert captured == {
        "metadata_fetch_in_atomic": False,
        "readme_fetch_in_atomic": False,
        "repo": "django/django",
        "description": "The Web framework",
        "readme_text": "# Django\n",
        "embedding_sync_in_atomic": False,
    }


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_readme_when_embeddings_unconfigured(
    monkeypatch,
    settings,
):
    settings.OPENROUTER_API_KEY = ""
    settings.REPOSITORY_EMBEDDINGS_ENABLED = True
    readme = "# Django\n"

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "path": "README.md",
                "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
            }
        return github_repo_api_payload()

    def fail_sync_repository_embedding(repository, readme_text):
        raise AssertionError("embedding sync should not run when embeddings are unconfigured")

    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        fake_fetch_json,
    )
    monkeypatch.setattr(
        "apps.repos.services.sync_repository_embedding",
        fail_sync_repository_embedding,
    )

    repo = upsert_repository_from_github("django/django")

    assert repo.description == "The Web framework"
    assert repo.readme == readme
    assert repo.readme_path == "README.md"


@pytest.mark.django_db
def test_save_repository_embedding_persists_pgvector(monkeypatch, settings):
    settings.OPENROUTER_API_KEY = "or-test"
    settings.REPOSITORY_EMBEDDINGS_ENABLED = True
    settings.REPOSITORY_EMBEDDING_MODEL = "openai/text-embedding-3-small"
    settings.REPOSITORY_EMBEDDING_DIMENSIONS = REPOSITORY_EMBEDDING_DIMENSIONS
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )

    def fake_generate_embedding(text, input_type="document"):
        from apps.repos.embeddings import EmbeddingResponse

        assert input_type == "document"
        assert "The Web framework" in text
        assert "# Django" in text
        return EmbeddingResponse(
            vector=[0.1] * REPOSITORY_EMBEDDING_DIMENSIONS,
            model="openai/text-embedding-3-small",
        )

    monkeypatch.setattr("apps.repos.embeddings.generate_embedding", fake_generate_embedding)

    embedding = save_repository_embedding(repo, "# Django")

    assert embedding is not None
    assert embedding.repository == repo
    assert embedding.source_text_chars > 0
    assert RepositoryEmbedding.objects.filter(repository=repo).exists()


@pytest.mark.django_db
def test_save_repository_embedding_skips_unchanged_source(monkeypatch, settings):
    settings.OPENROUTER_API_KEY = "or-test"
    settings.REPOSITORY_EMBEDDINGS_ENABLED = True
    settings.REPOSITORY_EMBEDDING_MODEL = "openai/text-embedding-3-small"
    settings.REPOSITORY_EMBEDDING_DIMENSIONS = REPOSITORY_EMBEDDING_DIMENSIONS
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )
    calls = 0

    def fake_generate_embedding(text, input_type="document"):
        nonlocal calls
        from apps.repos.embeddings import EmbeddingResponse

        calls += 1
        return EmbeddingResponse(
            vector=[0.1] * REPOSITORY_EMBEDDING_DIMENSIONS,
            model="openai/text-embedding-3-small",
        )

    monkeypatch.setattr("apps.repos.embeddings.generate_embedding", fake_generate_embedding)

    first = save_repository_embedding(repo, "# Django")
    second = save_repository_embedding(repo, "# Django")

    assert calls == 1
    assert first == second


@pytest.mark.django_db
def test_repository_embedding_text_uses_description_and_readme(settings):
    settings.REPOSITORY_EMBEDDING_MAX_CHARS = 80
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )

    text = build_repository_embedding_text(repo, "# Django\n" + ("docs " * 40))

    assert text.startswith("Repository: django/django")
    assert "Description:" in text
    assert "README:" in text
    assert len(text) == 80


def test_repository_embedding_text_handles_null_description(settings):
    settings.REPOSITORY_EMBEDDING_MAX_CHARS = 24000
    repo = Repository(full_name="owner/repo", description=None)

    text = build_repository_embedding_text(repo, "# README")

    assert text == "Repository: owner/repo\n\nREADME:\n# README"


def test_normalize_repository_tags_dedupes_and_limits(settings):
    settings.REPOSITORY_TAGGING_MAX_TAGS = 3

    assert normalize_repository_tags(
        ["Web Framework", "web/framework", "Django Admin!", "C++", "REST API"]
    ) == ["web-framework", "django-admin", "c++"]


def test_build_repository_tagging_payload_includes_known_repository_metadata(settings):
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    repo = Repository(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        language="Python",
        topics=["django", "web-framework", "orm"],
        ai_development_signals=[{"tool": "Codex", "path": "AGENTS.md"}],
    )

    payload = build_repository_tagging_payload(repo, "")

    assert payload is not None
    assert "Primary language:\nPython" in payload.text
    assert "GitHub topics:\ndjango, web-framework, orm" in payload.text
    assert "AI development signals:\nCodex (AGENTS.md)" in payload.text


@pytest.mark.django_db
def test_save_repository_tags_persists_generated_tags(monkeypatch, settings):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        readme="# Django\nDjango includes an ORM and admin interface.",
    )
    captured = {}

    def fake_generate_repository_tags(text):
        captured["text"] = text
        return ["web-framework", "orm", "admin-ui"]

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        fake_generate_repository_tags,
    )

    tags = save_repository_tags(repo, repo.readme)

    repo.refresh_from_db()
    assert tags == ["web-framework", "orm", "admin-ui"]
    assert repo.generated_tags == tags
    assert repo.generated_tags_model == repository_tagging_model_id()
    assert repo.generated_tags_source_hash
    assert repo.generated_tags_synced_at is not None
    assert repo.generated_tags_last_error == ""
    assert "The Web framework" in captured["text"]
    assert "Django includes an ORM" in captured["text"]


@pytest.mark.django_db
def test_save_repository_tags_skips_unchanged_source(monkeypatch, settings):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        readme="# Django",
    )
    calls = 0

    def fake_generate_repository_tags(text):
        nonlocal calls
        calls += 1
        return ["web-framework"]

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        fake_generate_repository_tags,
    )

    first = save_repository_tags(repo, repo.readme)
    repo.refresh_from_db()
    second = save_repository_tags(repo, repo.readme)

    assert calls == 1
    assert first == ["web-framework"]
    assert second == first


@pytest.mark.django_db(transaction=True)
def test_save_repository_tags_regenerates_when_tags_are_empty(
    monkeypatch,
    settings,
):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        readme="# Django",
        generated_tags=[],
        generated_tags_model=repository_tagging_model_id(),
        generated_tags_synced_at=timezone.now(),
    )
    payload = build_repository_tagging_payload(repo, repo.readme)
    assert payload is not None
    repo.generated_tags_source_hash = payload.text_hash
    repo.save(update_fields=["generated_tags_source_hash", "updated_at"])
    calls = 0

    def fake_generate_repository_tags(text):
        nonlocal calls
        calls += 1
        return ["web-framework"]

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        fake_generate_repository_tags,
    )

    tags = save_repository_tags(repo, repo.readme)

    repo.refresh_from_db()
    assert calls == 1
    assert tags == ["web-framework"]
    assert repo.generated_tags == tags


def test_generate_repository_tags_rejects_empty_normalized_output(monkeypatch):
    class FakeAgent:
        def run_sync(self, prompt):
            return SimpleNamespace(output=SimpleNamespace(tags=["!!!", "   "]))

    monkeypatch.setattr("apps.repos.tags._tagging_agent", lambda: FakeAgent())

    with pytest.raises(ValueError, match="no usable tags"):
        generate_repository_tags("Repository: owner/repo\n\nDescription:\nUseful project")


@pytest.mark.django_db
def test_sync_repository_tags_records_and_skips_current_empty_generation_failure(
    monkeypatch,
    settings,
):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        readme="# Django",
    )
    payload = build_repository_tagging_payload(repo, repo.readme)
    assert payload is not None
    calls = 0

    class FakeAgent:
        def run_sync(self, prompt):
            nonlocal calls
            calls += 1
            return SimpleNamespace(output=SimpleNamespace(tags=["!!!", "   "]))

    monkeypatch.setattr("apps.repos.tags._tagging_agent", lambda: FakeAgent())

    tags = sync_repository_tags(repo, repo.readme)
    assert calls == 1
    second = sync_repository_tags(repo, repo.readme)
    assert calls == 1
    third = sync_repository_tags(repo, "# Django\nUpdated docs")
    assert calls == 2

    repo.refresh_from_db()
    assert tags == []
    assert second == []
    assert third == []
    assert repo.generated_tags == []
    assert repo.generated_tags_model == repository_tagging_model_id()
    assert repo.generated_tags_source_hash != payload.text_hash
    assert repo.generated_tags_synced_at is not None
    assert repo.generated_tags_last_error == "Repository tag generation returned no usable tags."


@pytest.mark.django_db(transaction=True)
def test_upsert_repository_from_github_syncs_generated_tags_from_readme(
    monkeypatch,
    settings,
):
    settings.REPOSITORY_TAGGING_ENABLED = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(b"# Django\n").decode("ascii"),
            }
        return github_repo_api_payload()

    def fake_sync_repository_tags(repository, readme_text):
        captured["repo"] = repository.full_name
        captured["description"] = repository.description
        captured["readme_text"] = readme_text
        captured["tag_sync_in_atomic"] = connection.in_atomic_block

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)
    monkeypatch.setattr("apps.repos.services.sync_repository_tags", fake_sync_repository_tags)

    repo = upsert_repository_from_github("django/django")

    assert repo.description == "The Web framework"
    assert captured == {
        "repo": "django/django",
        "description": "The Web framework",
        "readme_text": "# Django\n",
        "tag_sync_in_atomic": False,
    }


@pytest.mark.django_db(transaction=True)
def test_upsert_repository_from_github_syncs_missing_generated_tags_without_readme_refresh(
    monkeypatch,
    settings,
):
    settings.REPOSITORY_TAGGING_ENABLED = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Old description",
        readme="# Existing README\n",
    )
    captured = {}

    def fake_fetch_json(url, **kwargs):
        assert not url.endswith("/readme")
        return github_repo_payload()

    def fake_sync_repository_tags(repository, readme_text):
        captured["repo"] = repository.full_name
        captured["description"] = repository.description
        captured["readme_text"] = readme_text
        captured["tag_sync_in_atomic"] = connection.in_atomic_block

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_tree_items",
        lambda full_name, default_branch, **kwargs: [],
    )
    monkeypatch.setattr("apps.repos.services.sync_repository_tags", fake_sync_repository_tags)

    repo = upsert_repository_from_github("django/django", include_readme=False)

    assert repo.description == "The Web framework for perfectionists with deadlines."
    assert repo.readme == "# Existing README\n"
    assert captured == {
        "repo": "django/django",
        "description": "The Web framework for perfectionists with deadlines.",
        "readme_text": "# Existing README\n",
        "tag_sync_in_atomic": False,
    }


@pytest.mark.django_db
def test_tag_repositories_command_reports_unchanged_tags(monkeypatch, settings):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        readme="# Django",
        generated_tags=["web-framework"],
        generated_tags_model=repository_tagging_model_id(),
        generated_tags_synced_at=timezone.now(),
    )
    payload = build_repository_tagging_payload(repo, repo.readme)
    assert payload is not None
    repo.generated_tags_source_hash = payload.text_hash
    repo.save(update_fields=["generated_tags_source_hash", "updated_at"])

    def fail_generate_repository_tags(text):
        raise AssertionError("unchanged generated tags should not be regenerated")

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        fail_generate_repository_tags,
    )

    stdout = StringIO()
    call_command("tag_repositories", stdout=stdout)

    output = stdout.getvalue()
    assert "'tagged': 0" in output
    assert "'skipped': 0" in output
    assert "'unchanged': 1" in output


@pytest.mark.django_db
def test_tag_repositories_task_backfills_missing_generated_tags(monkeypatch, settings):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        topics=["django", "orm"],
    )

    def fake_generate_repository_tags(text):
        assert "Primary language:\nPython" in text
        assert "GitHub topics:\ndjango, orm" in text
        return ["python", "web-framework", "orm"]

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        fake_generate_repository_tags,
    )

    result = tag_repositories_task(limit=10)

    repo.refresh_from_db()
    assert result["tagged"] == 1
    assert result["failure_count"] == 0
    assert repo.generated_tags == ["python", "web-framework", "orm"]
    assert repo.generated_tags_model == repository_tagging_model_id()


@pytest.mark.django_db
def test_tag_repositories_task_limit_zero_is_noop(monkeypatch, settings):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )

    def fail_generate_repository_tags(text):
        raise AssertionError("limit=0 should not tag scheduled-task repositories")

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        fail_generate_repository_tags,
    )

    result = tag_repositories_task(limit=0)

    repo.refresh_from_db()
    assert result == {
        "tagged": 0,
        "skipped": 0,
        "unchanged": 0,
        "failure_count": 0,
        "failures": [],
    }
    assert repo.generated_tags == []


@pytest.mark.django_db
def test_tag_repositories_command_limit_zero_keeps_no_cap(monkeypatch, settings):
    settings.REPOSITORY_TAGGING_ENABLED = True
    settings.REPOSITORY_TAGGING_PROVIDER = "openai"
    settings.REPOSITORY_TAGGING_MODEL_LABEL = "fast"
    settings.REPOSITORY_TAGGING_MAX_CHARS = 16000
    settings.REPOSITORY_TAGGING_MAX_TAGS = 8
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )
    Repository.objects.create(
        full_name="pallets/flask",
        owner="pallets",
        name="flask",
        url="https://github.com/pallets/flask",
        description="A Python web framework.",
    )

    monkeypatch.setattr(
        "apps.repos.tags.generate_repository_tags",
        lambda text: ["python", "web-framework"],
    )

    stdout = StringIO()
    call_command("tag_repositories", "--limit", "0", stdout=stdout)

    output = stdout.getvalue()
    assert "'tagged': 2" in output
    assert Repository.objects.filter(generated_tags=["python", "web-framework"]).count() == 2


@pytest.mark.django_db
def test_embed_repositories_command_reports_unchanged_embeddings(monkeypatch, settings):
    settings.OPENROUTER_API_KEY = "or-test"
    settings.REPOSITORY_EMBEDDINGS_ENABLED = True
    settings.REPOSITORY_EMBEDDING_MODEL = "openai/text-embedding-3-small"
    settings.REPOSITORY_EMBEDDING_DIMENSIONS = REPOSITORY_EMBEDDING_DIMENSIONS
    settings.REPOSITORY_EMBEDDING_MAX_CHARS = 24000
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )
    readme_text = "# Django"
    payload = build_repository_embedding_payload(repo, readme_text)
    assert payload is not None
    RepositoryEmbedding.objects.create(
        repository=repo,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash=payload.text_hash,
        source_text_chars=len(payload.text),
        embedding=[0.1] * REPOSITORY_EMBEDDING_DIMENSIONS,
        embedded_at=timezone.now(),
    )

    def fail_generate_embedding(text, input_type="document"):
        raise AssertionError("unchanged embeddings should not be regenerated")

    monkeypatch.setattr("apps.repos.embeddings.generate_embedding", fail_generate_embedding)
    monkeypatch.setattr(
        "apps.repos.management.commands.embed_repositories.fetch_repository_readme",
        lambda full_name, **kwargs: readme_text,
    )

    stdout = StringIO()
    call_command("embed_repositories", stdout=stdout)

    output = stdout.getvalue()
    assert "'embedded': 0" in output
    assert "'skipped': 0" in output
    assert "'unchanged': 1" in output


@pytest.mark.django_db
def test_refresh_repository_task_updates_single_repository(monkeypatch):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    refreshed = []

    def fake_sync_from_source(full_name, *, github_access_token=None):
        refreshed.append(full_name)
        return repository

    monkeypatch.setattr(
        "apps.repos.tasks.Repository.sync_from_source",
        staticmethod(fake_sync_from_source),
    )

    result = refresh_repository_task(repository.id, repository.full_name)

    assert refreshed == ["django/django"]
    assert result == {"repository_id": repository.id, "full_name": "django/django"}


@pytest.mark.django_db
def test_refresh_repository_task_resolves_sync_token_index(monkeypatch):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    captured = {}

    def fake_sync_from_source(full_name, *, github_access_token=None):
        captured["full_name"] = full_name
        captured["github_access_token"] = github_access_token
        return repository

    monkeypatch.setattr(
        "apps.repos.tasks.Repository.sync_from_source",
        staticmethod(fake_sync_from_source),
    )
    monkeypatch.setattr(
        "apps.repos.tasks.github_repository_sync_token_for_index",
        lambda index: "user-token",
    )

    result = refresh_repository_task(
        repository.id,
        repository.full_name,
        github_token_index=3,
    )

    assert captured == {
        "full_name": "django/django",
        "github_access_token": "user-token",
    }
    assert result == {"repository_id": repository.id, "full_name": "django/django"}


@pytest.mark.django_db
def test_refresh_repository_task_updates_repository_readme(monkeypatch):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        readme="# Old README\n",
    )
    readme = "# New README\nUpdated project docs.\n"

    def fake_fetch_json(url, **kwargs):
        if url.endswith("/readme"):
            return {
                "encoding": "base64",
                "content": base64.b64encode(readme.encode("utf-8")).decode("ascii"),
                "path": "README.md",
                "download_url": "https://raw.githubusercontent.com/django/django/main/README.md",
            }
        return github_repo_payload(stars=81000, forks=33000, watchers=1300)

    monkeypatch.setattr("apps.repos.services.fetch_json", fake_fetch_json)

    result = refresh_repository_task(repository.id, repository.full_name)

    repository.refresh_from_db()
    assert result == {"repository_id": repository.id, "full_name": "django/django"}
    assert repository.stars == 81000
    assert repository.readme == readme
    assert repository.readme_path == "README.md"
    assert repository.readme_url == (
        "https://raw.githubusercontent.com/django/django/main/README.md"
    )
    assert repository.readme_synced_at == repository.last_synced_at
    assert repository.readme_last_error == ""


@pytest.mark.django_db
def test_refresh_repository_task_logs_and_reraises_failures(monkeypatch):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )

    class DummyLogger:
        def __init__(self):
            self.errors = []

        def info(self, event, **kwargs):
            pass

        def error(self, event, **kwargs):
            self.errors.append((event, kwargs))

    dummy_logger = DummyLogger()

    def fake_sync_from_source(full_name, *, github_access_token=None):
        raise RuntimeError(f"could not refresh {full_name}")

    monkeypatch.setattr("apps.repos.tasks.logger", dummy_logger)
    monkeypatch.setattr(
        "apps.repos.tasks.Repository.sync_from_source",
        staticmethod(fake_sync_from_source),
    )

    with pytest.raises(RuntimeError, match="could not refresh django/django"):
        refresh_repository_task(repository.id, repository.full_name)

    assert dummy_logger.errors == [
        (
            "repository_refresh_task_failed",
            {
                "repository_id": repository.id,
                "repository_full_name": "django/django",
                "error": "could not refresh django/django",
                "exc_info": True,
            },
        )
    ]


def test_daily_repository_refresh_limit_uses_target_days_and_cap(settings):
    settings.GITHUB_REPOSITORY_REFRESH_TARGET_DAYS = 14
    settings.GITHUB_DAILY_REPOSITORY_REFRESH_LIMIT = 1000

    assert daily_repository_refresh_limit(0) == 0
    assert daily_repository_refresh_limit(10) == 1
    assert daily_repository_refresh_limit(10_000) == 715
    assert daily_repository_refresh_limit(30_000) == 1000


@pytest.mark.django_db
def test_refresh_repositories_defaults_to_full_sync(monkeypatch):
    repository = Repository.objects.create(
        full_name="owner/project",
        owner="owner",
        name="project",
        url="https://github.com/owner/project",
    )
    refreshed = []

    def fake_upsert_repository_from_github(
        full_name,
        *,
        include_readme=True,
        active_source_full_names=None,
        github_access_token=None,
    ):
        refreshed.append((full_name, include_readme, active_source_full_names))
        return repository

    monkeypatch.setattr(
        "apps.repos.services.upsert_repository_from_github",
        fake_upsert_repository_from_github,
    )

    result = refresh_repositories()

    assert result == {"synced": 1, "failure_count": 0, "failures": []}
    assert refreshed == [("owner/project", True, set())]


@pytest.mark.django_db
def test_refresh_repositories_assigns_sync_tokens(monkeypatch):
    repositories = [
        Repository.objects.create(
            full_name="owner/one",
            owner="owner",
            name="one",
            url="https://github.com/owner/one",
        ),
        Repository.objects.create(
            full_name="owner/two",
            owner="owner",
            name="two",
            url="https://github.com/owner/two",
        ),
    ]
    refreshed = []

    monkeypatch.setattr(
        "apps.repos.services.github_repository_sync_token_pool",
        lambda: ["primary-token", "user-token"],
    )

    def fake_upsert_repository_from_github(
        full_name,
        *,
        include_readme=True,
        active_source_full_names=None,
        github_access_token=None,
    ):
        refreshed.append(
            (
                full_name,
                include_readme,
                active_source_full_names,
                github_access_token,
            )
        )
        return next(repository for repository in repositories if repository.full_name == full_name)

    monkeypatch.setattr(
        "apps.repos.services.upsert_repository_from_github",
        fake_upsert_repository_from_github,
    )

    result = refresh_repositories(queryset=Repository.objects.order_by("full_name"))

    assert result == {"synced": 2, "failure_count": 0, "failures": []}
    assert refreshed == [
        ("owner/one", True, set(), "primary-token"),
        ("owner/two", True, set(), "user-token"),
    ]


@pytest.mark.django_db
def test_refresh_repositories_task_refreshes_oldest_repositories(monkeypatch):
    stale = Repository.objects.create(
        full_name="owner/stale",
        owner="owner",
        name="stale",
        url="https://github.com/owner/stale",
        last_synced_at=timezone.now() - timedelta(days=7),
    )
    Repository.objects.create(
        full_name="owner/fresh",
        owner="owner",
        name="fresh",
        url="https://github.com/owner/fresh",
        last_synced_at=timezone.now(),
    )
    queued = []

    def fake_async_task(func_path, repository_id, full_name, **kwargs):
        task_id = f"task-{repository_id}"
        queued.append((func_path, repository_id, full_name, kwargs, task_id))
        return task_id

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)
    monkeypatch.setattr("apps.repos.tasks.github_rate_limit_remaining", lambda: None)
    monkeypatch.setattr("apps.repos.tasks.github_rate_limit_status", lambda: {"ok": False})

    result = refresh_repositories_task(limit=1)

    assert queued == [
        (
            "apps.repos.tasks.refresh_repository_task",
            stale.id,
            "owner/stale",
            {"group": "Refresh repositories"},
            f"task-{stale.id}",
        )
    ]
    assert result == {
        "queued": 1,
        "limit": 1,
        "total_repositories": 2,
        "include_readme": True,
        "rate_limit_remaining": None,
        "repositories": [
            {
                "repository_id": stale.id,
                "full_name": "owner/stale",
                "task_id": f"task-{stale.id}",
            },
        ],
    }


@pytest.mark.django_db
def test_refresh_repositories_task_assigns_sync_tokens_and_uses_pool_budget(
    monkeypatch,
    settings,
):
    settings.GITHUB_REPOSITORY_REFRESH_MIN_RATE_LIMIT_REMAINING = 1000
    stale = Repository.objects.create(
        full_name="owner/stale",
        owner="owner",
        name="stale",
        url="https://github.com/owner/stale",
        last_synced_at=timezone.now() - timedelta(days=7),
    )
    fresh = Repository.objects.create(
        full_name="owner/fresh",
        owner="owner",
        name="fresh",
        url="https://github.com/owner/fresh",
        last_synced_at=timezone.now() - timedelta(days=1),
    )
    queued = []
    pool_calls = 0

    monkeypatch.setattr("apps.repos.tasks.github_rate_limit_remaining", lambda: 0)

    def fake_token_pool():
        nonlocal pool_calls
        pool_calls += 1
        return ["primary-token", "user-token"]

    monkeypatch.setattr(
        "apps.repos.tasks.github_repository_sync_token_pool",
        fake_token_pool,
    )

    def fake_async_task(func_path, repository_id, full_name, **kwargs):
        task_id = f"task-{repository_id}"
        queued.append((func_path, repository_id, full_name, kwargs, task_id))
        return task_id

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    result = refresh_repositories_task(limit=2)

    assert queued == [
        (
            "apps.repos.tasks.refresh_repository_task",
            stale.id,
            "owner/stale",
            {"github_token_index": 0, "group": "Refresh repositories"},
            f"task-{stale.id}",
        ),
        (
            "apps.repos.tasks.refresh_repository_task",
            fresh.id,
            "owner/fresh",
            {"github_token_index": 1, "group": "Refresh repositories"},
            f"task-{fresh.id}",
        ),
    ]
    assert result["queued"] == 2
    assert result["limit"] == 2
    assert result["rate_limit_remaining"] == 0
    assert pool_calls == 1


@pytest.mark.django_db
def test_refresh_repositories_task_stops_before_reserved_rate_limit(monkeypatch, settings):
    settings.GITHUB_REPOSITORY_REFRESH_MIN_RATE_LIMIT_REMAINING = 1000
    Repository.objects.create(
        full_name="owner/stale",
        owner="owner",
        name="stale",
        url="https://github.com/owner/stale",
    )

    def fail_async_task(*args, **kwargs):
        raise AssertionError("should not queue repository refresh")

    monkeypatch.setattr("apps.repos.tasks.async_task", fail_async_task)
    monkeypatch.setattr("apps.repos.tasks.github_rate_limit_remaining", lambda: 999)

    result = refresh_repositories_task(limit=1)

    assert result["queued"] == 0
    assert result["limit"] == 0
    assert result["rate_limit_remaining"] == 999


@pytest.mark.django_db
def test_refresh_repository_task_stops_on_rate_limit_error(monkeypatch):
    repository = Repository.objects.create(
        full_name="owner/stale",
        owner="owner",
        name="stale",
        url="https://github.com/owner/stale",
    )

    def fail_sync_from_source(full_name, *, github_access_token=None):
        raise GitHubAPIError(
            "403 Forbidden | rate_limit_remaining=0",
            status_code=403,
            rate_limit_remaining="0",
        )

    monkeypatch.setattr(
        "apps.repos.tasks.Repository.sync_from_source",
        staticmethod(fail_sync_from_source),
    )

    result = refresh_repository_task(repository.id, repository.full_name)

    assert result["stopped_for_rate_limit"] is True
    assert result["repository_id"] == repository.id
    assert result["full_name"] == "owner/stale"


@pytest.mark.django_db
def test_repository_search_filters_and_sorts():
    recent = Repository.objects.create(
        full_name="owner/recent",
        owner="owner",
        name="recent",
        url="https://github.com/owner/recent",
        description="Django tool",
        language="Python",
        license_name="BSD-3-Clause",
        stars=50,
        forks=25,
        commit_count=20,
        first_commit_at=timezone.now() - timedelta(days=500),
        github_pushed_at=timezone.now(),
        uses_ai_for_development=True,
        ai_development_signals=[
            {
                "path": "AGENTS.md",
                "kind": "file",
                "tool": "Agent instructions",
                "signal": "agent_instructions",
            }
        ],
    )
    old = Repository.objects.create(
        full_name="owner/old",
        owner="owner",
        name="old",
        url="https://github.com/owner/old",
        description="Node app",
        language="JavaScript",
        stars=100,
        forks=75,
        commit_count=40,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        github_pushed_at=timezone.now() - timedelta(days=500),
    )
    unsynced = Repository.objects.create(
        full_name="owner/unsynced",
        owner="owner",
        name="unsynced",
        url="https://github.com/owner/unsynced",
        description="No commit count yet",
        stars=75,
        forks=5,
    )
    awesome = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeListItem.objects.create(awesome_list=awesome, repository=recent)

    qs = repository_search_queryset({"q": "django", "updated_days": "30", "sort": "recent"})
    assert list(qs) == [recent]

    qs = repository_search_queryset({"min_stars": "80"})
    assert list(qs) == [old]

    qs = repository_search_queryset({"ai_development": "yes"})
    assert list(qs) == [recent]

    qs = repository_search_queryset({"min_age_years": "10"})
    assert list(qs) == [old]

    qs = repository_search_queryset({"sort": "oldest"})
    assert list(qs) == [old, recent, unsynced]

    qs = repository_search_queryset({"sort": "commits"})
    assert list(qs) == [old, recent, unsynced]

    qs = repository_search_queryset({"sort": "forks"})
    assert list(qs) == [old, recent, unsynced]

    qs = repository_search_queryset(
        {"sort": "stars"},
        extra_sort_map={"stars": ("full_name", "asc")},
    )
    assert list(qs) == [old, unsynced, recent]

    qs = repository_search_queryset({"sort": "least_awesome"})
    assert list(qs) == [old, unsynced, recent]

    qs = repository_search_queryset({"q": "bsd"})
    assert list(qs) == [recent]


@pytest.mark.django_db
def test_awesome_list_repository_queryset_skips_snapshot_metrics():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=75,
        commit_count=80,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repository)
    RepositorySnapshot.objects.create(
        repository=repository,
        captured_at=timezone.now() - timedelta(days=1),
        stars=50,
        commit_count=50,
    )

    search_result = repository_search_queryset({"q": "django"}).get()
    list_result = awesome_list_repository_queryset(awesome_list, {"q": "django"}).get()

    assert search_result.snapshot_count == 1
    assert not hasattr(list_result, "snapshot_count")
    assert "repos_repositorysnapshot" not in str(
        awesome_list_repository_queryset(awesome_list, {"q": "django"}).query
    )


@pytest.mark.django_db
def test_repository_recent_growth_metrics_use_last_7_day_snapshots():
    now = timezone.now()
    growing = Repository.objects.create(
        full_name="owner/growing",
        owner="owner",
        name="growing",
        url="https://github.com/owner/growing",
        stars=115,
        commit_count=230,
    )
    stale = Repository.objects.create(
        full_name="owner/stale",
        owner="owner",
        name="stale",
        url="https://github.com/owner/stale",
        stars=300,
        commit_count=400,
    )
    RepositorySnapshot.objects.create(
        repository=growing,
        captured_at=now - timedelta(days=12),
        stars=20,
        commit_count=20,
    )
    RepositorySnapshot.objects.create(
        repository=growing,
        captured_at=now - timedelta(days=6),
        stars=100,
        commit_count=200,
    )
    RepositorySnapshot.objects.create(
        repository=stale,
        captured_at=now - timedelta(days=8),
        stars=250,
        commit_count=300,
    )

    repositories = {
        repo.full_name: repo
        for repo in annotate_repository_recent_growth_metrics(Repository.objects.all())
    }

    assert repositories["owner/growing"].stars_growth_7d == 15
    assert repositories["owner/growing"].commits_growth_7d == 30
    assert repositories["owner/growing"].stars_growth_7d_percent == pytest.approx(15)
    assert repositories["owner/growing"].commits_growth_7d_percent == pytest.approx(15)
    assert repositories["owner/stale"].stars_growth_7d is None
    assert repositories["owner/stale"].commits_growth_7d is None


@pytest.mark.django_db
def test_search_page_skips_full_snapshot_metrics_for_default_queryset(client, monkeypatch):
    from apps.repos import views as repo_views

    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=75,
    )
    original_repository_search_queryset = repo_views.repository_search_queryset
    calls = []

    def capture_repository_search_queryset(params, *args, **kwargs):
        calls.append(kwargs.get("include_snapshot_metrics"))
        return original_repository_search_queryset(params, *args, **kwargs)

    monkeypatch.setattr(
        repo_views,
        "repository_search_queryset",
        capture_repository_search_queryset,
    )

    response = client.get(reverse("repos:search"))

    assert response.status_code == 200
    assert calls == [False]


@pytest.mark.django_db
@override_settings(CACHES=LOC_MEM_CACHES)
def test_public_repository_filter_options_are_cached():
    cache.clear()
    try:
        active_list = AwesomeList.objects.create(
            name="Awesome Django",
            slug="awesome-django",
            source_url="https://github.com/wsvincent/awesome-django",
        )
        repo = Repository.objects.create(
            full_name="django/django",
            owner="django",
            name="django",
            url="https://github.com/django/django",
            description="The Web framework",
            language="Python",
            topics=["django"],
            generated_tags=["web-framework"],
            detected_stacks=["django"],
            package_managers=["pip"],
            stars=75,
        )
        AwesomeListItem.objects.create(awesome_list=active_list, repository=repo)

        first_options = public_repository_filter_options()

        assert first_options["languages"] == ["Python"]
        assert first_options["awesome_lists"][0].repo_count == 1
        assert first_options["topic_options"] == [{"name": "django", "count": 1}]

        with CaptureQueriesContext(connection) as queries:
            cached_options = public_repository_filter_options()

        assert len(queries) == 0
        assert cached_options["languages"] == ["Python"]
        assert [awesome_list.id for awesome_list in cached_options["awesome_lists"]] == [
            active_list.id
        ]
    finally:
        cache.clear()


@pytest.mark.django_db
@override_settings(CACHES=LOC_MEM_CACHES)
@pytest.mark.parametrize("model_name", ["repository", "awesome_list", "awesome_list_item"])
def test_public_repository_filter_options_cache_invalidates_on_catalog_changes(model_name):
    cache.clear()
    try:
        if model_name == "awesome_list_item":
            active_list = AwesomeList.objects.create(
                name="Awesome Django",
                slug="awesome-django",
                source_url="https://github.com/wsvincent/awesome-django",
            )
            repo = Repository.objects.create(
                full_name="django/django",
                owner="django",
                name="django",
                url="https://github.com/django/django",
                description="The Web framework",
                language="Python",
                stars=75,
            )

        public_repository_filter_options()
        with CaptureQueriesContext(connection) as queries:
            public_repository_filter_options()

        assert len(queries) == 0

        if model_name == "repository":
            Repository.objects.create(
                full_name="django/django",
                owner="django",
                name="django",
                url="https://github.com/django/django",
                description="The Web framework",
                language="Python",
                stars=75,
            )
        elif model_name == "awesome_list":
            AwesomeList.objects.create(
                name="Awesome Django",
                slug="awesome-django",
                source_url="https://github.com/wsvincent/awesome-django",
            )
        else:
            AwesomeListItem.objects.create(awesome_list=active_list, repository=repo)

        with CaptureQueriesContext(connection) as queries:
            updated_options = public_repository_filter_options()

        assert len(queries) > 0
        if model_name == "repository":
            assert updated_options["languages"] == ["Python"]
            assert updated_options["total_repositories"] == 1
        elif model_name == "awesome_list":
            assert updated_options["total_lists"] == 1
            assert [awesome_list.name for awesome_list in updated_options["awesome_lists"]] == [
                "Awesome Django"
            ]
        else:
            assert updated_options["awesome_lists"][0].repo_count == 1
    finally:
        cache.clear()


@pytest.mark.django_db
def test_repository_search_filters_growth_unmaintained_and_sort_direction():
    now = timezone.now()
    awesome_list = AwesomeList.objects.create(
        name="Awesome Growth",
        slug="awesome-growth",
        source_url="https://github.com/example/awesome-growth",
    )
    fast = Repository.objects.create(
        full_name="owner/fast",
        owner="owner",
        name="fast",
        url="https://github.com/owner/fast",
        stars=150,
        commit_count=150,
        github_pushed_at=now - timedelta(days=400),
    )
    slow = Repository.objects.create(
        full_name="owner/slow",
        owner="owner",
        name="slow",
        url="https://github.com/owner/slow",
        stars=120,
        commit_count=105,
        github_pushed_at=now - timedelta(days=20),
    )
    unknown_baseline = Repository.objects.create(
        full_name="owner/unknown-baseline",
        owner="owner",
        name="unknown-baseline",
        url="https://github.com/owner/unknown-baseline",
        stars=200,
        commit_count=200,
        github_pushed_at=now - timedelta(days=800),
    )
    for repository in (fast, slow, unknown_baseline):
        AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repository)
    RepositorySnapshot.objects.create(
        repository=fast,
        captured_at=now - timedelta(days=30),
        stars=100,
        commit_count=100,
    )
    RepositorySnapshot.objects.create(
        repository=slow,
        captured_at=now - timedelta(days=30),
        stars=100,
        commit_count=100,
    )
    RepositorySnapshot.objects.create(
        repository=unknown_baseline,
        captured_at=now - timedelta(days=30),
        stars=0,
        commit_count=0,
    )

    assert list(repository_search_queryset({"min_velocity_percent": "40"})) == [fast]
    assert list(repository_search_queryset({"min_star_growth_percent": "30"})) == [fast]
    assert list(repository_search_queryset({"min_liability_percent": "30"})) == [fast]
    assert list(repository_search_queryset({"unmaintained_days": "365"})) == [
        unknown_baseline,
        fast,
    ]
    assert list(repository_search_queryset({"updated_days": "30", "unmaintained_days": "365"})) == [
        unknown_baseline,
        fast,
    ]

    repos = list(repository_search_queryset({"sort": "velocity"}))
    assert repos == [fast, slow, unknown_baseline]
    assert repos[0].commits_growth_percent == 50
    assert repos[0].stars_growth_percent == 50
    assert repos[2].commits_growth_percent is None
    assert repos[2].stars_growth_percent is None
    assert list(repository_search_queryset({"sort": "star_growth"})) == [
        fast,
        slow,
        unknown_baseline,
    ]
    assert list(repository_search_queryset({"sort": "liability"})) == [
        fast,
        slow,
        unknown_baseline,
    ]

    assert list(repository_search_queryset({"sort": "stars", "sort_direction": "asc"})) == [
        slow,
        fast,
        unknown_baseline,
    ]
    assert list(repository_search_queryset({"sort": "stars", "direction": "asc"})) == [
        unknown_baseline,
        fast,
        slow,
    ]

    list_repos = list(
        awesome_list_repository_queryset(awesome_list, {"min_velocity_percent": "40"})
    )
    assert list_repos == [fast]
    assert list_repos[0].commits_growth_percent == 50


@pytest.mark.django_db
def test_repository_search_filters_by_topic_and_generated_tag():
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Django tool",
        topics=["django", "python", "web"],
        generated_tags=["web-framework", "orm"],
        detected_stacks=["django"],
        package_managers=["poetry"],
        stars=50,
    )
    node_repo = Repository.objects.create(
        full_name="nodejs/node",
        owner="nodejs",
        name="node",
        url="https://github.com/nodejs/node",
        description="JavaScript runtime",
        topics=["javascript", "runtime"],
        generated_tags=["server-runtime"],
        detected_stacks=["express"],
        package_managers=["npm"],
        stars=100,
    )

    assert list(repository_search_queryset({"topic": "django"})) == [django_repo]
    assert list(repository_search_queryset({"generated_tag": "server runtime"})) == [node_repo]
    assert list(repository_search_queryset({"stack": "django"})) == [django_repo]
    assert list(repository_search_queryset({"framework": "express"})) == [node_repo]
    assert list(repository_search_queryset({"package_manager": "npm"})) == [node_repo]
    assert list(repository_search_queryset({"q": "orm"})) == [django_repo]
    assert list(repository_search_queryset({"q": "express"})) == [node_repo]


@pytest.mark.django_db
def test_repository_search_hides_awesome_list_candidates_and_tracked_sources():
    visible = Repository.objects.create(
        full_name="owner/normal",
        owner="owner",
        name="normal",
        url="https://github.com/owner/normal",
        stars=10,
    )
    candidate = Repository.objects.create(
        full_name="owner/awesome-tools",
        owner="owner",
        name="awesome-tools",
        url="https://github.com/owner/awesome-tools",
        is_awesome_list_candidate=True,
        stars=100,
    )
    tracked_source = Repository.objects.create(
        full_name="vinta/awesome-python",
        owner="vinta",
        name="awesome-python",
        url="https://github.com/vinta/awesome-python",
        stars=90,
    )
    inactive_source = Repository.objects.create(
        full_name="old/awesome-list",
        owner="old",
        name="awesome-list",
        url="https://github.com/old/awesome-list",
        stars=80,
    )
    AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python",
        source_url="https://github.com/vinta/awesome-python",
        repo_full_name=tracked_source.full_name,
    )
    AwesomeList.objects.create(
        name="Inactive Awesome List",
        slug="inactive-awesome-list",
        source_url="https://github.com/old/awesome-list",
        repo_full_name=inactive_source.full_name,
        is_active=False,
    )

    repos = list(repository_search_queryset({"sort": "name"}))

    assert visible in repos
    assert inactive_source in repos
    assert candidate not in repos
    assert tracked_source not in repos


@pytest.mark.django_db
def test_repository_json_value_counts_aggregates_server_side():
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        topics=["django", "python", "web"],
        generated_tags=["web-framework", "orm"],
        detected_stacks=["django"],
        package_managers=["poetry"],
    )
    Repository.objects.create(
        full_name="django/channels",
        owner="django",
        name="channels",
        url="https://github.com/django/channels",
        topics=["django", "python", "async"],
        generated_tags=["web-framework", "websocket"],
        detected_stacks=["django"],
        package_managers=["pip"],
    )

    assert repository_json_value_counts("topics")[:2] == [
        {"name": "django", "count": 2},
        {"name": "python", "count": 2},
    ]
    assert repository_json_value_counts("generated_tags", limit=1) == [
        {"name": "web-framework", "count": 2}
    ]
    assert repository_json_value_counts("detected_stacks", limit=1) == [
        {"name": "django", "count": 2}
    ]


@pytest.mark.django_db
def test_repository_json_value_counts_can_scope_to_awesome_list():
    django_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    node_list = AwesomeList.objects.create(
        name="Awesome Node",
        slug="awesome-node",
        source_url="https://github.com/sindresorhus/awesome-nodejs",
    )
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        topics=["django", "python"],
    )
    node_repo = Repository.objects.create(
        full_name="nodejs/node",
        owner="nodejs",
        name="node",
        url="https://github.com/nodejs/node",
        topics=["javascript", "runtime"],
    )
    AwesomeListItem.objects.create(awesome_list=django_list, repository=django_repo)
    AwesomeListItem.objects.create(awesome_list=node_list, repository=node_repo)

    counts = repository_json_value_counts("topics", awesome_list=django_list)

    assert counts == [
        {"name": "django", "count": 1},
        {"name": "python", "count": 1},
    ]


def test_repository_json_value_counts_rejects_unknown_fields():
    with pytest.raises(ValueError, match="Unsupported repository JSON filter field"):
        repository_json_value_counts("readme")


@pytest.mark.django_db
def test_awesome_list_directory_totals_aggregates_in_one_query():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        stars=1200,
        readme_repository_count=42,
        last_scanned_at=timezone.now(),
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=80000,
    )
    candidate = Repository.objects.create(
        full_name="vinta/awesome-python",
        owner="vinta",
        name="awesome-python",
        url="https://github.com/vinta/awesome-python",
        description="Curated Python resources",
        stars=250000,
        is_awesome_list_candidate=True,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=candidate)
    inactive_list = AwesomeList.objects.create(
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        stars=9999,
        readme_repository_count=500,
        is_active=False,
    )
    inactive_repo = Repository.objects.create(
        full_name="example/inactive",
        owner="example",
        name="inactive",
        url="https://github.com/example/inactive",
        stars=1,
    )
    AwesomeListItem.objects.create(awesome_list=inactive_list, repository=inactive_repo)

    with CaptureQueriesContext(connection) as queries:
        totals = awesome_list_directory_totals()

    assert len(queries) == 1
    assert totals["total_lists"] == 1
    assert totals["total_readme_repositories"] == 42
    assert totals["total_list_stars"] == 1200
    assert totals["total_indexed_links"] == 1
    assert totals["latest_scan"] is not None


@pytest.mark.django_db
def test_repository_search_semantic_mode_orders_by_vector(monkeypatch, settings):
    settings.OPENROUTER_API_KEY = "or-test"
    settings.REPOSITORY_EMBEDDINGS_ENABLED = True
    settings.REPOSITORY_EMBEDDING_DIMENSIONS = REPOSITORY_EMBEDDING_DIMENSIONS
    near = Repository.objects.create(
        full_name="owner/near",
        owner="owner",
        name="near",
        url="https://github.com/owner/near",
        description="Python web framework",
        language="Python",
        stars=10,
    )
    far = Repository.objects.create(
        full_name="owner/far",
        owner="owner",
        name="far",
        url="https://github.com/owner/far",
        description="Terminal theme",
        language="JavaScript",
        stars=100,
    )
    stale_model = Repository.objects.create(
        full_name="owner/stale-model",
        owner="owner",
        name="stale-model",
        url="https://github.com/owner/stale-model",
        description="Old embedding model",
        stars=1000,
    )
    RepositoryEmbedding.objects.create(
        repository=near,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="a" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )
    RepositoryEmbedding.objects.create(
        repository=far,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="b" * 64,
        source_text_chars=10,
        embedding=[0.0, 1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 2),
        embedded_at=timezone.now(),
    )
    RepositoryEmbedding.objects.create(
        repository=stale_model,
        model="older-embedding-model",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="c" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )

    def fake_generate_embedding(text, input_type="query"):
        from apps.repos.embeddings import EmbeddingResponse

        assert text == "web framework"
        assert input_type == "query"
        return EmbeddingResponse(
            vector=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
            model="openai/text-embedding-3-small",
        )

    monkeypatch.setattr("apps.repos.services.generate_embedding", fake_generate_embedding)

    qs = repository_search_queryset({"q": "web framework", "mode": "semantic"})

    assert list(qs) == [near, far]

    qs = repository_search_queryset(
        {"q": "web framework", "mode": "semantic", "language": "Python"}
    )

    assert list(qs) == [near]


@pytest.mark.django_db
def test_similar_repositories_for_repository_orders_by_vector(settings):
    settings.REPOSITORY_EMBEDDING_MODEL = "openai/text-embedding-3-small"
    source = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Python web framework",
        stars=100,
    )
    near = Repository.objects.create(
        full_name="encode/django-rest-framework",
        owner="encode",
        name="django-rest-framework",
        url="https://github.com/encode/django-rest-framework",
        description="API toolkit for Django",
        stars=80,
    )
    far = Repository.objects.create(
        full_name="owner/theme",
        owner="owner",
        name="theme",
        url="https://github.com/owner/theme",
        description="Terminal theme",
        stars=1000,
    )
    stale_model = Repository.objects.create(
        full_name="owner/stale-model",
        owner="owner",
        name="stale-model",
        url="https://github.com/owner/stale-model",
        description="Old embedding model",
        stars=2000,
    )
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=near)
    RepositoryEmbedding.objects.create(
        repository=source,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="s" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )
    RepositoryEmbedding.objects.create(
        repository=near,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="n" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )
    RepositoryEmbedding.objects.create(
        repository=far,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="f" * 64,
        source_text_chars=10,
        embedding=[0.0, 1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 2),
        embedded_at=timezone.now(),
    )
    RepositoryEmbedding.objects.create(
        repository=stale_model,
        model="older-embedding-model",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="o" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )

    with CaptureQueriesContext(connection) as queries:
        assert list(similar_repositories_for_repository(source)) == [near, far]

    assert len(queries) == 2
    assert list(similar_repositories_for_repository(source, limit=1)) == [near]


@pytest.mark.django_db
def test_repository_search_queryset_annotates_tracked_growth():
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Django tool",
        language="Python",
        stars=75,
        commit_count=80,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=50,
        commit_count=50,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=75,
        commit_count=80,
    )

    result = repository_search_queryset({"q": "django"}).get()

    assert result.snapshot_count == 2
    assert result.first_snapshot_stars == 50
    assert result.first_snapshot_commit_count == 50
    assert result.stars_since_first == 25
    assert result.commits_since_first == 30


@pytest.mark.django_db
def test_repository_performance_summary_returns_recent_growth():
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=75,
        forks=12,
        watchers=5,
        commit_count=90,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=50,
        forks=10,
        watchers=4,
        commit_count=70,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=75,
        forks=12,
        watchers=5,
        commit_count=90,
    )

    summary = repository_performance_summary(repo)

    assert summary["snapshot_count"] == 2
    assert summary["stars_since_first"] == 25
    assert summary["stars_since_first_label"] == "+25"
    assert summary["forks_since_first"] == 2
    assert summary["watchers_since_first"] == 1
    assert summary["commits_since_first"] == 20
    assert summary["commits_since_first_label"] == "+20"
    assert summary["history"][0]["stars_delta"] == 25
    assert summary["history"][0]["commit_delta"] == 20
    assert summary["history"][1]["stars_delta_label"] == "baseline"
    assert summary["history"][1]["commit_delta_label"] == "baseline"


@pytest.mark.django_db
def test_repository_performance_summary_reuses_recent_snapshots_for_short_history():
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=75,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=75,
    )

    with CaptureQueriesContext(connection) as queries:
        summary = repository_performance_summary(repo)

    assert len(queries) == 1
    assert summary["snapshot_count"] == 1
    assert summary["first_snapshot"] == summary["latest_snapshot"]


@pytest.mark.django_db
def test_repository_history_chart_data_limits_latest_snapshots_chronologically():
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    now = timezone.now()
    for index in range(5):
        RepositorySnapshot.objects.create(
            repository=repo,
            captured_at=now - timedelta(days=5 - index),
            stars=100 + index,
            commit_count=200 + index,
        )

    chart_data = repository_history_chart_data(repo, limit=3)

    assert [point["stars"] for point in chart_data] == [102, 103, 104]
    assert [point["commit_count"] for point in chart_data] == [202, 203, 204]


@pytest.mark.django_db
def test_awesome_list_history_chart_data_uses_list_snapshots_chronologically():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )
    now = timezone.now()
    for index in range(5):
        AwesomeListSnapshot.objects.create(
            awesome_list=awesome_list,
            captured_at=now - timedelta(days=5 - index),
            stars=100 + index,
            commits_count=200 + index,
        )

    chart_data = awesome_list_history_chart_data(awesome_list, limit=3)

    assert [point["stars"] for point in chart_data] == [102, 103, 104]
    assert [point["commit_count"] for point in chart_data] == [202, 203, 204]


@pytest.mark.django_db
def test_awesome_list_snapshot_string_uses_cached_list_identifier_without_fk_query():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )
    snapshot = AwesomeListSnapshot.objects.create(
        awesome_list=awesome_list,
        repo_full_name="wsvincent/awesome-django",
        stars=100,
    )
    deferred_snapshot = AwesomeListSnapshot.objects.only(
        "repo_full_name",
        "captured_at",
        "awesome_list_id",
    ).get(id=snapshot.id)

    with CaptureQueriesContext(connection) as queries:
        label = str(deferred_snapshot)

    assert len(queries) == 0
    assert label.startswith("wsvincent/awesome-django at ")


@pytest.mark.django_db
def test_awesome_list_history_chart_data_falls_back_to_current_list_metadata():
    scanned_at = timezone.now()
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        stars=1200,
        commits_count=350,
        last_scanned_at=scanned_at,
    )

    chart_data = awesome_list_history_chart_data(awesome_list)

    assert chart_data == [
        {
            "captured_at": scanned_at.isoformat(),
            "stars": 1200,
            "commit_count": 350,
        }
    ]


@pytest.mark.django_db
def test_awesome_list_history_chart_data_skips_unscanned_empty_lists():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )

    assert awesome_list_history_chart_data(awesome_list) == []


@pytest.mark.django_db
def test_awesome_list_history_chart_data_skips_zero_star_lists_without_commits():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        stars=0,
        commits_count=None,
        last_scanned_at=timezone.now(),
    )

    assert awesome_list_history_chart_data(awesome_list) == []


@pytest.mark.django_db
def test_awesome_list_repository_history_chart_data_aggregates_list_snapshots(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    second_repo = Repository.objects.create(
        full_name="django/channels",
        owner="django",
        name="channels",
        url="https://github.com/django/channels",
    )
    outside_repo = Repository.objects.create(
        full_name="pallets/flask",
        owner="pallets",
        name="flask",
        url="https://github.com/pallets/flask",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=second_repo)
    now = timezone.now().replace(hour=12, minute=0, second=0, microsecond=0)

    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=now - timedelta(days=3),
        stars=10,
        commit_count=100,
    )
    RepositorySnapshot.objects.create(
        repository=second_repo,
        captured_at=now - timedelta(days=3, minutes=10),
        stars=5,
        commit_count=None,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=now - timedelta(days=2),
        stars=12,
        commit_count=120,
    )
    RepositorySnapshot.objects.create(
        repository=second_repo,
        captured_at=now - timedelta(days=1),
        stars=8,
        commit_count=80,
    )
    RepositorySnapshot.objects.create(
        repository=outside_repo,
        captured_at=now - timedelta(days=1),
        stars=1000,
        commit_count=1000,
    )

    chart_data = awesome_list_repository_history_chart_data(awesome_list)

    assert [point["stars"] for point in chart_data] == [15, 17, 20]
    assert [point["commit_count"] for point in chart_data] == [100, 120, 200]
    monkeypatch.setattr("apps.repos.services.timezone.now", lambda: now + timedelta(hours=6))
    windowed_chart_data = awesome_list_repository_history_chart_data(awesome_list, limit=3)
    assert [point["stars"] for point in windowed_chart_data] == [17, 20]


@pytest.mark.django_db
def test_awesome_list_repository_history_chart_data_seeds_from_before_window():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    second_repo = Repository.objects.create(
        full_name="django/channels",
        owner="django",
        name="channels",
        url="https://github.com/django/channels",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=second_repo)
    now = timezone.now()

    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=now - timedelta(days=30),
        stars=10,
        commit_count=100,
    )
    RepositorySnapshot.objects.create(
        repository=second_repo,
        captured_at=now - timedelta(days=30),
        stars=5,
        commit_count=50,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=now - timedelta(days=1),
        stars=12,
        commit_count=120,
    )

    chart_data = awesome_list_repository_history_chart_data(awesome_list, limit=7)

    assert [point["stars"] for point in chart_data] == [17]
    assert [point["commit_count"] for point in chart_data] == [170]


@pytest.mark.django_db
def test_search_page_renders(client):
    active_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeList.objects.create(
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        is_active=False,
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        topics=["django", "python"],
        generated_tags=["web-framework"],
        stars=80000,
        commit_count=90100,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=6),
        stars=79000,
        commit_count=90000,
    )
    hidden_repo = Repository.objects.create(
        full_name="wsvincent/awesome-django",
        owner="wsvincent",
        name="awesome-django",
        url="https://github.com/wsvincent/awesome-django",
        is_awesome_list_candidate=True,
    )
    AwesomeListItem.objects.create(awesome_list=active_list, repository=repo)
    AwesomeListItem.objects.create(awesome_list=active_list, repository=hidden_repo)
    response = client.get(reverse("repos:search"), {"q": "framework"})
    content = response.content
    assert response.status_code == 200
    assert b"django/django" in content
    assert b"Star growth, last 7 days" in content
    assert b"+1,000" in content
    assert b"+1.3%" in content
    assert b"Commit velocity, last 7 days" in content
    assert b"+100" in content
    assert b"+0.1%" in content
    assert b"Find repositories" in content
    assert b"Tune results" in content
    assert b"More filters" in content
    assert b"Any GitHub topic" in content
    assert b"Any detected framework" in content
    assert b"Commit velocity" in content
    assert b"What does Commit velocity mean?" in content
    assert b"commit-count growth since Awesome first tracked the repository" in content
    assert b"What does Star growth mean?" in content
    assert b"GitHub star growth since Awesome first tracked the repository" in content
    assert b"Direction" in content
    assert_option_label_with_count(content, "django", 1)
    assert b'href="/?topic=django"' in content
    assert_option_label_with_count(content, "web-framework", 1)
    assert b"data-page-ad-shell" in content
    assert b"data-page-content" in content
    assert b"max-w-none" in content
    assert b"xl:col-start-3" in content
    assert b"xl:col-start-5" in content
    assert b'data-ad-rail="left"' in content
    assert b'data-ad-rail="right"' in content
    assert b"grid-rows-5" in content
    assert content.count(b'data-ad-slot="global-left-') == 5
    assert content.count(b'data-ad-slot="global-right-') == 5
    assert content.count(b"data-ad-slot=") == 10
    assert content.count(b"data-ad-empty-slot=") == 1
    assert b'data-ad-empty-slot="global-right-5"' in content
    assert b"Get sponsored" in content
    assert content.count(b"utm_source=awesome_repos") == 9
    assert content.count(b"utm_medium=side_ad") == 9
    assert b"data-sponsor-modal-open" in content
    assert b'action="/sponsor/checkout/"' in content
    assert response.context["total_lists"] == 1
    assert [awesome_list.id for awesome_list in response.context["awesome_lists"]] == [
        active_list.id
    ]
    assert response.context["awesome_lists"][0].repo_count == 1
    assert_option_label_with_count(content, "Awesome Django", 1)
    assert b"Awesome Django (2)" not in content
    assert b"Inactive List" not in content


@pytest.mark.django_db
def test_authenticated_user_can_toggle_repository_like_with_htmx(auth_client, user):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    url = reverse("repos:repo_like_toggle", kwargs={"owner": repo.owner, "name": repo.name})

    response = auth_client.post(url, {"next": "/"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert RepositoryLike.objects.filter(user=user, repository=repo).exists()
    assert b'aria-pressed="true"' in response.content
    assert b'fill="currentColor"' in response.content

    response = auth_client.post(url, {"next": "/"}, HTTP_HX_REQUEST="true")

    assert response.status_code == 200
    assert not RepositoryLike.objects.filter(user=user, repository=repo).exists()
    assert b'aria-pressed="false"' in response.content
    assert b'fill="none"' in response.content


@pytest.mark.django_db
def test_repository_search_tracks_first_page_only(auth_client, user, monkeypatch):
    for index in range(31):
        Repository.objects.create(
            full_name=f"example/repo-{index}",
            owner="example",
            name=f"repo-{index}",
            url=f"https://github.com/example/repo-{index}",
            description="A framework for testing.",
        )
    events = []
    monkeypatch.setattr(
        "apps.repos.views.queue_track_event", lambda **kwargs: events.append(kwargs)
    )

    response = auth_client.get(reverse("repos:search"), {"q": "framework"})

    assert response.status_code == 200
    assert events == [
        {
            "event_name": "search_performed",
            "profile_id": user.profile.id,
            "properties": {
                "query": "framework",
                "mode": "",
                "results_count": 31,
                "sort": "",
                "sort_direction": "",
                "search_scope": "public_repositories",
                "filters_applied": True,
            },
            "source_function": "RepositorySearchView",
        }
    ]

    events.clear()
    response = auth_client.get(reverse("repos:search"), {"q": "framework", "page": "2"})

    assert response.status_code == 200
    assert events == []


@pytest.mark.django_db
def test_repository_like_queues_analytics_event(auth_client, user, monkeypatch):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        language="Python",
        stars=78000,
    )
    events = []
    monkeypatch.setattr(
        "apps.repos.views.queue_track_event", lambda **kwargs: events.append(kwargs)
    )

    response = auth_client.post(
        reverse("repos:repo_like_toggle", kwargs={"owner": repo.owner, "name": repo.name}),
        {"next": "/"},
    )

    assert response.status_code == 302
    assert events == [
        {
            "event_name": "repository_liked",
            "profile_id": user.profile.id,
            "properties": {
                "repository_id": repo.id,
                "repository_full_name": "django/django",
                "repository_language": "Python",
                "repository_stars": 78000,
            },
            "source_function": "toggle_repository_like",
        }
    ]

    events.clear()

    unlike_response = auth_client.post(
        reverse("repos:repo_like_toggle", kwargs={"owner": repo.owner, "name": repo.name}),
        {"next": "/"},
    )

    assert unlike_response.status_code == 302
    assert events == [
        {
            "event_name": "repository_unliked",
            "profile_id": user.profile.id,
            "properties": {
                "repository_id": repo.id,
                "repository_full_name": "django/django",
                "repository_language": "Python",
                "repository_stars": 78000,
            },
            "source_function": "toggle_repository_like",
        }
    ]


@pytest.mark.django_db
def test_repository_like_htmx_response_uses_safe_next_url(auth_client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )

    response = auth_client.post(
        reverse("repos:repo_like_toggle", kwargs={"owner": repo.owner, "name": repo.name}),
        {"next": "https://example.invalid/not-local"},
        HTTP_HX_REQUEST="true",
    )

    assert response.status_code == 200
    assert f'value="{repo.get_absolute_url()}"'.encode() in response.content
    assert b"https://example.invalid/not-local" not in response.content


@pytest.mark.django_db
def test_repository_like_falls_back_to_safe_redirect(auth_client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )

    response = auth_client.post(
        reverse("repos:repo_like_toggle", kwargs={"owner": repo.owner, "name": repo.name}),
        {"next": "https://example.invalid/not-local"},
    )

    assert response.status_code == 302
    assert response["Location"] == repo.get_absolute_url()


@pytest.mark.django_db
def test_liked_repositories_page_requires_login(client):
    response = client.get(reverse("repos:liked"))

    assert response.status_code == 302
    assert response["Location"].startswith(f"{reverse('account_login')}?next=")


@pytest.mark.django_db
def test_liked_repositories_page_lists_current_users_likes(auth_client, user, django_user_model):
    other_user = django_user_model.objects.create_user(
        username="other",
        email="other@example.com",
        password="password123",
    )
    liked_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        homepage_url="https://www.djangoproject.com/",
        language="Python",
        stars=80000,
        is_awesome_list_candidate=True,
    )
    other_repo = Repository.objects.create(
        full_name="pallets/flask",
        owner="pallets",
        name="flask",
        url="https://github.com/pallets/flask",
        description="A lightweight WSGI framework",
        language="Python",
        stars=70000,
    )
    RepositoryLike.objects.create(user=user, repository=liked_repo)
    RepositoryLike.objects.create(user=other_user, repository=other_repo)

    response = auth_client.get(reverse("repos:liked"))
    content = response.content

    assert response.status_code == 200
    assert b"Liked repositories" in content
    assert b"django/django" in content
    assert b'href="https://www.djangoproject.com/"' in content
    assert b"Website" in content
    assert b"pallets/flask" not in content
    assert b"Remove django/django from liked repositories" in content
    assert response.context["liked_repository_count"] == 1
    assert response.context["page_obj"].paginator.count == 1


@pytest.mark.django_db
def test_repository_pages_render_homepage_links(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        homepage_url="https://www.djangoproject.com/",
        description="The Web framework",
        stars=80000,
    )
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)

    response = client.get(reverse("repos:search"))

    assert response.status_code == 200
    assert b'href="https://www.djangoproject.com/"' in response.content
    assert b"Website" in response.content

    response = client.get(reverse("repos:list_detail", kwargs={"slug": awesome_list.slug}))

    assert response.status_code == 200
    assert b'href="https://www.djangoproject.com/"' in response.content
    assert b"Website" in response.content

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    assert b'href="https://www.djangoproject.com/"' in response.content
    assert b"Open website" in response.content


@pytest.mark.django_db
def test_repository_pages_render_liked_heart_for_authenticated_user(auth_client, user):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )
    RepositoryLike.objects.create(user=user, repository=repo)
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)

    response = auth_client.get(reverse("repos:search"))

    assert response.status_code == 200
    assert b"Remove django/django from liked repositories" in response.content
    assert b'aria-pressed="true"' in response.content

    response = auth_client.get(reverse("repos:list_detail", kwargs={"slug": awesome_list.slug}))

    assert response.status_code == 200
    assert b"Remove django/django from liked repositories" in response.content
    assert b'aria-pressed="true"' in response.content

    response = auth_client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    assert b"Remove django/django from liked repositories" in response.content
    assert b'aria-pressed="true"' in response.content


@pytest.mark.django_db
def test_repository_pages_render_starred_badge_for_authenticated_user(
    auth_client,
    user,
    profile,
    django_user_model,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        stars=80000,
    )
    other_repo = Repository.objects.create(
        full_name="pallets/flask",
        owner="pallets",
        name="flask",
        url="https://github.com/pallets/flask",
        description="A lightweight WSGI framework",
        stars=70000,
    )
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=other_repo)
    other_user = django_user_model.objects.create_user(
        username="other-starred",
        email="other-starred@example.com",
        password="password123",
    )
    RepositoryLike.objects.create(user=user, repository=repo)
    UserStarredRepository.objects.create(
        profile=profile,
        repository=repo,
        starred_at=datetime(2026, 5, 1, tzinfo=UTC),
    )
    UserStarredRepository.objects.create(profile=other_user.profile, repository=other_repo)

    response = auth_client.get(reverse("repos:search"))

    assert response.status_code == 200
    assert b"Starred django/django on GitHub" in response.content
    assert b"Starred pallets/flask on GitHub" not in response.content

    response = auth_client.get(reverse("repos:starred"))

    assert response.status_code == 200
    assert b"Starred django/django on GitHub on 2026-05-01" in response.content
    assert b"pallets/flask" not in response.content

    response = auth_client.get(reverse("repos:list_detail", kwargs={"slug": awesome_list.slug}))

    assert response.status_code == 200
    assert b"Starred django/django on GitHub" in response.content
    assert b"Starred pallets/flask on GitHub" not in response.content

    response = auth_client.get(reverse("repos:liked"))

    assert response.status_code == 200
    assert b"Starred django/django on GitHub" in response.content

    response = auth_client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    assert b"Starred django/django on GitHub" in response.content

    response = auth_client.get(
        reverse(
            "repos:repo_detail",
            kwargs={"owner": other_repo.owner, "name": other_repo.name},
        )
    )

    assert response.status_code == 200
    assert b"Starred pallets/flask on GitHub" not in response.content


@pytest.mark.django_db
def test_repository_pages_hide_starred_badge_when_authenticated_user_has_no_profile(
    auth_client,
    user,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
    )
    user.profile.delete()

    response = auth_client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    assert b"django/django" in response.content
    assert b"Starred django/django on GitHub" not in response.content


@pytest.mark.django_db
def test_repository_search_is_root_page(client):
    response = client.get(reverse("repos:search"))

    assert reverse("repos:search") == "/"
    assert response.status_code == 200
    assert b"Search awesome repositories" in response.content
    assert b"Browse awesome lists" in response.content
    assert b"Request a list" in response.content


@pytest.mark.django_db
def test_legacy_repos_page_redirects_to_root(client):
    response = client.get("/repos/")

    assert response.status_code == 301
    assert response["Location"] == "/"


@pytest.mark.django_db
def test_search_page_exposes_semantic_search_filter(client):
    response = client.get(reverse("repos:search"), {"q": "framework", "mode": "semantic"})

    assert response.status_code == 200
    content = response.content.decode()
    assert 'name="mode"' in content
    assert 'x-model="searchMode"' in content
    assert re.search(r'<input\b[^>]*name="mode"[^>]*value="semantic"[^>]*checked', content)

    sort_select = re.search(r'<select\b[^>]*\bname="sort"[^>]*>', content)
    assert sort_select is not None
    assert "x-bind:disabled=\"searchMode === 'semantic'\"" in sort_select.group(0)
    assert re.search(r"\sdisabled(?=[\s>])", sort_select.group(0))


@pytest.mark.django_db
def test_search_page_humanizes_stars_and_hides_tracked_star_growth(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=123456,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=123521,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=123456,
    )

    response = client.get(reverse("repos:search"), {"q": "framework"})

    assert response.status_code == 200
    assert b"123,456" in response.content
    assert b"2 history points" in response.content
    assert b"-65 tracked" not in response.content
    assert b" tracked</div>" not in response.content


@pytest.mark.django_db
def test_search_page_hides_tracked_commit_growth(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=80,
        commit_count=90,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=70,
        commit_count=70,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=80,
        commit_count=90,
    )

    response = client.get(reverse("repos:search"), {"q": "framework"})

    assert response.status_code == 200
    assert b"90 commits" in response.content
    assert b"+20 commits tracked" not in response.content
    assert b"commits tracked" not in response.content


@pytest.mark.django_db
def test_awesome_list_list_page_renders_activity_metrics(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        description="Curated Django resources.",
        topics=["django", "awesome-list"],
        stars=1200,
        forks=100,
        open_issues=7,
        commits_count=350,
        readme_repository_count=42,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        github_pushed_at=timezone.now(),
        last_scanned_at=timezone.now(),
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        stars=80000,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    AwesomeList.objects.create(
        name="Young List",
        slug="young-list",
        source_url="https://github.com/example/young-list",
        first_commit_at=timezone.now() - timedelta(days=365 * 2),
        stars=50,
    )
    AwesomeList.objects.create(
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        is_active=False,
        stars=9999,
        readme_repository_count=500,
    )

    response = client.get(reverse("repos:list"), {"min_age_years": "10", "sort": "oldest"})

    assert response.status_code == 200
    assert response.context["awesome_lists"][0].indexed_repo_count == 1
    assert response.context["total_indexed_links"] == 1
    assert b"Awesome Django" in response.content
    assert b"first commit" in response.content
    assert b"Young List" not in response.content
    assert b"Inactive List" not in response.content
    assert b"wsvincent/awesome-django" in response.content
    assert b"README repos" in response.content
    assert b"1,200" in response.content
    assert b"42" in response.content
    assert b"350" in response.content
    assert b"django" in response.content
    assert b"Request a list" in response.content
    assert b"requestListOpen" in response.content
    assert b"openRequestList()" in response.content
    assert b"handleRequestListTab($event)" in response.content
    assert b'name="next"' in response.content
    assert b"Submit request" in response.content


@pytest.mark.django_db
@override_settings(CACHES=LOC_MEM_CACHES)
def test_awesome_list_request_page_accepts_public_requests(client):
    response = client.get(reverse("repos:request_list"))

    assert response.status_code == 200
    assert b"Request an awesome list" in response.content

    response = client.post(
        reverse("repos:request_list"),
        data={
            "source_url": "https://github.com/wsvincent/awesome-django",
            "requester_email": "reader@example.com",
            "note": "Please add this.",
        },
        follow=True,
    )

    assert response.status_code == 200
    assert AwesomeList.objects.count() == 0
    list_request = AwesomeListRequest.objects.get()
    assert list_request.repo_full_name == "wsvincent/awesome-django"
    assert list_request.requester_email == "reader@example.com"
    assert "has been submitted" in response.content.decode()


@pytest.mark.django_db
@override_settings(CACHES=LOC_MEM_CACHES)
def test_awesome_list_request_modal_redirects_back_to_lists(client):
    response = client.post(
        reverse("repos:request_list"),
        data={
            "source_url": "https://github.com/wsvincent/awesome-django",
            "requester_email": "reader@example.com",
            "next": reverse("repos:list"),
        },
    )

    assert response.status_code == 302
    assert response["Location"] == reverse("repos:list")


@pytest.mark.django_db
@override_settings(CACHES=LOC_MEM_CACHES)
def test_awesome_list_request_page_handles_duplicate_submit_race(client, monkeypatch):
    def raise_integrity_error(self, commit=True):
        raise IntegrityError("duplicate key value violates unique constraint")

    monkeypatch.setattr(AwesomeListRequestForm, "save", raise_integrity_error)

    response = client.post(
        reverse("repos:request_list"),
        data={"source_url": "https://github.com/wsvincent/awesome-django"},
    )

    assert response.status_code == 200
    assert AwesomeListRequest.objects.count() == 0
    assert "already been submitted" in response.content.decode()


@pytest.mark.django_db
@override_settings(CACHES=LOC_MEM_CACHES)
def test_awesome_list_request_page_rate_limits_public_posts(client, monkeypatch):
    cache.clear()
    monkeypatch.setattr("apps.repos.views.AwesomeListRequestView.rate_limit_count", 1)

    response = client.post(
        reverse("repos:request_list"),
        data={"source_url": "https://github.com/wsvincent/awesome-django"},
        HTTP_X_FORWARDED_FOR="203.0.113.10",
    )
    assert response.status_code == 302

    response = client.post(
        reverse("repos:request_list"),
        data={"source_url": "https://github.com/vinta/awesome-python"},
        HTTP_X_FORWARDED_FOR="203.0.113.11",
    )

    assert response.status_code == 429


@pytest.mark.django_db
def test_awesome_list_detail_page_renders_activity_metrics(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        description="Curated Django resources.",
        topics=["django"],
        stars=1200,
        forks=100,
        open_issues=7,
        watchers=25,
        commits_count=350,
        readme_repository_count=42,
        default_branch="main",
        github_pushed_at=timezone.now(),
        last_scanned_at=timezone.now(),
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=80000,
        forks=32000,
        commit_count=90000,
        github_pushed_at=timezone.now(),
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)

    response = client.get(reverse("repos:list_detail", kwargs={"slug": "awesome-django"}))

    assert response.status_code == 200
    content = response.content.decode()
    assert b"Awesome Django" in response.content
    assert b"README repos" in response.content
    assert b"List stars" in response.content
    assert b"List commits" in response.content
    assert b"django/django" in response.content
    assert b"Python" in response.content
    assert b"1,200" in response.content
    assert b"350" in response.content
    assert b"80,000" in response.content
    assert b"Tracked list growth" in response.content
    assert b"Likes history" in response.content
    assert b"Commits history" in response.content
    assert b"/static/vendors/js/d3.min.js" in response.content
    assert b"/static/js/modules/repository-history-charts.js" in response.content
    assert b"awesome-list-history-data" in response.content
    assert b'data-metric="stars"' in response.content
    assert b'data-metric="commit_count"' in response.content
    assert b'"stars": 1200' in response.content
    assert b'"commit_count": 350' in response.content
    assert b'data-ad-rail="left"' not in response.content
    assert b'data-ad-rail="right"' not in response.content
    assert 'href="/repos/django/django/" class="block rounded-2xl' not in content
    assert "<article " in content
    assert_repository_detail_link(content, "django/django")


@pytest.mark.django_db
def test_awesome_list_detail_page_renders_list_history_not_repository_aggregate(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        stars=1200,
        commits_count=350,
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=80000,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=79000,
        commit_count=89000,
    )
    AwesomeListSnapshot.objects.create(
        awesome_list=awesome_list,
        captured_at=timezone.now() - timedelta(days=1),
        stars=1100,
        commits_count=300,
    )
    AwesomeListSnapshot.objects.create(
        awesome_list=awesome_list,
        captured_at=timezone.now(),
        stars=1200,
        commits_count=350,
    )

    response = client.get(reverse("repos:list_detail", kwargs={"slug": awesome_list.slug}))

    assert response.status_code == 200
    assert b"Tracked list growth" in response.content
    assert b"Likes history" in response.content
    assert b"Commits history" in response.content
    assert b"/static/vendors/js/d3.min.js" in response.content
    assert b"/static/js/modules/repository-history-charts.js" in response.content
    assert b"awesome-list-history-data" in response.content
    assert b'"stars": 1200' in response.content
    assert b'"commit_count": 350' in response.content
    assert b'"stars": 79000' not in response.content
    assert b'"commit_count": 89000' not in response.content


@pytest.mark.django_db
def test_awesome_list_detail_page_skips_history_charts_without_list_metadata(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
    )

    response = client.get(reverse("repos:list_detail", kwargs={"slug": awesome_list.slug}))

    assert response.status_code == 200
    assert b"Tracked list growth" not in response.content
    assert b"/static/vendors/js/d3.min.js" not in response.content
    assert b"/static/js/modules/repository-history-charts.js" not in response.content
    assert b"awesome-list-history-data" not in response.content


@pytest.mark.django_db
def test_awesome_list_detail_page_filters_repositories(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        last_scanned_at=timezone.now(),
    )
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Django web framework",
        language="Python",
        topics=["django", "python"],
        generated_tags=["web-framework"],
        detected_stacks=["django"],
        stars=80000,
        commit_count=90000,
        first_commit_at=timezone.now() - timedelta(days=365 * 12),
        github_pushed_at=timezone.now(),
        uses_ai_for_development=True,
    )
    node_repo = Repository.objects.create(
        full_name="nodejs/node",
        owner="nodejs",
        name="node",
        url="https://github.com/nodejs/node",
        description="JavaScript runtime",
        language="JavaScript",
        topics=["javascript", "runtime"],
        generated_tags=["server-runtime"],
        detected_stacks=["express"],
        stars=110000,
        commit_count=120000,
        first_commit_at=timezone.now() - timedelta(days=365 * 2),
        github_pushed_at=timezone.now() - timedelta(days=500),
        is_archived=True,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=django_repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=node_repo)

    response = client.get(
        reverse("repos:list_detail", kwargs={"slug": "awesome-django"}),
        {
            "q": "django",
            "language": "Python",
            "topic": "django",
            "generated_tag": "web-framework",
            "framework": "django",
            "min_stars": "50",
            "updated_days": "30",
            "min_age_years": "10",
            "archived": "no",
            "ai_development": "yes",
            "list": "awesome-python",
            "sort": "commits",
        },
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "django/django" in content
    assert "nodejs/node" not in content
    assert "first commit" in content
    assert_option_label_with_count(content, "django", 1)
    assert_option_label_with_count(content, "Django", 1)
    assert_option_label_with_count(content, "web-framework", 1)
    assert 'name="mode"' in content
    assert 'name="list"' not in content
    assert 'class="md:col-span-2 lg:col-span-1 min-w-0"' in content
    assert "List: awesome-python" not in content
    assert "Forks" in content
    assert "Fewest list mentions" in content
    assert "Search: django" in content
    assert "Mode: Semantic relevance" not in content
    assert "list=awesome-python" not in response.context["querystring"]
    assert response.context["filters_applied"] is True
    assert response.context["page_obj"].paginator.count == 1
    assert response.context["repo_stats"]["active_count"] == 1
    assert response.context["repo_stats"]["archived_count"] == 1


@pytest.mark.django_db
def test_awesome_list_detail_page_hides_awesome_list_candidates(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        topics=["django"],
    )
    candidate = Repository.objects.create(
        full_name="vinta/awesome-python",
        owner="vinta",
        name="awesome-python",
        url="https://github.com/vinta/awesome-python",
        topics=["python"],
        is_awesome_list_candidate=True,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=candidate)

    response = client.get(reverse("repos:list_detail", kwargs={"slug": "awesome-django"}))

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert response.context["awesome_list"].indexed_repo_count == 1
    assert b"django/django" in response.content
    assert b"vinta/awesome-python" not in response.content


@pytest.mark.django_db
def test_awesome_list_detail_page_sorts_by_cross_list_mentions(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    other_list = AwesomeList.objects.create(
        name="Awesome Python",
        slug="awesome-python",
        source_url="https://github.com/vinta/awesome-python",
    )
    popular = Repository.objects.create(
        full_name="owner/popular",
        owner="owner",
        name="popular",
        url="https://github.com/owner/popular",
        stars=10,
    )
    solo = Repository.objects.create(
        full_name="owner/solo",
        owner="owner",
        name="solo",
        url="https://github.com/owner/solo",
        stars=100,
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=popular)
    AwesomeListItem.objects.create(awesome_list=other_list, repository=popular)
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=solo)

    response = client.get(
        reverse("repos:list_detail", kwargs={"slug": "awesome-django"}),
        {"sort": "awesome"},
    )

    assert response.status_code == 200
    repos = list(response.context["page_obj"].object_list)
    assert [repo.full_name for repo in repos] == ["owner/popular", "owner/solo"]
    assert repos[0].awesome_count == 2
    assert b"2 list mentions" in response.content


@pytest.mark.django_db
def test_awesome_list_detail_page_ignores_extreme_updated_days_filter(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        github_pushed_at=timezone.now(),
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)

    response = client.get(
        reverse("repos:list_detail", kwargs={"slug": "awesome-django"}),
        {"updated_days": "1000000000"},
    )

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 1
    assert b"django/django" in response.content


@pytest.mark.django_db
def test_awesome_list_detail_page_preserves_filters_in_pagination_links(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    for index in range(51):
        repo = Repository.objects.create(
            full_name=f"owner/repo-{index:02d}",
            owner="owner",
            name=f"repo-{index:02d}",
            url=f"https://github.com/owner/repo-{index:02d}",
            description="Owner maintained Django package",
            language="Python",
            stars=index,
        )
        AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)

    response = client.get(
        reverse("repos:list_detail", kwargs={"slug": "awesome-django"}),
        {"page": "2", "q": "owner", "sort": "name"},
    )

    assert response.status_code == 200
    assert response.context["page_obj"].paginator.count == 51
    assert "?page=1&amp;q=owner&amp;sort=name" in response.content.decode()


@pytest.mark.django_db
def test_awesome_list_detail_page_hides_inactive_lists(client):
    AwesomeList.objects.create(
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        is_active=False,
    )

    response = client.get(reverse("repos:list_detail", kwargs={"slug": "inactive-list"}))

    assert response.status_code == 404


@pytest.mark.django_db
def test_awesome_list_detail_page_shows_scan_controls_only_to_superusers(
    client,
    django_user_model,
):
    admin = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    user = django_user_model.objects.create_user(
        username="regular",
        email="regular@example.com",
        password="password123",
    )
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    url = reverse("repos:list_detail", kwargs={"slug": awesome_list.slug})

    response = client.get(url)

    assert response.status_code == 200
    assert b"Rescan list" not in response.content
    assert b"Find missing repos" not in response.content

    client.force_login(user)

    response = client.get(url)

    assert b"Rescan list" not in response.content
    assert b"Find missing repos" not in response.content

    client.force_login(admin)

    response = client.get(url)

    assert b"Rescan list" in response.content
    assert b"Find missing repos" in response.content


@pytest.mark.django_db
def test_superuser_can_queue_awesome_list_rescan_from_detail(
    client,
    django_user_model,
    monkeypatch,
):
    admin = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    client.force_login(admin)
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    queued = []

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return "task-1"

    monkeypatch.setattr("apps.repos.views.async_task", fake_async_task)
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("repos:list_rescan", kwargs={"slug": awesome_list.slug}),
        follow=True,
    )

    assert response.status_code == 200
    assert queued == [
        (
            "apps.repos.tasks.sync_awesome_list_task",
            (awesome_list.id,),
            {"group": "Scan awesome list"},
        )
    ]
    assert "Queued a rescan for Awesome Django." in response.content.decode()


@pytest.mark.django_db
def test_superuser_can_queue_awesome_list_missing_repo_discovery_from_detail(
    client,
    django_user_model,
    monkeypatch,
):
    admin = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    client.force_login(admin)
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    queued = []

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return "task-1"

    monkeypatch.setattr("apps.repos.views.async_task", fake_async_task)
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("repos:list_discover_missing", kwargs={"slug": awesome_list.slug}),
        follow=True,
    )

    assert response.status_code == 200
    assert queued == [
        (
            "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
            (awesome_list.id,),
            {"group": "Manual awesome-list missing repo discovery"},
        )
    ]
    assert "Queued missing repository discovery for Awesome Django." in response.content.decode()


@pytest.mark.django_db
def test_regular_user_cannot_queue_awesome_list_rescan(
    client,
    django_user_model,
    monkeypatch,
):
    django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    user = django_user_model.objects.create_user(
        username="regular",
        email="regular@example.com",
        password="password123",
    )
    client.force_login(user)
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )

    def fail_async_task(*args, **kwargs):
        raise AssertionError("regular users should not queue scans")

    monkeypatch.setattr("apps.repos.views.async_task", fail_async_task)
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())

    response = client.post(reverse("repos:list_rescan", kwargs={"slug": awesome_list.slug}))

    assert response.status_code == 403


@pytest.mark.django_db
def test_regular_user_cannot_queue_awesome_list_missing_repo_discovery(
    client,
    django_user_model,
    monkeypatch,
):
    django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    user = django_user_model.objects.create_user(
        username="regular",
        email="regular@example.com",
        password="password123",
    )
    client.force_login(user)
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )

    def fail_async_task(*args, **kwargs):
        raise AssertionError("regular users should not queue missing repo discovery")

    monkeypatch.setattr("apps.repos.views.async_task", fail_async_task)
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("repos:list_discover_missing", kwargs={"slug": awesome_list.slug})
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_repository_detail_page_renders_performance_history(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        topics=["django", "python"],
        stars=123456,
        forks=32000,
        watchers=5,
        commit_count=90,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=123431,
        forks=31990,
        watchers=4,
        commit_count=70,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=123456,
        forks=32000,
        watchers=5,
        commit_count=90,
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": "django", "name": "django"})
    )

    assert response.status_code == 200
    assert b"Tracked growth" in response.content
    assert b"123,456" in response.content
    assert b"32,000" in response.content
    assert b'href="/?topic=django"' in response.content
    assert b"Stars history" in response.content
    assert b"Commits history" in response.content
    assert b"/static/vendors/js/d3.min.js" in response.content
    assert b"/static/js/modules/repository-history-charts.js" in response.content
    assert b"repository-history-data" in response.content
    assert b'data-metric="stars"' in response.content
    assert b'data-metric="commit_count"' in response.content
    assert b'"stars": 123431' in response.content
    assert b'"commit_count": 90' in response.content
    assert b"Commits since first" not in response.content
    assert b"Forks since first" not in response.content
    assert b"<table" not in response.content


@pytest.mark.django_db
def test_repository_badge_svg_renders_shareable_history(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        language="Python",
        stars=123457,
        commit_count=90,
    )
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=123431,
        commit_count=70,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=123456,
        commit_count=90,
    )

    response = client.get(reverse("repos:repo_badge", kwargs={"owner": "django", "name": "django"}))

    assert response.status_code == 200
    assert response["Content-Type"] == "image/svg+xml; charset=utf-8"
    assert response["Cache-Control"] == "public, max-age=3600"
    assert response["X-Content-Type-Options"] == "nosniff"
    content = response.content.decode()
    assert content.startswith("<svg")
    assert "Awesome badge for django/django" in content
    assert "123.5k" in content
    assert "stars" in content
    assert "1 awesome list" in content
    assert "Python" in content
    assert "2 captures" in content


@pytest.mark.django_db
def test_repository_badge_svg_supports_commit_metric_and_escapes_metadata(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        language='"><script>alert(1)</script>',
        stars=75,
        commit_count=9,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=70,
        commit_count=5,
    )

    response = client.get(
        reverse("repos:repo_badge", kwargs={"owner": "django", "name": "django"}),
        {"metric": "commits", "theme": "dark"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "Commit history" in content
    assert "commits" in content
    assert "#020617" in content
    assert "<script>" not in content
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in content


@pytest.mark.django_db
def test_repository_badge_svg_renders_star_growth_periods(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=130,
        commit_count=90,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=8),
        stars=100,
        commit_count=70,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=125,
        commit_count=85,
    )

    response = client.get(
        reverse("repos:repo_badge", kwargs={"owner": "django", "name": "django"}),
        {"metric": "stars", "variant": "growth", "days": "7"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "7-day star growth" in content
    assert "+30" in content
    assert "stars in last 7 days" in content
    assert "100 to 130" in content


@pytest.mark.django_db
def test_repository_badge_svg_renders_commit_velocity_periods(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        stars=130,
        commit_count=120,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=31),
        stars=100,
        commit_count=75,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=5),
        stars=125,
        commit_count=115,
    )

    response = client.get(
        reverse("repos:repo_badge", kwargs={"owner": "django", "name": "django"}),
        {"metric": "commits", "variant": "growth", "days": "30"},
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "30-day commit velocity" in content
    assert "+45" in content
    assert "commits in last 30 days" in content
    assert "75 to 120" in content


@pytest.mark.django_db
def test_repository_detail_page_omits_share_badge_embed_snippets(client):
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=123456,
        commit_count=90,
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": "django", "name": "django"})
    )

    assert response.status_code == 200
    badge_path = reverse("repos:repo_badge", kwargs={"owner": "django", "name": "django"})
    content = response.content.decode()
    assert "Share badges" not in content
    assert f"http://testserver{badge_path}" not in content
    assert "repo-badge-markdown" not in content


@pytest.mark.django_db
def test_repository_detail_page_skips_chart_data_without_history(client, monkeypatch):
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=75,
        forks=12,
        watchers=5,
        commit_count=90,
    )

    def fail_chart_data(repository):
        raise AssertionError("chart data should not be queried without snapshot history")

    monkeypatch.setattr("apps.repos.views.repository_history_chart_data", fail_chart_data)

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": "django", "name": "django"})
    )

    assert response.status_code == 200
    assert b"/static/vendors/js/d3.min.js" not in response.content
    assert b"/static/js/modules/repository-history-charts.js" not in response.content
    assert b"repository-history-data" not in response.content
    assert b"Stars history" not in response.content


@pytest.mark.django_db
def test_repository_detail_page_compacts_ai_development_signals(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        uses_ai_for_development=True,
        ai_development_signals=[
            {
                "path": ".agents",
                "kind": "directory",
                "tool": "Agent workspace",
                "signal": "agent_directory",
            },
            {
                "path": ".agents/skills/company-creator/SKILL.md",
                "kind": "file",
                "tool": "Agent workspace",
                "signal": "agent_directory",
            },
            {
                "path": ".agents/skills/doc-maintenance/references/audit-checklist.md",
                "kind": "file",
                "tool": "Agent workspace",
                "signal": "agent_directory",
            },
            {
                "path": ".github/copilot-instructions.md",
                "kind": "file",
                "tool": "GitHub Copilot",
                "signal": "copilot_repo_instructions",
            },
            {
                "path": "CLAUDE.md",
                "kind": "file",
                "tool": "Claude Code",
                "signal": "claude_memory",
            },
            {
                "path": ".cursor/rules/python.mdc",
                "kind": "file",
                "tool": "Cursor",
                "signal": "cursor_project_rules",
            },
            {
                "path": ".windsurfrules",
                "kind": "file",
                "tool": "Windsurf",
                "signal": "windsurf_legacy_rules",
            },
        ],
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    summary = response.context["ai_development_signal_summary"]
    assert summary["total_count"] == 7
    assert summary["file_count"] == 6
    assert summary["directory_count"] == 1
    assert [tool["name"] for tool in summary["visible_tools"]] == [
        "Agent workspace",
        "Claude Code",
        "Cursor",
        "GitHub Copilot",
        "Windsurf",
    ]
    assert [signal["path"] for signal in summary["visible_signals"]] == [
        ".agents",
        ".cursor/rules/python.mdc",
        ".github/copilot-instructions.md",
        ".windsurfrules",
        "CLAUDE.md",
    ]
    assert summary["hidden_signal_count"] == 0
    assert summary["show_detail_signals"] is True
    assert b"AI agent config detected" in response.content
    assert b"Key config paths" in response.content
    assert b"more config paths detected." not in response.content
    assert b"Review config paths" in response.content
    assert b"AI dev signals:" not in response.content


@pytest.mark.django_db
def test_repository_detail_page_shows_empty_ai_development_signal_state(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    summary = response.context["ai_development_signal_summary"]
    assert summary["has_signals"] is False
    assert summary["total_count"] == 0
    assert summary["visible_tools"] == []
    assert summary["visible_signals"] == []
    assert b"No AI development config files detected." in response.content


@pytest.mark.django_db
def test_repository_detail_page_hides_ai_development_detail_expander_when_all_paths_visible(
    client,
):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        uses_ai_for_development=True,
        ai_development_signals=[
            {
                "path": "AGENTS.md",
                "kind": "file",
                "tool": "Agent instructions",
                "signal": "agent_instructions",
            }
        ],
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    summary = response.context["ai_development_signal_summary"]
    assert summary["total_count"] == 1
    assert len(summary["visible_signals"]) == 1
    assert summary["show_detail_signals"] is False
    assert b"AGENTS.md" in response.content
    assert b"Review config paths" not in response.content


@pytest.mark.django_db
def test_repository_detail_page_counts_hidden_ai_development_key_paths(client):
    signals = [
        {
            "path": f".github/instructions/agent-{index}.instructions.md",
            "kind": "file",
            "tool": "GitHub Copilot",
            "signal": "copilot_path_instructions",
        }
        for index in range(8)
    ]
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        uses_ai_for_development=True,
        ai_development_signals=signals,
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 200
    summary = response.context["ai_development_signal_summary"]
    assert summary["total_count"] == 8
    assert len(summary["visible_signals"]) == 6
    assert summary["hidden_signal_count"] == 2
    assert summary["show_detail_signals"] is True
    assert b"2 more config paths detected." in response.content


@pytest.mark.django_db
def test_repository_detail_page_shows_rescan_control_only_to_superusers(
    client,
    django_user_model,
):
    admin = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    user = django_user_model.objects.create_user(
        username="regular",
        email="regular@example.com",
        password="password123",
    )
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    url = reverse("repos:repo_detail", kwargs={"owner": repo.owner, "name": repo.name})

    response = client.get(url)

    assert response.status_code == 200
    assert b"Rescan repo" not in response.content

    client.force_login(user)

    response = client.get(url)

    assert b"Rescan repo" not in response.content

    client.force_login(admin)

    response = client.get(url)

    assert b"Rescan repo" in response.content


@pytest.mark.django_db
def test_superuser_can_queue_repository_rescan_from_detail(
    client,
    django_user_model,
    monkeypatch,
):
    admin = django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    client.force_login(admin)
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )
    queued = []

    def fake_async_task(func_path, *args, **kwargs):
        queued.append((func_path, args, kwargs))
        return "task-1"

    monkeypatch.setattr("apps.repos.views.async_task", fake_async_task)
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("repos:repo_rescan", kwargs={"owner": repo.owner, "name": repo.name}),
        follow=True,
    )

    assert response.status_code == 200
    assert queued == [
        (
            "apps.repos.tasks.refresh_repository_task",
            (repo.id, repo.full_name),
            {"group": "Refresh repositories"},
        )
    ]
    assert "Queued a rescan for django/django." in response.content.decode()


@pytest.mark.django_db
def test_regular_user_cannot_queue_repository_rescan(
    client,
    django_user_model,
    monkeypatch,
):
    django_user_model.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="password123",
    )
    user = django_user_model.objects.create_user(
        username="regular",
        email="regular@example.com",
        password="password123",
    )
    client.force_login(user)
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
    )

    def fail_async_task(*args, **kwargs):
        raise AssertionError("regular users should not queue repository rescans")

    monkeypatch.setattr("apps.repos.views.async_task", fail_async_task)
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())

    response = client.post(
        reverse("repos:repo_rescan", kwargs={"owner": repo.owner, "name": repo.name})
    )

    assert response.status_code == 403


@pytest.mark.django_db
def test_repository_detail_page_renders_similar_repositories(client, settings):
    settings.REPOSITORY_EMBEDDING_MODEL = "openai/text-embedding-3-small"
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=75,
    )
    similar_repo = Repository.objects.create(
        full_name="encode/django-rest-framework",
        owner="encode",
        name="django-rest-framework",
        url="https://github.com/encode/django-rest-framework",
        description="API toolkit for Django",
        language="Python",
        stars=30,
    )
    Repository.objects.create(
        full_name="owner/no-vector",
        owner="owner",
        name="no-vector",
        url="https://github.com/owner/no-vector",
        description="No embedding",
        stars=1000,
    )
    RepositoryEmbedding.objects.create(
        repository=repo,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="r" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )
    RepositoryEmbedding.objects.create(
        repository=similar_repo,
        model="openai/text-embedding-3-small",
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash="m" * 64,
        source_text_chars=10,
        embedding=[1.0] + [0.0] * (REPOSITORY_EMBEDDING_DIMENSIONS - 1),
        embedded_at=timezone.now(),
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": "django", "name": "django"})
    )

    assert response.status_code == 200
    assert b"Similar repositories" in response.content
    assert b"encode/django-rest-framework" in response.content
    assert b"owner/no-vector" not in response.content
