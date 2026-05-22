import base64
from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from apps.repos.embeddings import (
    build_repository_embedding_payload,
    build_repository_embedding_text,
    save_repository_embedding,
)
from apps.repos.forms import AwesomeListCreateForm
from apps.repos.models import (
    REPOSITORY_EMBEDDING_DIMENSIONS,
    AwesomeList,
    AwesomeListItem,
    Repository,
    RepositoryEmbedding,
    RepositorySnapshot,
)
from apps.repos.services import (
    attach_awesome_list_commit_count,
    add_repository_to_awesome_list,
    discover_missing_awesome_list_repositories,
    extract_github_repos,
    fetch_github_commit_count,
    fetch_json,
    fetch_repository_readme,
    fetch_repository_readme_data,
    github_rate_limit_status,
    parse_github_repo_url,
    repository_performance_summary,
    repository_search_queryset,
    sync_awesome_list,
    update_awesome_list_metadata,
    upsert_repository_from_github,
)
from apps.repos.tags import (
    build_repository_tagging_payload,
    normalize_repository_tags,
    repository_tagging_model_id,
    save_repository_tags,
)
from apps.repos.tasks import refresh_repositories_task, refresh_repository_task
from apps.repos.views import awesome_list_directory_totals, repository_json_value_counts


@pytest.fixture(autouse=True)
def disable_repository_tagging(settings):
    settings.REPOSITORY_TAGGING_ENABLED = False


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

    form = AwesomeListCreateForm(data={"source_url": "https://github.com/wsvincent/awesome-django"})

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
        lambda full_name: (markdown, github_awesome_list_payload(full_name)),
    )
    monkeypatch.setattr("apps.repos.services.fetch_github_commit_count", lambda *args: 350)

    def fake_upsert(full_name):
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
    assert awesome_list.readme_repository_count == 2
    assert awesome_list.default_branch == "main"
    assert awesome_list.github_pushed_at is not None
    assert awesome_list.last_error == ""
    assert awesome_list.items.count() == 1


@pytest.mark.django_db
def test_update_awesome_list_metadata_preserves_missing_commit_count():
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        repo_full_name="wsvincent/awesome-django",
        commits_count=350,
    )
    awesome_list.commits_count = None

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
    assert awesome_list.readme_repository_count == 42


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
        lambda full_name: (
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


def stub_repository_readme(monkeypatch, content="# Django\n"):
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        lambda full_name: {
            "ok": True,
            "readme": content,
            "readme_path": "README.md",
            "readme_url": f"https://raw.githubusercontent.com/{full_name}/main/README.md",
            "readme_last_error": "",
        },
    )


@pytest.mark.django_db
def test_upsert_repository_from_github_records_snapshot(monkeypatch):
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url: github_repo_payload(stars=80000, forks=32000, watchers=1200),
    )
    stub_repository_readme(monkeypatch)

    repo = upsert_repository_from_github("django/django")

    snapshot = RepositorySnapshot.objects.get(repository=repo)
    assert repo.stars == 80000
    assert repo.forks == 32000
    assert repo.watchers == 1200
    assert snapshot.stars == repo.stars
    assert snapshot.forks == repo.forks
    assert snapshot.watchers == repo.watchers
    assert snapshot.captured_at == repo.last_synced_at


@pytest.mark.django_db
def test_upsert_repository_from_github_records_snapshot_for_each_refresh(monkeypatch):
    payloads = [
        github_repo_payload(stars=10, forks=3, watchers=1),
        github_repo_payload(stars=15, forks=4, watchers=2),
    ]

    def fake_fetch_json(url):
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
        lambda url: github_repo_payload(stars=15, forks=4, watchers=2),
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
        lambda url: {
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


def test_fetch_github_commit_count_uses_last_link_page(monkeypatch):
    captured = {}

    class DummyResponse:
        headers = {
            "Link": (
                '<https://api.github.com/repositories/1/commits?sha=main&per_page=1&page=2>;'
                ' rel="next", '
                '<https://api.github.com/repositories/1/commits?sha=main&per_page=1&page=456>;'
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


def test_fetch_github_commit_count_requires_default_branch():
    with pytest.raises(ValueError, match="default branch"):
        fetch_github_commit_count("owner/repo", "")


def test_attach_awesome_list_commit_count_is_explicit_about_commit_fetch(monkeypatch):
    calls = []

    def fake_fetch_commit_count(full_name, default_branch):
        calls.append((full_name, default_branch))
        return 456

    monkeypatch.setattr("apps.repos.services.fetch_github_commit_count", fake_fetch_commit_count)
    meta = {"default_branch": "trunk"}

    attach_awesome_list_commit_count("owner/repo", meta)

    assert calls == [("owner/repo", "trunk")]
    assert meta["commits_count"] == 456


def test_attach_awesome_list_commit_count_skips_missing_default_branch(monkeypatch):
    def fail_fetch_commit_count(full_name, default_branch):
        raise AssertionError("commit count should not be fetched without a default branch")

    monkeypatch.setattr("apps.repos.services.fetch_github_commit_count", fail_fetch_commit_count)
    meta = {}

    attach_awesome_list_commit_count("owner/repo", meta)

    assert "commits_count" not in meta


@pytest.mark.django_db
def test_upsert_repository_from_github_stores_readme(monkeypatch):
    readme = "# Django\nThe Web framework.\n"

    def fake_fetch_json(url):
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
        lambda url: github_repo_payload(stars=15, forks=4, watchers=2),
    )
    monkeypatch.setattr(
        "apps.repos.services.fetch_repository_readme_data",
        lambda full_name: {
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
def test_enqueue_awesome_list_missing_repo_syncs_task_queues_active_lists(monkeypatch):
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

    assert result == {"queued": 1, "task_ids": ["task-1"]}
    assert queued == [
        (
            "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
            (active.id,),
            {
                "limit": 5,
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
        "apps.repos.tasks.discover_missing_awesome_list_repositories",
        fake_discover,
    )
    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    from apps.repos.tasks import enqueue_missing_repositories_for_awesome_list_task

    result = enqueue_missing_repositories_for_awesome_list_task(awesome_list.id, limit=10)

    assert result["queued"] == 2
    assert result["task_ids"] == ["task-1", "task-2"]
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
        "apps.repos.tasks.discover_missing_awesome_list_repositories",
        fake_discover,
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
def test_add_missing_repository_to_awesome_list_task_persists_last_error(monkeypatch):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
    )

    def fail_add_repository(awesome_list, repo_full_name):
        raise RuntimeError(f"GitHub failed for {repo_full_name}")

    monkeypatch.setattr("apps.repos.tasks.add_repository_to_awesome_list", fail_add_repository)

    from apps.repos.tasks import add_missing_repository_to_awesome_list_task

    with pytest.raises(RuntimeError, match="GitHub failed for django/django"):
        add_missing_repository_to_awesome_list_task(awesome_list.id, "django/django")

    awesome_list.refresh_from_db()
    assert awesome_list.last_error == "GitHub failed for django/django"


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


def test_github_rate_limit_status_formats_core_limit(monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_test_token")
    monkeypatch.setattr(
        "apps.repos.services.fetch_json",
        lambda url: {
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
        lambda url: {
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

    def fake_fetch_json(url):
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

    def fake_fetch_json(url):
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
def test_upsert_repository_from_github_syncs_generated_tags_from_readme(
    monkeypatch,
    settings,
):
    settings.REPOSITORY_TAGGING_ENABLED = True
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    captured = {}

    def fake_fetch_json(url):
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
        lambda full_name: readme_text,
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

    def fake_upsert_repository_from_github(full_name):
        refreshed.append(full_name)
        return repository

    monkeypatch.setattr(
        "apps.repos.tasks.upsert_repository_from_github",
        fake_upsert_repository_from_github,
    )

    result = refresh_repository_task(repository.id, repository.full_name)

    assert refreshed == ["django/django"]
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

    def fake_fetch_json(url):
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

    def fake_upsert_repository_from_github(full_name):
        raise RuntimeError(f"could not refresh {full_name}")

    monkeypatch.setattr("apps.repos.tasks.logger", dummy_logger)
    monkeypatch.setattr(
        "apps.repos.tasks.upsert_repository_from_github",
        fake_upsert_repository_from_github,
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


@pytest.mark.django_db
def test_refresh_repositories_task_queues_one_task_per_repository(monkeypatch):
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
        last_synced_at=timezone.now(),
    )
    queued = []

    def fake_async_task(func_path, repository_id, full_name, **kwargs):
        task_id = f"task-{repository_id}"
        queued.append((func_path, repository_id, full_name, kwargs, task_id))
        return task_id

    monkeypatch.setattr("apps.repos.tasks.async_task", fake_async_task)

    result = refresh_repositories_task()

    assert queued == [
        (
            "apps.repos.tasks.refresh_repository_task",
            stale.id,
            "owner/stale",
            {"group": "Refresh repositories"},
            f"task-{stale.id}",
        ),
        (
            "apps.repos.tasks.refresh_repository_task",
            fresh.id,
            "owner/fresh",
            {"group": "Refresh repositories"},
            f"task-{fresh.id}",
        ),
    ]
    assert result == {
        "queued": 2,
        "repositories": [
            {
                "repository_id": stale.id,
                "full_name": "owner/stale",
                "task_id": f"task-{stale.id}",
            },
            {
                "repository_id": fresh.id,
                "full_name": "owner/fresh",
                "task_id": f"task-{fresh.id}",
            },
        ],
    }


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
def test_repository_search_filters_by_topic_and_generated_tag():
    django_repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="Django tool",
        topics=["django", "python", "web"],
        generated_tags=["web-framework", "orm"],
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
        stars=100,
    )

    assert list(repository_search_queryset({"topic": "django"})) == [django_repo]
    assert list(repository_search_queryset({"generated_tag": "server runtime"})) == [node_repo]
    assert list(repository_search_queryset({"q": "orm"})) == [django_repo]


@pytest.mark.django_db
def test_repository_json_value_counts_aggregates_server_side():
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        topics=["django", "python", "web"],
        generated_tags=["web-framework", "orm"],
    )
    Repository.objects.create(
        full_name="django/channels",
        owner="django",
        name="channels",
        url="https://github.com/django/channels",
        topics=["django", "python", "async"],
        generated_tags=["web-framework", "websocket"],
    )

    assert repository_json_value_counts("topics")[:2] == [
        {"name": "django", "count": 2},
        {"name": "python", "count": 2},
    ]
    assert repository_json_value_counts("generated_tags", limit=1) == [
        {"name": "web-framework", "count": 2}
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
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)
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
        stars=10,
    )
    far = Repository.objects.create(
        full_name="owner/far",
        owner="owner",
        name="far",
        url="https://github.com/owner/far",
        description="Terminal theme",
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
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=50,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=75,
    )

    result = repository_search_queryset({"q": "django"}).get()

    assert result.snapshot_count == 2
    assert result.first_snapshot_stars == 50
    assert result.stars_since_first == 25


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
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=50,
        forks=10,
        watchers=4,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=75,
        forks=12,
        watchers=5,
    )

    summary = repository_performance_summary(repo)

    assert summary["snapshot_count"] == 2
    assert summary["stars_since_first"] == 25
    assert summary["stars_since_first_label"] == "+25"
    assert summary["forks_since_first"] == 2
    assert summary["watchers_since_first"] == 1
    assert summary["history"][0]["stars_delta"] == 25
    assert summary["history"][1]["stars_delta_label"] == "baseline"


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
def test_search_page_renders(client):
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        topics=["django", "python"],
        generated_tags=["web-framework"],
        stars=80000,
    )
    response = client.get(reverse("repos:search"), {"q": "framework"})
    assert response.status_code == 200
    assert b"django/django" in response.content
    assert b"Any GitHub topic" in response.content
    assert b"django (1)" in response.content
    assert b"web-framework (1)" in response.content


@pytest.mark.django_db
def test_search_page_renders_negative_tracked_growth(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=80,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=100,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=80,
    )

    response = client.get(reverse("repos:search"), {"q": "framework"})

    assert response.status_code == 200
    assert b"-20 tracked" in response.content
    assert b">0 tracked<" not in response.content


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
        name="Inactive List",
        slug="inactive-list",
        source_url="https://github.com/example/inactive-list",
        is_active=False,
        stars=9999,
        readme_repository_count=500,
    )

    response = client.get(reverse("repos:list"))

    assert response.status_code == 200
    assert b"Awesome Django" in response.content
    assert b"Inactive List" not in response.content
    assert b"wsvincent/awesome-django" in response.content
    assert b"README repos" in response.content
    assert b"42" in response.content
    assert b"350" in response.content
    assert b"django" in response.content


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
        github_pushed_at=timezone.now(),
    )
    AwesomeListItem.objects.create(awesome_list=awesome_list, repository=repo)

    response = client.get(reverse("repos:list_detail", kwargs={"slug": "awesome-django"}))

    assert response.status_code == 200
    assert b"Awesome Django" in response.content
    assert b"README repos" in response.content
    assert b"Commits" in response.content
    assert b"django/django" in response.content
    assert b"Python" in response.content


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
def test_repository_detail_page_renders_performance_history(client):
    repo = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework",
        language="Python",
        stars=75,
        forks=12,
        watchers=5,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=2),
        stars=50,
        forks=10,
        watchers=4,
    )
    RepositorySnapshot.objects.create(
        repository=repo,
        captured_at=timezone.now() - timedelta(days=1),
        stars=75,
        forks=12,
        watchers=5,
    )

    response = client.get(
        reverse("repos:repo_detail", kwargs={"owner": "django", "name": "django"})
    )

    assert response.status_code == 200
    assert b"Tracked growth" in response.content
    assert b"+25" in response.content
