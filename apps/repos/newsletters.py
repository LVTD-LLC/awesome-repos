from __future__ import annotations

import hashlib
import html
import os
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from html.parser import HTMLParser
from urllib.parse import urlencode

import markdown as md
from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from apps.core.agents.base import build_model
from apps.core.choices import EmailType
from apps.core.utils import send_transactional_email
from apps.repos.models import (
    NewsletterCadence,
    NewsletterIssueDelivery,
    NewsletterSubscription,
    Repository,
    RepositoryCommit,
    RepositoryNewsletterIssue,
)
from apps.repos.services import (
    dt,
    fetch_json,
    fetch_json_page,
    github_rate_limit_remaining,
    github_rate_limit_status,
    is_github_rate_limit_error,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

SAFE_TAGS = {
    "a",
    "blockquote",
    "br",
    "code",
    "em",
    "h1",
    "h2",
    "h3",
    "h4",
    "li",
    "ol",
    "p",
    "pre",
    "strong",
    "table",
    "tbody",
    "td",
    "th",
    "thead",
    "tr",
    "ul",
}


@dataclass(frozen=True, slots=True)
class NewsletterPeriod:
    start: date
    end: date


class CommitSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="A concise, developer-facing summary of the commit.")


class NewsletterIssueOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(description="A concise newsletter title.")
    content_markdown: str = Field(description="Markdown newsletter body.")


class SafeHTMLRenderer(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=False)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in SAFE_TAGS:
            return
        if tag == "a":
            href = ""
            for attr_name, attr_value in attrs:
                if attr_name == "href":
                    href = attr_value or ""
                    break
            if href.startswith(("https://", "http://", "mailto:")):
                self.parts.append(
                    '<a href="'
                    + html.escape(href, quote=True)
                    + '" rel="noopener noreferrer">'
                )
            else:
                self.parts.append("<a>")
            return
        self.parts.append(f"<{tag}>")

    def handle_endtag(self, tag):
        if tag in SAFE_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data):
        self.parts.append(html.escape(data))

    def handle_entityref(self, name):
        self.parts.append(f"&{name};")

    def handle_charref(self, name):
        self.parts.append(f"&#{name};")

    def html(self) -> str:
        return "".join(self.parts)


def newsletter_model_id() -> str:
    supported: dict[str, dict[str, str]] = settings.SUPPORTED_AI_MODELS
    provider = settings.NEWSLETTER_AI_PROVIDER
    label = settings.NEWSLETTER_AI_MODEL_LABEL
    return f"{provider}/{supported[provider][label]}"


def newsletter_ai_configured() -> bool:
    if settings.NEWSLETTER_AI_PROVIDER == "openrouter":
        return bool(settings.OPENROUTER_API_KEY)
    provider_env_keys = {
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    }
    return any(
        os.environ.get(key)
        for key in provider_env_keys.get(settings.NEWSLETTER_AI_PROVIDER, ())
    )


def _newsletter_agent(output_type, instructions: str):
    model = build_model(
        provider=settings.NEWSLETTER_AI_PROVIDER,
        label=settings.NEWSLETTER_AI_MODEL_LABEL,
    )
    return Agent(model, output_type=output_type, instructions=instructions)


def render_newsletter_markdown(markdown_text: str) -> str:
    rendered = md.Markdown(extensions=["tables"]).convert(markdown_text or "")
    parser = SafeHTMLRenderer()
    parser.feed(rendered)
    return parser.html()


def enable_repository_newsletter_tracking(repository: Repository) -> Repository:
    if repository.newsletter_tracking_enabled:
        return repository
    now = timezone.now()
    repository.newsletter_tracking_enabled = True
    repository.newsletter_tracking_started_at = repository.newsletter_tracking_started_at or now
    repository.newsletter_tracking_last_error = ""
    repository.save(
        update_fields=[
            "newsletter_tracking_enabled",
            "newsletter_tracking_started_at",
            "newsletter_tracking_last_error",
            "updated_at",
        ]
    )
    return repository


def disable_repository_newsletter_tracking(repository: Repository) -> Repository:
    repository.newsletter_tracking_enabled = False
    repository.save(update_fields=["newsletter_tracking_enabled", "updated_at"])
    return repository


@transaction.atomic
def upsert_newsletter_subscription(
    *,
    user,
    repository: Repository,
    email: str,
    cadence: str,
) -> NewsletterSubscription:
    if cadence not in NewsletterCadence.values:
        raise ValueError("Unsupported newsletter cadence.")
    repository = Repository.objects.select_for_update().get(pk=repository.pk)
    enable_repository_newsletter_tracking(repository)
    normalized_email = email.strip().lower()
    subscription = (
        NewsletterSubscription.objects.select_for_update()
        .filter(user=user, repository=repository, is_active=True)
        .first()
    )
    if subscription is None:
        try:
            with transaction.atomic():
                subscription = NewsletterSubscription.objects.create(
                    user=user,
                    repository=repository,
                    email=normalized_email,
                    cadence=cadence,
                )
        except IntegrityError:
            subscription = (
                NewsletterSubscription.objects.select_for_update()
                .filter(user=user, repository=repository, is_active=True)
                .first()
            )
            if subscription is None:
                raise
            subscription.email = normalized_email
            subscription.cadence = cadence
            subscription.unsubscribed_at = None
            subscription.save(
                update_fields=["email", "cadence", "unsubscribed_at", "updated_at"]
            )
    else:
        subscription.email = normalized_email
        subscription.cadence = cadence
        subscription.unsubscribed_at = None
        subscription.save(update_fields=["email", "cadence", "unsubscribed_at", "updated_at"])
    return subscription


def unsubscribe_newsletter(subscription: NewsletterSubscription) -> NewsletterSubscription:
    if not subscription.is_active:
        return subscription
    subscription.is_active = False
    subscription.unsubscribed_at = timezone.now()
    subscription.save(update_fields=["is_active", "unsubscribed_at", "updated_at"])
    return subscription


def _isoformat(value) -> str:
    if value is None:
        value = timezone.now()
    if timezone.is_naive(value):
        value = timezone.make_aware(value)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _commit_since(repository: Repository):
    if repository.newsletter_tracking_last_polled_at:
        return repository.newsletter_tracking_last_polled_at - timedelta(
            hours=max(0, settings.NEWSLETTER_COMMIT_POLL_OVERLAP_HOURS)
        )
    return repository.newsletter_tracking_started_at or timezone.now()


def _github_rate_limit_budget_exhausted() -> bool:
    min_remaining = settings.NEWSLETTER_GITHUB_MIN_RATE_LIMIT_REMAINING
    if min_remaining <= 0:
        return False
    if github_rate_limit_remaining() is None:
        github_rate_limit_status()
    remaining = github_rate_limit_remaining()
    return remaining is not None and remaining <= min_remaining


def fetch_repository_commit_page(
    repository: Repository,
    *,
    branch: str,
    since,
    page: int,
) -> tuple[list[dict], str]:
    query = urlencode(
        {
            "sha": branch,
            "since": _isoformat(since),
            "per_page": 100,
            "page": page,
        }
    )
    data, link_header = fetch_json_page(
        f"https://api.github.com/repos/{repository.full_name}/commits?{query}"
    )
    if not isinstance(data, list):
        raise RuntimeError("GitHub returned an unexpected commits response.")
    return data, link_header


def fetch_repository_commit_detail(repository: Repository, sha: str) -> dict:
    data = fetch_json(f"https://api.github.com/repos/{repository.full_name}/commits/{sha}")
    if not isinstance(data, dict):
        raise RuntimeError("GitHub returned an unexpected commit detail response.")
    return data


def _compact_github_user(value: dict | None) -> dict:
    value = value or {}
    return {
        "login": value.get("login") or "",
        "id": value.get("id"),
        "html_url": value.get("html_url") or "",
        "avatar_url": value.get("avatar_url") or "",
    }


def _bounded_commit_files(files: list[dict]) -> tuple[list[dict], bool]:
    bounded_files = []
    total_patch_chars = 0
    any_truncated = False
    file_patch_limit = max(0, settings.NEWSLETTER_COMMIT_FILE_PATCH_MAX_CHARS)
    total_patch_limit = max(0, settings.NEWSLETTER_COMMIT_TOTAL_PATCH_MAX_CHARS)

    for file_data in files:
        patch = file_data.get("patch") or ""
        patch_truncated = False
        if file_patch_limit and len(patch) > file_patch_limit:
            patch = patch[:file_patch_limit]
            patch_truncated = True
        if total_patch_limit and total_patch_chars + len(patch) > total_patch_limit:
            remaining_chars = max(0, total_patch_limit - total_patch_chars)
            patch = patch[:remaining_chars]
            patch_truncated = True
        total_patch_chars += len(patch)
        any_truncated = any_truncated or patch_truncated
        bounded_files.append(
            {
                "filename": file_data.get("filename") or "",
                "status": file_data.get("status") or "",
                "additions": file_data.get("additions") or 0,
                "deletions": file_data.get("deletions") or 0,
                "changes": file_data.get("changes") or 0,
                "blob_url": file_data.get("blob_url") or "",
                "raw_url": file_data.get("raw_url") or "",
                "patch": patch,
                "patch_truncated": patch_truncated,
            }
        )
    return bounded_files, any_truncated


def _commit_source_hash(commit: RepositoryCommit) -> str:
    payload = {
        "sha": commit.sha,
        "message": commit.message,
        "files": commit.files,
        "additions": commit.additions,
        "deletions": commit.deletions,
        "changed_files": commit.changed_files,
    }
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def save_repository_commit_detail(
    repository: Repository,
    *,
    branch: str,
    detail: dict,
) -> tuple[RepositoryCommit, bool]:
    commit_data = detail.get("commit") or {}
    author_data = commit_data.get("author") or {}
    committer_data = commit_data.get("committer") or {}
    stats = detail.get("stats") or {}
    files, patch_truncated = _bounded_commit_files(detail.get("files") or [])
    sha = detail.get("sha") or ""
    defaults = {
        "branch": branch,
        "message": commit_data.get("message") or "",
        "html_url": detail.get("html_url") or "",
        "api_url": detail.get("url") or "",
        "author_name": author_data.get("name") or "",
        "author_email": author_data.get("email") or "",
        "author_login": (detail.get("author") or {}).get("login") or "",
        "authored_at": dt(author_data.get("date")),
        "committer_name": committer_data.get("name") or "",
        "committer_email": committer_data.get("email") or "",
        "committer_login": (detail.get("committer") or {}).get("login") or "",
        "committed_at": dt(committer_data.get("date")),
        "parent_shas": [parent.get("sha") for parent in detail.get("parents") or []],
        "additions": stats.get("additions") or 0,
        "deletions": stats.get("deletions") or 0,
        "changed_files": len(files),
        "files": files,
        "patch_truncated": patch_truncated,
        "raw_metadata": {
            "sha": sha,
            "node_id": detail.get("node_id") or "",
            "html_url": detail.get("html_url") or "",
            "comments_url": detail.get("comments_url") or "",
            "author": _compact_github_user(detail.get("author")),
            "committer": _compact_github_user(detail.get("committer")),
            "stats": stats,
        },
    }
    commit, created = RepositoryCommit.objects.update_or_create(
        repository=repository,
        sha=sha,
        defaults=defaults,
    )
    source_hash = _commit_source_hash(commit)
    if commit.summary and commit.summary_source_hash != source_hash:
        commit.summary = ""
        commit.summary_source_hash = ""
        commit.summarized_at = None
        commit.save(
            update_fields=[
                "summary",
                "summary_source_hash",
                "summarized_at",
                "updated_at",
            ]
        )
    return commit, created


def _save_polled_commit_item(
    repository: Repository,
    *,
    branch: str,
    item: dict,
    started_at,
) -> bool | None:
    sha = item.get("sha") or ""
    if not sha:
        return None
    detail = fetch_repository_commit_detail(repository, sha)
    committed_at = dt(((detail.get("commit") or {}).get("committer") or {}).get("date"))
    if committed_at and committed_at < started_at:
        return None
    _commit, was_created = save_repository_commit_detail(
        repository,
        branch=branch,
        detail=detail,
    )
    return was_created


def poll_repository_commits(repository: Repository) -> dict:
    if not repository.newsletter_tracking_enabled:
        return {"repository_id": repository.id, "skipped": "tracking_disabled", "saved": 0}
    if _github_rate_limit_budget_exhausted():
        return {"repository_id": repository.id, "skipped": "github_rate_limit_budget", "saved": 0}

    branch = repository.default_branch or "main"
    since = _commit_since(repository)
    started_at = repository.newsletter_tracking_started_at or since
    saved = 0
    created = 0
    page = 1

    try:
        while True:
            commits, link_header = fetch_repository_commit_page(
                repository,
                branch=branch,
                since=since,
                page=page,
            )
            for item in commits:
                if _github_rate_limit_budget_exhausted():
                    return {
                        "repository_id": repository.id,
                        "skipped": "github_rate_limit_budget",
                        "saved": saved,
                        "created": created,
                    }
                was_created = _save_polled_commit_item(
                    repository,
                    branch=branch,
                    item=item,
                    started_at=started_at,
                )
                if was_created is None:
                    continue
                saved += 1
                created += int(was_created)
            if len(commits) < 100 or 'rel="next"' not in link_header:
                break
            page += 1
    except Exception as exc:
        repository.newsletter_tracking_last_error = str(exc)
        repository.save(update_fields=["newsletter_tracking_last_error", "updated_at"])
        if is_github_rate_limit_error(exc):
            logger.warning(
                "repository_newsletter_commit_poll_rate_limited",
                repository_id=repository.id,
                repository_full_name=repository.full_name,
                error=str(exc),
            )
            return {
                "repository_id": repository.id,
                "stopped_for_rate_limit": True,
                "saved": saved,
                "created": created,
            }
        logger.error(
            "repository_newsletter_commit_poll_failed",
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            error=str(exc),
            exc_info=True,
        )
        raise

    repository.newsletter_tracking_last_polled_at = timezone.now()
    repository.newsletter_tracking_last_error = ""
    repository.save(
        update_fields=[
            "newsletter_tracking_last_polled_at",
            "newsletter_tracking_last_error",
            "updated_at",
        ]
    )
    return {"repository_id": repository.id, "saved": saved, "created": created}


def build_commit_summary_text(commit: RepositoryCommit) -> str:
    file_sections = []
    for file_data in commit.files:
        patch = file_data.get("patch") or ""
        file_sections.append(
            "\n".join(
                [
                    f"File: {file_data.get('filename')}",
                    f"Status: {file_data.get('status')}",
                    f"Stats: +{file_data.get('additions')} -{file_data.get('deletions')}",
                    f"Patch:\n{patch}" if patch else "Patch: not available",
                ]
            )
        )
    text = "\n\n".join(
        [
            f"Repository: {commit.repository.full_name}",
            f"Commit: {commit.sha}",
            f"Author: {commit.author_name or commit.author_login}",
            f"Message:\n{commit.message}",
            f"Stats: +{commit.additions} -{commit.deletions}, {commit.changed_files} files",
            "Files:\n" + "\n\n".join(file_sections),
        ]
    )
    max_chars = max(0, settings.NEWSLETTER_COMMIT_SUMMARY_MAX_CHARS)
    return text[:max_chars] if max_chars else text


def generate_commit_summary(text: str) -> str:
    result = _newsletter_agent(
        CommitSummaryOutput,
        instructions=(
            "Summarize a GitHub commit for a repository change newsletter. Focus on "
            "what changed, why a developer might care, and concrete product/API impact. "
            "Do not exaggerate or infer unrevealed intent."
        ),
    ).run_sync(
        "Return a concise structured commit summary for this commit.\n\n" + text
    )
    return result.output.summary.strip()


def summarize_commit(commit: RepositoryCommit, *, force: bool = False) -> RepositoryCommit:
    source_hash = _commit_source_hash(commit)
    if (
        not force
        and commit.summary
        and commit.summary_source_hash == source_hash
        and not commit.summary_last_error
    ):
        return commit
    if not newsletter_ai_configured():
        commit.summary_last_error = "Newsletter AI is not configured."
        commit.save(update_fields=["summary_last_error", "updated_at"])
        return commit
    try:
        commit.summary = generate_commit_summary(build_commit_summary_text(commit))
        commit.summary_model = newsletter_model_id()
        commit.summary_source_hash = source_hash
        commit.summarized_at = timezone.now()
        commit.summary_last_error = ""
        commit.save(
            update_fields=[
                "summary",
                "summary_model",
                "summary_source_hash",
                "summarized_at",
                "summary_last_error",
                "updated_at",
            ]
        )
    except Exception as exc:
        commit.summary_last_error = str(exc)
        commit.save(update_fields=["summary_last_error", "updated_at"])
        logger.warning(
            "repository_newsletter_commit_summary_failed",
            commit_id=commit.id,
            repository_full_name=commit.repository.full_name,
            error=str(exc),
            exc_info=True,
        )
    return commit


def summarize_pending_commits(limit: int | None = None) -> dict:
    resolved_limit = settings.NEWSLETTER_COMMIT_SUMMARY_LIMIT if limit is None else limit
    queryset = (
        RepositoryCommit.objects.filter(summary="", repository__newsletter_tracking_enabled=True)
        .order_by("committed_at", "id")
        .select_related("repository")
    )
    if resolved_limit is not None:
        queryset = queryset[: max(0, resolved_limit)]

    summarized = 0
    for commit in queryset:
        before = bool(commit.summary)
        summarize_commit(commit)
        summarized += int(not before and bool(commit.summary))
    return {"summarized": summarized, "limit": resolved_limit}


def previous_week_period(reference_date: date | None = None) -> NewsletterPeriod:
    today = reference_date or timezone.localdate()
    current_week_start = today - timedelta(days=today.weekday())
    start = current_week_start - timedelta(days=7)
    return NewsletterPeriod(start=start, end=current_week_start - timedelta(days=1))


def previous_month_period(reference_date: date | None = None) -> NewsletterPeriod:
    today = reference_date or timezone.localdate()
    first_this_month = today.replace(day=1)
    last_previous_month = first_this_month - timedelta(days=1)
    return NewsletterPeriod(
        start=last_previous_month.replace(day=1),
        end=last_previous_month,
    )


def newsletter_period_for_cadence(
    cadence: str,
    reference_date: date | None = None,
) -> NewsletterPeriod:
    if cadence == NewsletterCadence.WEEKLY:
        return previous_week_period(reference_date)
    if cadence == NewsletterCadence.MONTHLY:
        return previous_month_period(reference_date)
    raise ValueError("Unsupported newsletter cadence.")


def issue_slug(cadence: str, period: NewsletterPeriod) -> str:
    if cadence == NewsletterCadence.MONTHLY:
        return f"{period.start:%Y-%m}"
    return f"{period.start:%Y-%m-%d}"


def _issue_source_hash(commits: list[RepositoryCommit]) -> str:
    payload = [
        {
            "sha": commit.sha,
            "summary": commit.summary,
            "summary_source_hash": commit.summary_source_hash,
            "message": commit.message,
        }
        for commit in commits
    ]
    return hashlib.sha256(repr(payload).encode("utf-8")).hexdigest()


def build_issue_generation_text(
    repository: Repository,
    *,
    cadence: str,
    period: NewsletterPeriod,
    commits: list[RepositoryCommit],
) -> str:
    sections = [
        f"Repository: {repository.full_name}",
        f"Cadence: {cadence}",
        f"Period: {period.start.isoformat()} to {period.end.isoformat()}",
        "Commits:",
    ]
    for commit in commits[: settings.NEWSLETTER_ISSUE_GENERATION_MAX_COMMITS]:
        summary = commit.summary or commit.message
        sections.append(
            "\n".join(
                [
                    f"- SHA: {commit.sha}",
                    f"  URL: {commit.html_url}",
                    f"  Author: {commit.author_name or commit.author_login}",
                    f"  Committed: {commit.committed_at}",
                    (
                        f"  Stats: +{commit.additions} -{commit.deletions}, "
                        f"{commit.changed_files} files"
                    ),
                    f"  Summary: {summary}",
                ]
            )
        )
    text = "\n".join(sections)
    max_chars = max(0, settings.NEWSLETTER_ISSUE_GENERATION_MAX_CHARS)
    return text[:max_chars] if max_chars else text


def generate_issue_content(text: str) -> NewsletterIssueOutput:
    result = _newsletter_agent(
        NewsletterIssueOutput,
        instructions=(
            "Write a concise public repository change newsletter in Markdown. Include a short "
            "opening summary and grouped bullets for meaningful changes. Mention commit SHAs "
            "or URLs only when they add useful traceability. Do not invent changes."
        ),
    ).run_sync("Create a repository newsletter issue from these commit summaries.\n\n" + text)
    return result.output


def _commit_queryset_for_period(
    repository: Repository,
    *,
    period: NewsletterPeriod,
):
    return repository.newsletter_commits.filter(
        committed_at__date__gte=period.start,
        committed_at__date__lte=period.end,
    ).order_by("committed_at", "id")


def generate_repository_newsletter_issue(
    repository: Repository,
    *,
    cadence: str,
    period: NewsletterPeriod,
    force: bool = False,
) -> RepositoryNewsletterIssue | None:
    commits = list(
        _commit_queryset_for_period(repository, period=period).select_related("repository")
    )
    if not commits:
        return None

    source_hash = _issue_source_hash(commits)
    slug = issue_slug(cadence, period)
    existing = RepositoryNewsletterIssue.objects.filter(
        repository=repository,
        cadence=cadence,
        period_start=period.start,
    ).first()
    if existing and not force and existing.generation_source_hash == source_hash:
        return existing
    if not newsletter_ai_configured():
        issue, _created = RepositoryNewsletterIssue.objects.update_or_create(
            repository=repository,
            cadence=cadence,
            period_start=period.start,
            defaults={
                "period_end": period.end,
                "slug": slug,
                "title": f"{repository.full_name} {cadence} update",
                "commit_count": len(commits),
                "generation_source_hash": source_hash,
                "generation_last_error": "Newsletter AI is not configured.",
            },
        )
        return issue

    try:
        generated = generate_issue_content(
            build_issue_generation_text(
                repository,
                cadence=cadence,
                period=period,
                commits=commits,
            )
        )
        content_markdown = generated.content_markdown.strip()
        content_html = render_newsletter_markdown(content_markdown)
        issue, _created = RepositoryNewsletterIssue.objects.update_or_create(
            repository=repository,
            cadence=cadence,
            period_start=period.start,
            defaults={
                "period_end": period.end,
                "slug": slug,
                "title": generated.title.strip()[:255]
                or f"{repository.full_name} {cadence} update",
                "content_markdown": content_markdown,
                "content_html": content_html,
                "commit_count": len(commits),
                "published_at": timezone.now(),
                "generation_model": newsletter_model_id(),
                "generation_source_hash": source_hash,
                "generated_at": timezone.now(),
                "generation_last_error": "",
            },
        )
        return issue
    except Exception as exc:
        issue, _created = RepositoryNewsletterIssue.objects.update_or_create(
            repository=repository,
            cadence=cadence,
            period_start=period.start,
            defaults={
                "period_end": period.end,
                "slug": slug,
                "title": f"{repository.full_name} {cadence} update",
                "commit_count": len(commits),
                "generation_source_hash": source_hash,
                "generation_last_error": str(exc),
            },
        )
        logger.warning(
            "repository_newsletter_issue_generation_failed",
            repository_id=repository.id,
            repository_full_name=repository.full_name,
            cadence=cadence,
            period_start=period.start.isoformat(),
            error=str(exc),
            exc_info=True,
        )
        return issue


def absolute_site_url(path: str) -> str:
    return f"{settings.SITE_URL.rstrip('/')}{path}"


def _send_locked_issue_delivery(delivery: NewsletterIssueDelivery) -> bool:
    issue = delivery.issue
    subscription = delivery.subscription
    context = {
        "issue": issue,
        "repository": issue.repository,
        "issue_url": absolute_site_url(issue.get_absolute_url()),
        "unsubscribe_url": absolute_site_url(subscription.unsubscribe_url()),
    }
    subject = issue.title
    text_body = render_to_string("repos/email/newsletter_issue.txt", context)
    html_body = render_to_string("repos/email/newsletter_issue.html", context)
    message = EmailMultiAlternatives(
        subject=subject,
        body=text_body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[delivery.recipient_email],
    )
    message.attach_alternative(html_body, "text/html")
    profile = getattr(subscription.user, "profile", None)
    success = send_transactional_email(
        lambda: message.send(),
        email_address=delivery.recipient_email,
        email_type=EmailType.NEWSLETTER,
        profile=profile,
        context={
            "issue_id": issue.id,
            "subscription_id": subscription.id,
            "repository_id": issue.repository_id,
        },
    )
    if success:
        delivery.sent_at = timezone.now()
        delivery.last_error = ""
        delivery.save(update_fields=["sent_at", "last_error", "updated_at"])
    else:
        delivery.last_error = "Email provider returned failure."
        delivery.save(update_fields=["last_error", "updated_at"])
    return success


def send_issue_delivery(
    delivery: NewsletterIssueDelivery,
    *,
    recipient_email: str | None = None,
) -> bool | None:
    with transaction.atomic():
        locked_delivery = (
            NewsletterIssueDelivery.objects.select_for_update(skip_locked=True)
            .select_related("issue__repository", "subscription__user")
            .filter(pk=delivery.pk)
            .first()
        )
        if locked_delivery is None:
            return None
        if locked_delivery.sent_at:
            return None
        if recipient_email and locked_delivery.recipient_email != recipient_email:
            locked_delivery.recipient_email = recipient_email
            locked_delivery.save(update_fields=["recipient_email", "updated_at"])
        return _send_locked_issue_delivery(locked_delivery)


def send_issue_to_subscribers(issue: RepositoryNewsletterIssue) -> dict:
    if not issue.published_at:
        return {"sent": 0, "skipped": "unpublished"}
    subscriptions = NewsletterSubscription.objects.filter(
        repository=issue.repository,
        cadence=issue.cadence,
        is_active=True,
    ).select_related("user", "repository")
    sent = 0
    failed = 0
    for subscription in subscriptions:
        delivery, _created = NewsletterIssueDelivery.objects.get_or_create(
            issue=issue,
            subscription=subscription,
            defaults={"recipient_email": subscription.email},
        )
        if delivery.sent_at:
            continue
        result = send_issue_delivery(delivery, recipient_email=subscription.email)
        if result is True:
            sent += 1
        elif result is False:
            failed += 1
    return {"sent": sent, "failed": failed}


def generate_due_newsletter_issues(
    *,
    cadence: str,
    reference_date: date | None = None,
) -> dict:
    period = newsletter_period_for_cadence(cadence, reference_date)
    generated = 0
    sent = 0
    failed = 0
    skipped = 0
    repositories = Repository.objects.filter(newsletter_tracking_enabled=True).order_by(
        "full_name"
    )
    for repository in repositories:
        issue = generate_repository_newsletter_issue(
            repository,
            cadence=cadence,
            period=period,
        )
        if issue is None:
            skipped += 1
            continue
        generated += int(bool(issue.published_at))
        delivery_result = send_issue_to_subscribers(issue)
        sent += delivery_result.get("sent", 0)
        failed += delivery_result.get("failed", 0)
    return {
        "cadence": cadence,
        "period_start": period.start.isoformat(),
        "period_end": period.end.isoformat(),
        "generated": generated,
        "skipped": skipped,
        "sent": sent,
        "failed": failed,
    }


def poll_tracked_repositories(limit: int | None = None) -> dict:
    resolved_limit = settings.NEWSLETTER_COMMIT_POLL_LIMIT if limit is None else limit
    repositories = Repository.objects.filter(newsletter_tracking_enabled=True).order_by(
        "newsletter_tracking_last_polled_at",
        "full_name",
    )
    if resolved_limit is not None:
        repositories = repositories[: max(0, resolved_limit)]
    results = [poll_repository_commits(repository) for repository in repositories]
    return {
        "polled": len(results),
        "saved": sum(result.get("saved", 0) for result in results),
        "created": sum(result.get("created", 0) for result in results),
        "results": results[:25],
    }
