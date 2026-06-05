from datetime import UTC, date, datetime

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from pydantic_ai.models.openai import OpenAIChatModel

from apps.core.agents.base import build_model
from apps.repos.forms import NewsletterSubscriptionForm
from apps.repos.models import (
    NewsletterCadence,
    NewsletterIssueDelivery,
    NewsletterSubscription,
    Repository,
    RepositoryCommit,
    RepositoryNewsletterIssue,
)
from apps.repos.newsletters import (
    NewsletterIssueOutput,
    NewsletterPeriod,
    generate_repository_newsletter_issue,
    poll_repository_commits,
    render_newsletter_markdown,
    send_issue_to_subscribers,
    unsubscribe_newsletter,
    upsert_newsletter_subscription,
)
from apps.repos.services import GitHubAPIError


@pytest.fixture(autouse=True)
def disable_profile_async_tasks(monkeypatch):
    monkeypatch.setattr("apps.core.models.async_task", lambda *args, **kwargs: "task-id")


@pytest.fixture
def repository():
    return Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        default_branch="main",
        stars=100,
    )


def commit_detail(sha="abc123", *, committed_at="2026-05-26T12:00:00Z", patch="+hello"):
    return {
        "sha": sha,
        "node_id": f"node-{sha}",
        "html_url": f"https://github.com/django/django/commit/{sha}",
        "url": f"https://api.github.com/repos/django/django/commits/{sha}",
        "comments_url": f"https://api.github.com/repos/django/django/commits/{sha}/comments",
        "commit": {
            "message": "Add newsletter tracking",
            "author": {
                "name": "Ada",
                "email": "ada@example.com",
                "date": committed_at,
            },
            "committer": {
                "name": "Ada",
                "email": "ada@example.com",
                "date": committed_at,
            },
        },
        "author": {
            "login": "ada",
            "id": 1,
            "html_url": "https://github.com/ada",
            "avatar_url": "https://avatars.example/ada",
        },
        "committer": {
            "login": "ada",
            "id": 1,
            "html_url": "https://github.com/ada",
            "avatar_url": "https://avatars.example/ada",
        },
        "parents": [{"sha": "parent1"}],
        "stats": {"additions": 5, "deletions": 2, "total": 7},
        "files": [
            {
                "filename": "apps/repos/newsletters.py",
                "status": "modified",
                "additions": 5,
                "deletions": 2,
                "changes": 7,
                "blob_url": "https://github.com/django/django/blob/main/apps/repos/newsletters.py",
                "raw_url": "https://raw.githubusercontent.com/django/django/main/apps/repos/newsletters.py",
                "patch": patch,
            }
        ],
    }


@pytest.mark.django_db
def test_newsletter_subscription_form_normalizes_email():
    form = NewsletterSubscriptionForm(
        data={"email": "ADMIN@Example.COM", "cadence": NewsletterCadence.MONTHLY}
    )

    assert form.is_valid(), form.errors
    assert form.cleaned_data["email"] == "admin@example.com"


@pytest.mark.django_db
def test_upsert_subscription_enables_tracking_and_updates_existing(user, repository):
    subscription = upsert_newsletter_subscription(
        user=user,
        repository=repository,
        email="ADMIN@Example.COM",
        cadence=NewsletterCadence.WEEKLY,
    )

    repository.refresh_from_db()
    assert repository.newsletter_tracking_enabled is True
    assert repository.newsletter_tracking_started_at is not None
    assert subscription.email == "admin@example.com"
    assert subscription.cadence == NewsletterCadence.WEEKLY

    updated = upsert_newsletter_subscription(
        user=user,
        repository=repository,
        email="other@example.com",
        cadence=NewsletterCadence.MONTHLY,
    )

    assert updated.id == subscription.id
    assert updated.email == "other@example.com"
    assert updated.cadence == NewsletterCadence.MONTHLY


@pytest.mark.django_db
def test_active_subscription_is_unique_per_user_and_repository(user, repository):
    NewsletterSubscription.objects.create(
        user=user,
        repository=repository,
        email="first@example.com",
        cadence=NewsletterCadence.WEEKLY,
    )

    with pytest.raises(IntegrityError):
        with transaction.atomic():
            NewsletterSubscription.objects.create(
                user=user,
                repository=repository,
                email="second@example.com",
                cadence=NewsletterCadence.MONTHLY,
            )


@pytest.mark.django_db
def test_repository_detail_hides_newsletter_controls_from_regular_users(auth_client, repository):
    response = auth_client.get(repository.get_absolute_url())

    assert response.status_code == 200
    assert b"Newsletter tracking" not in response.content


@pytest.mark.django_db
def test_regular_user_cannot_subscribe_to_newsletter(auth_client, repository):
    response = auth_client.post(
        reverse(
            "repos:repo_newsletter_subscribe",
            kwargs={"owner": repository.owner, "name": repository.name},
        ),
        data={"email": "reader@example.com", "cadence": NewsletterCadence.WEEKLY},
    )

    assert response.status_code == 403
    assert NewsletterSubscription.objects.count() == 0


@pytest.mark.django_db
def test_superuser_can_subscribe_from_repository_detail(
    client,
    django_user_model,
    repository,
    monkeypatch,
):
    admin = django_user_model.objects.create_user(
        username="admin",
        email="admin@example.com",
        password="password123",
        is_superuser=True,
        is_staff=True,
    )
    client.force_login(admin)
    queued = []
    monkeypatch.setattr("apps.repos.views.transaction.on_commit", lambda callback: callback())
    monkeypatch.setattr(
        "apps.repos.views.async_task",
        lambda func_path, *args, **kwargs: queued.append((func_path, args, kwargs)) or "task-1",
    )

    response = client.post(
        reverse(
            "repos:repo_newsletter_subscribe",
            kwargs={"owner": repository.owner, "name": repository.name},
        ),
        data={"email": "ADMIN@Example.COM", "cadence": NewsletterCadence.MONTHLY},
    )

    assert response.status_code == 302
    subscription = NewsletterSubscription.objects.get()
    repository.refresh_from_db()
    assert subscription.email == "admin@example.com"
    assert subscription.cadence == NewsletterCadence.MONTHLY
    assert repository.newsletter_tracking_enabled is True
    assert queued == [
        (
            "apps.repos.tasks.poll_tracked_repository_commits_task",
            (repository.id,),
            {"group": "Poll repository newsletter commits"},
        )
    ]


@pytest.mark.django_db
@override_settings(
    NEWSLETTER_COMMIT_FILE_PATCH_MAX_CHARS=5,
    NEWSLETTER_COMMIT_TOTAL_PATCH_MAX_CHARS=8,
)
def test_poll_repository_commits_stores_bounded_commit_data(repository, monkeypatch):
    repository.newsletter_tracking_enabled = True
    repository.newsletter_tracking_started_at = datetime(2026, 5, 25, tzinfo=UTC)
    repository.save(update_fields=["newsletter_tracking_enabled", "newsletter_tracking_started_at"])
    monkeypatch.setattr(
        "apps.repos.newsletters.fetch_repository_commit_page",
        lambda repository, branch, since, page: ([{"sha": "abc123"}], ""),
    )
    monkeypatch.setattr(
        "apps.repos.newsletters.fetch_repository_commit_detail",
        lambda repository, sha: commit_detail(sha=sha, patch="+123456789"),
    )

    result = poll_repository_commits(repository)
    second_result = poll_repository_commits(repository)

    repository.refresh_from_db()
    commit = RepositoryCommit.objects.get()
    assert result["saved"] == 1
    assert result["created"] == 1
    assert second_result["created"] == 0
    assert repository.newsletter_tracking_last_polled_at is not None
    assert commit.branch == "main"
    assert commit.author_login == "ada"
    assert commit.additions == 5
    assert commit.deletions == 2
    assert commit.parent_shas == ["parent1"]
    assert commit.patch_truncated is True
    assert commit.files[0]["patch"] == "+1234"
    assert commit.html_url.endswith("/abc123")
    assert RepositoryCommit.objects.count() == 1


@pytest.mark.django_db
def test_poll_repository_commits_records_rate_limit_without_advancing_watermark(
    repository,
    monkeypatch,
):
    repository.newsletter_tracking_enabled = True
    repository.newsletter_tracking_started_at = datetime(2026, 5, 25, tzinfo=UTC)
    repository.save(update_fields=["newsletter_tracking_enabled", "newsletter_tracking_started_at"])

    def fail_rate_limit(*args, **kwargs):
        raise GitHubAPIError("rate limit exceeded", status_code=403, rate_limit_remaining="0")

    monkeypatch.setattr("apps.repos.newsletters.fetch_repository_commit_page", fail_rate_limit)

    result = poll_repository_commits(repository)

    repository.refresh_from_db()
    assert result["stopped_for_rate_limit"] is True
    assert repository.newsletter_tracking_last_polled_at is None
    assert "rate limit exceeded" in repository.newsletter_tracking_last_error


@pytest.mark.django_db
def test_poll_repository_commits_rechecks_rate_limit_before_commit_details(
    repository,
    monkeypatch,
):
    repository.newsletter_tracking_enabled = True
    repository.newsletter_tracking_started_at = datetime(2026, 5, 25, tzinfo=UTC)
    repository.save(update_fields=["newsletter_tracking_enabled", "newsletter_tracking_started_at"])
    detail_calls = []
    budget_checks = {"count": 0}

    monkeypatch.setattr(
        "apps.repos.newsletters.fetch_repository_commit_page",
        lambda repository, branch, since, page: ([{"sha": "abc123"}, {"sha": "def456"}], ""),
    )
    monkeypatch.setattr(
        "apps.repos.newsletters.fetch_repository_commit_detail",
        lambda repository, sha: detail_calls.append(sha) or commit_detail(sha=sha),
    )

    def budget_exhausted():
        budget_checks["count"] += 1
        return budget_checks["count"] >= 3

    monkeypatch.setattr(
        "apps.repos.newsletters._github_rate_limit_budget_exhausted",
        budget_exhausted,
    )

    result = poll_repository_commits(repository)

    repository.refresh_from_db()
    assert result == {
        "repository_id": repository.id,
        "skipped": "github_rate_limit_budget",
        "saved": 1,
        "created": 1,
    }
    assert detail_calls == ["abc123"]
    assert repository.newsletter_tracking_last_polled_at is None


@pytest.mark.django_db
def test_generate_repository_newsletter_issue_uses_calendar_period_and_sanitizes(
    repository,
    monkeypatch,
):
    repository.newsletter_tracking_enabled = True
    repository.save(update_fields=["newsletter_tracking_enabled"])
    RepositoryCommit.objects.create(
        repository=repository,
        sha="abc123",
        branch="main",
        message="Add newsletter tracking",
        summary="Added newsletter tracking.",
        summary_source_hash="summary-hash",
        committed_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
        html_url="https://github.com/django/django/commit/abc123",
    )
    monkeypatch.setattr("apps.repos.newsletters.newsletter_ai_configured", lambda: True)
    monkeypatch.setattr(
        "apps.repos.newsletters.newsletter_model_id",
        lambda: "openrouter/test-model",
    )
    monkeypatch.setattr(
        "apps.repos.newsletters.generate_issue_content",
        lambda text: NewsletterIssueOutput(
            title="Django weekly update",
            content_markdown=(
                "## Changes\n<script>alert(1)</script>\n"
                "[unsafe](javascript:alert(1))\n[commit](https://github.com/django/django)"
            ),
        ),
    )

    issue = generate_repository_newsletter_issue(
        repository,
        cadence=NewsletterCadence.WEEKLY,
        period=NewsletterPeriod(start=date(2026, 5, 25), end=date(2026, 5, 31)),
    )

    assert issue is not None
    assert issue.slug == "2026-05-25"
    assert issue.commit_count == 1
    assert issue.published_at is not None
    assert "<script>" not in issue.content_html
    assert "javascript:alert" not in issue.content_html
    assert 'href="https://github.com/django/django"' in issue.content_html


@pytest.mark.django_db
def test_generate_repository_newsletter_issue_skips_empty_period(repository, monkeypatch):
    monkeypatch.setattr("apps.repos.newsletters.newsletter_ai_configured", lambda: True)

    issue = generate_repository_newsletter_issue(
        repository,
        cadence=NewsletterCadence.WEEKLY,
        period=NewsletterPeriod(start=date(2026, 5, 25), end=date(2026, 5, 31)),
    )

    assert issue is None
    assert RepositoryNewsletterIssue.objects.count() == 0


@pytest.mark.django_db
def test_generate_repository_newsletter_issue_retries_after_generation_error(
    repository,
    monkeypatch,
):
    repository.newsletter_tracking_enabled = True
    repository.save(update_fields=["newsletter_tracking_enabled"])
    RepositoryCommit.objects.create(
        repository=repository,
        sha="abc123",
        branch="main",
        message="Add newsletter tracking",
        summary="Added newsletter tracking.",
        summary_source_hash="summary-hash",
        committed_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
        html_url="https://github.com/django/django/commit/abc123",
    )
    calls = {"count": 0}
    monkeypatch.setattr("apps.repos.newsletters.newsletter_ai_configured", lambda: True)
    monkeypatch.setattr("apps.repos.newsletters.newsletter_model_id", lambda: "model/test")

    def generate(text):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError("temporary failure")
        return NewsletterIssueOutput(
            title="Django weekly update",
            content_markdown="## Changes\n- Added tracking.",
        )

    monkeypatch.setattr("apps.repos.newsletters.generate_issue_content", generate)
    period = NewsletterPeriod(start=date(2026, 5, 25), end=date(2026, 5, 31))

    failed_issue = generate_repository_newsletter_issue(
        repository,
        cadence=NewsletterCadence.WEEKLY,
        period=period,
    )
    retried_issue = generate_repository_newsletter_issue(
        repository,
        cadence=NewsletterCadence.WEEKLY,
        period=period,
    )

    assert failed_issue is not None
    assert retried_issue is not None
    assert retried_issue.id == failed_issue.id
    assert calls["count"] == 2
    assert retried_issue.generation_last_error == ""
    assert retried_issue.published_at is not None
    assert "<h2>Changes</h2>" in retried_issue.content_html


@pytest.mark.django_db
def test_generate_repository_newsletter_issue_retries_after_ai_configuration_returns(
    repository,
    monkeypatch,
):
    repository.newsletter_tracking_enabled = True
    repository.save(update_fields=["newsletter_tracking_enabled"])
    RepositoryCommit.objects.create(
        repository=repository,
        sha="abc123",
        branch="main",
        message="Add newsletter tracking",
        summary="Added newsletter tracking.",
        summary_source_hash="summary-hash",
        committed_at=datetime(2026, 5, 26, 12, tzinfo=UTC),
        html_url="https://github.com/django/django/commit/abc123",
    )
    configured = {"enabled": False}
    monkeypatch.setattr(
        "apps.repos.newsletters.newsletter_ai_configured",
        lambda: configured["enabled"],
    )
    monkeypatch.setattr("apps.repos.newsletters.newsletter_model_id", lambda: "model/test")
    monkeypatch.setattr(
        "apps.repos.newsletters.generate_issue_content",
        lambda text: NewsletterIssueOutput(
            title="Django weekly update",
            content_markdown="## Changes\n- Added tracking.",
        ),
    )
    period = NewsletterPeriod(start=date(2026, 5, 25), end=date(2026, 5, 31))

    unconfigured_issue = generate_repository_newsletter_issue(
        repository,
        cadence=NewsletterCadence.WEEKLY,
        period=period,
    )
    configured["enabled"] = True
    retried_issue = generate_repository_newsletter_issue(
        repository,
        cadence=NewsletterCadence.WEEKLY,
        period=period,
    )

    assert unconfigured_issue is not None
    assert retried_issue is not None
    assert retried_issue.id == unconfigured_issue.id
    assert retried_issue.generation_last_error == ""
    assert retried_issue.published_at is not None
    assert "<h2>Changes</h2>" in retried_issue.content_html


@pytest.mark.django_db
def test_public_newsletter_pages_and_rss_render(client, repository):
    issue = RepositoryNewsletterIssue.objects.create(
        repository=repository,
        cadence=NewsletterCadence.WEEKLY,
        period_start=date(2026, 5, 25),
        period_end=date(2026, 5, 31),
        slug="2026-05-25",
        title="Django weekly update",
        content_markdown="## Changes\n- Added tracking.",
        content_html="<h2>Changes</h2><ul><li>Added tracking.</li></ul>",
        commit_count=1,
        published_at=datetime(2026, 6, 1, 4, tzinfo=UTC),
    )

    list_response = client.get(
        reverse(
            "repos:newsletter_issue_list",
            kwargs={"owner": repository.owner, "name": repository.name},
        )
    )
    detail_response = client.get(issue.get_absolute_url())
    feed_response = client.get(
        reverse(
            "repos:newsletter_feed",
            kwargs={
                "owner": repository.owner,
                "name": repository.name,
                "cadence": NewsletterCadence.WEEKLY,
            },
        )
    )

    assert list_response.status_code == 200
    assert b"Django weekly update" in list_response.content
    assert detail_response.status_code == 200
    assert b"Added tracking." in detail_response.content
    assert feed_response.status_code == 200
    assert b"Django weekly update" in feed_response.content


@pytest.mark.django_db
def test_send_issue_to_subscribers_is_idempotent_and_unsubscribe_blocks_future_sends(
    user,
    repository,
    monkeypatch,
):
    subscription = NewsletterSubscription.objects.create(
        user=user,
        repository=repository,
        email="reader@example.com",
        cadence=NewsletterCadence.WEEKLY,
    )
    issue = RepositoryNewsletterIssue.objects.create(
        repository=repository,
        cadence=NewsletterCadence.WEEKLY,
        period_start=date(2026, 5, 25),
        period_end=date(2026, 5, 31),
        slug="2026-05-25",
        title="Django weekly update",
        content_markdown="Update",
        content_html="<p>Update</p>",
        commit_count=1,
        published_at=timezone.now(),
    )
    sent = []
    monkeypatch.setattr(
        "apps.repos.newsletters.send_transactional_email",
        lambda send_callable, **kwargs: sent.append(kwargs["email_address"]) or True,
    )

    first = send_issue_to_subscribers(issue)
    second = send_issue_to_subscribers(issue)
    unsubscribe_newsletter(subscription)
    future_issue = RepositoryNewsletterIssue.objects.create(
        repository=repository,
        cadence=NewsletterCadence.WEEKLY,
        period_start=date(2026, 6, 1),
        period_end=date(2026, 6, 7),
        slug="2026-06-01",
        title="Django weekly update 2",
        content_markdown="Update",
        content_html="<p>Update</p>",
        commit_count=1,
        published_at=timezone.now(),
    )
    third = send_issue_to_subscribers(future_issue)

    assert first["sent"] == 1
    assert second["sent"] == 0
    assert third["sent"] == 0
    assert sent == ["reader@example.com"]
    assert NewsletterIssueDelivery.objects.count() == 1


@pytest.mark.django_db
def test_send_issue_to_subscribers_skips_delivery_locked_by_another_worker(
    user,
    repository,
    monkeypatch,
):
    NewsletterSubscription.objects.create(
        user=user,
        repository=repository,
        email="reader@example.com",
        cadence=NewsletterCadence.WEEKLY,
    )
    issue = RepositoryNewsletterIssue.objects.create(
        repository=repository,
        cadence=NewsletterCadence.WEEKLY,
        period_start=date(2026, 5, 25),
        period_end=date(2026, 5, 31),
        slug="2026-05-25",
        title="Django weekly update",
        content_markdown="Update",
        content_html="<p>Update</p>",
        commit_count=1,
        published_at=timezone.now(),
    )
    monkeypatch.setattr("apps.repos.newsletters.send_issue_delivery", lambda *args, **kwargs: None)

    result = send_issue_to_subscribers(issue)

    assert result == {"sent": 0, "failed": 0}
    assert NewsletterIssueDelivery.objects.filter(sent_at__isnull=True).count() == 1


@pytest.mark.django_db
def test_send_issue_to_subscribers_recovers_from_concurrent_delivery_create(
    user,
    repository,
    monkeypatch,
):
    subscription = NewsletterSubscription.objects.create(
        user=user,
        repository=repository,
        email="reader@example.com",
        cadence=NewsletterCadence.WEEKLY,
    )
    issue = RepositoryNewsletterIssue.objects.create(
        repository=repository,
        cadence=NewsletterCadence.WEEKLY,
        period_start=date(2026, 5, 25),
        period_end=date(2026, 5, 31),
        slug="2026-05-25",
        title="Django weekly update",
        content_markdown="Update",
        content_html="<p>Update</p>",
        commit_count=1,
        published_at=timezone.now(),
    )
    NewsletterIssueDelivery.objects.create(
        issue=issue,
        subscription=subscription,
        recipient_email=subscription.email,
    )
    sent = []
    monkeypatch.setattr(
        "apps.repos.newsletters.NewsletterIssueDelivery.objects.get_or_create",
        lambda *args, **kwargs: (_ for _ in ()).throw(IntegrityError("duplicate")),
    )
    monkeypatch.setattr(
        "apps.repos.newsletters.send_transactional_email",
        lambda send_callable, **kwargs: sent.append(kwargs["email_address"]) or True,
    )

    result = send_issue_to_subscribers(issue)

    assert result == {"sent": 1, "failed": 0}
    assert sent == ["reader@example.com"]
    assert NewsletterIssueDelivery.objects.get().sent_at is not None


@pytest.mark.django_db
def test_unsubscribe_route_marks_subscription_inactive(client, user, repository):
    subscription = NewsletterSubscription.objects.create(
        user=user,
        repository=repository,
        email="reader@example.com",
        cadence=NewsletterCadence.WEEKLY,
    )

    response = client.post(
        reverse(
            "repos:newsletter_unsubscribe",
            kwargs={"token": subscription.unsubscribe_token},
        )
    )

    subscription.refresh_from_db()
    assert response.status_code == 302
    assert subscription.is_active is False
    assert subscription.unsubscribed_at is not None


def test_render_newsletter_markdown_allows_safe_links_and_escapes_raw_html():
    rendered = render_newsletter_markdown(
        "## Heading\n<script>alert(1)</script>\n"
        "[unsafe](javascript:alert(1))\n[safe](https://example.com)"
    )

    assert "<script>" not in rendered
    assert "javascript:alert" not in rendered
    assert 'href="https://example.com"' in rendered


def test_render_newsletter_markdown_preserves_blockquotes_and_query_links():
    rendered = render_newsletter_markdown(
        "> Important release note\n\n"
        "[filtered changes](https://example.com/changes?repo=django&cadence=weekly)"
    )

    assert "<blockquote>" in rendered
    assert "Important release note" in rendered
    assert 'href="https://example.com/changes?repo=django&amp;cadence=weekly"' in rendered
    assert "&amp;amp;" not in rendered


@override_settings(
    OPENROUTER_API_KEY="test-key",
    OPENROUTER_BASE_URL="https://openrouter.example/api/v1",
    SUPPORTED_AI_MODELS={"openrouter": {"newsletter": "google/gemini-2.5-flash-lite"}},
)
def test_build_model_supports_openrouter_provider():
    model = build_model(provider="openrouter", label="newsletter")

    assert isinstance(model, OpenAIChatModel)
