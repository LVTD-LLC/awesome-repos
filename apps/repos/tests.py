from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone

from apps.repos.forms import AwesomeListCreateForm
from apps.repos.models import AwesomeList, AwesomeListItem, Repository
from apps.repos.services import (
    extract_github_repos,
    fetch_json,
    parse_github_repo_url,
    repository_search_queryset,
    sync_awesome_list,
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


@pytest.mark.django_db
def test_awesome_list_form_derives_name_and_unique_slug_from_url():
    AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/old/awesome-django",
    )

    form = AwesomeListCreateForm(
        data={"source_url": "https://github.com/wsvincent/awesome-django"}
    )

    assert form.is_valid()
    awesome_list = form.save()

    assert awesome_list.name == "Awesome Django"
    assert awesome_list.slug == "awesome-django-2"


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
        lambda full_name: ("# Empty\n", {"full_name": full_name, "description": ""}),
    )

    result = sync_awesome_list(awesome_list)
    awesome_list.refresh_from_db()

    assert result["discovered"] == 0
    assert result["synced"] == 0
    assert awesome_list.last_error == "No GitHub repository links found in README."


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
def test_repository_search_filters_and_sorts():
    recent = Repository.objects.create(
        full_name="owner/recent",
        owner="owner",
        name="recent",
        url="https://github.com/owner/recent",
        description="Django tool",
        language="Python",
        stars=50,
        github_pushed_at=timezone.now(),
    )
    old = Repository.objects.create(
        full_name="owner/old",
        owner="owner",
        name="old",
        url="https://github.com/owner/old",
        description="Node app",
        language="JavaScript",
        stars=100,
        github_pushed_at=timezone.now() - timedelta(days=500),
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


@pytest.mark.django_db
def test_search_page_renders(client):
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=80000,
    )
    response = client.get(reverse("repos:search"), {"q": "framework"})
    assert response.status_code == 200
    assert b"django/django" in response.content
