from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import models, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from pgvector.django import CosineDistance

from apps.repos.embeddings import (
    generate_embedding,
    repository_embeddings_configured,
    sync_repository_embedding,
)
from apps.repos.models import (
    REPOSITORY_EMBEDDING_DIMENSIONS,
    AwesomeList,
    AwesomeListItem,
    Repository,
    RepositorySnapshot,
)
from apps.repos.tags import (
    normalize_repository_tag,
    repository_tagging_configured,
    sync_repository_tags,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)
# Process-local snapshot from the most recent GitHub response. Treat this as a
# best-effort hint; callers must still handle actual 403/429 responses.
_github_rate_limit_state: dict[str, str] = {}
GITHUB_API_VERSION = "2026-03-10"

GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?][^\s)\]>'\"]*)?",
    re.IGNORECASE,
)
SKIP_REPO_NAMES = {"stargazers", "network", "issues", "pulls", "pull", "wiki", "releases"}
README_CANDIDATES = ("README.md", "readme.md", "README.markdown", "README.rst")


class GitHubAPIError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        retry_after: str | None = None,
        rate_limit_remaining: str | None = None,
        rate_limit_reset: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after
        self.rate_limit_remaining = rate_limit_remaining
        self.rate_limit_reset = rate_limit_reset


def github_token() -> str:
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_API_TOKEN")
        or ""
    )


def github_headers():
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "awesome-repos-bot",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _capture_github_rate_limit_headers(headers) -> None:
    if not headers:
        return

    limit = headers.get("X-RateLimit-Limit")
    remaining = headers.get("X-RateLimit-Remaining")
    used = headers.get("X-RateLimit-Used")
    reset = headers.get("X-RateLimit-Reset")
    resource = headers.get("X-RateLimit-Resource")
    if remaining is None and limit is None:
        return

    _github_rate_limit_state.clear()
    _github_rate_limit_state.update(
        {
            "limit": limit or "",
            "remaining": remaining or "",
            "used": used or "",
            "reset": reset or "",
            "resource": resource or "",
        }
    )


def github_rate_limit_state() -> dict[str, str]:
    return dict(_github_rate_limit_state)


def github_rate_limit_remaining() -> int | None:
    reset = _github_rate_limit_state.get("reset")
    if reset:
        try:
            if int(reset) <= int(time.time()):
                return None
        except ValueError:
            return None

    remaining = _github_rate_limit_state.get("remaining")
    if remaining in {None, ""}:
        return None
    try:
        return int(remaining)
    except ValueError:
        return None


def _github_error_message(url: str, exc: HTTPError) -> str:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
    reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
    body_message = ""
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001 - error bodies are best-effort diagnostics only
        body = ""
    if body:
        try:
            body_message = json.loads(body).get("message", "")
        except (
            AttributeError,
            TypeError,
            ValueError,
            json.JSONDecodeError,
        ):
            body_message = body.strip()

    parts = [f"{exc.code} {exc.reason}", url]
    if body_message:
        parts.append(f"message={body_message[:300]}")
    if remaining is not None:
        parts.append(f"rate_limit_remaining={remaining}")
    if reset is not None:
        parts.append(f"rate_limit_reset={reset}")
    if retry_after is not None:
        parts.append(f"retry_after={retry_after}")
    return " | ".join(parts)


def fetch_json(url: str):
    request = Request(url, headers=github_headers())
    try:
        with urlopen(request, timeout=30) as response:
            _capture_github_rate_limit_headers(getattr(response, "headers", None))
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        _capture_github_rate_limit_headers(exc.headers)
        raise GitHubAPIError(
            _github_error_message(url, exc),
            status_code=exc.code,
            retry_after=exc.headers.get("Retry-After") if exc.headers else None,
            rate_limit_remaining=(
                exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            ),
            rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
        ) from exc


def fetch_text(url: str) -> str:
    request = Request(url, headers=github_headers())
    try:
        with urlopen(request, timeout=30) as response:
            _capture_github_rate_limit_headers(getattr(response, "headers", None))
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        _capture_github_rate_limit_headers(exc.headers)
        raise GitHubAPIError(
            _github_error_message(url, exc),
            status_code=exc.code,
            retry_after=exc.headers.get("Retry-After") if exc.headers else None,
            rate_limit_remaining=(
                exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
            ),
            rate_limit_reset=exc.headers.get("X-RateLimit-Reset") if exc.headers else None,
        ) from exc


def is_github_rate_limit_error(exc: Exception) -> bool:
    if not isinstance(exc, GitHubAPIError):
        return False
    message = str(exc).lower()
    if exc.status_code == 429:
        return True
    return bool(
        exc.status_code == 403
        and (
            exc.retry_after
            or exc.rate_limit_remaining == "0"
            or "rate limit" in message
            or "secondary rate limit" in message
        )
    )


def github_rate_limit_status() -> dict:
    token_configured = bool(github_token())
    status = {
        "token_configured": token_configured,
        "ok": False,
        "core": {},
        "error": "",
    }
    if not token_configured:
        status["error"] = "GITHUB_TOKEN is not configured."
        return status

    try:
        data = fetch_json("https://api.github.com/rate_limit")
    except Exception as exc:  # noqa: BLE001 - admin diagnostics should not break page render
        status["error"] = str(exc)
        return status

    core = data.get("resources", {}).get("core", {})
    reset = core.get("reset")
    reset_at = None
    if reset:
        reset_at = datetime.fromtimestamp(int(reset), UTC)

    status.update(
        ok=True,
        core={
            "limit": core.get("limit", 0),
            "used": core.get("used", 0),
            "remaining": core.get("remaining", 0),
            "reset_at": reset_at,
        },
    )
    return status


def parse_github_repo_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.netloc.lower() != "github.com":
        raise ValueError(
            "Only github.com awesome-list repositories are supported for scanning now."
        )
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        raise ValueError("GitHub repo URL must include owner and repository name.")
    return f"{parts[0]}/{parts[1].removesuffix('.git')}"


def extract_github_repos(markdown: str) -> list[str]:
    repos = set()
    for owner, repo in GITHUB_REPO_RE.findall(markdown):
        repo = repo.removesuffix(".git")
        if repo.lower() in SKIP_REPO_NAMES:
            continue
        if owner.lower() in {"topics", "collections", "marketplace", "features"}:
            continue
        repos.add(f"{owner}/{repo}")
    return sorted(repos, key=str.lower)


def raw_readme_url(
    full_name: str,
    default_branch: str = "main",
    filename: str = "README.md",
) -> str:
    owner, repo = full_name.split("/", 1)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/{filename}"


def readme_candidate_urls(full_name: str, default_branch: str):
    for filename in README_CANDIDATES:
        yield filename, raw_readme_url(full_name, default_branch, filename)


def fetch_awesome_readme(full_name: str) -> tuple[str, dict]:
    repo_meta = fetch_json(f"https://api.github.com/repos/{full_name}")
    branch = repo_meta.get("default_branch") or "main"
    last_error = None
    for _filename, url in readme_candidate_urls(full_name, branch):
        try:
            return fetch_text(url), repo_meta
        except (RuntimeError, URLError, TimeoutError) as exc:
            last_error = exc
    raise RuntimeError(f"Could not fetch README for {full_name}: {last_error}")


def fetch_repository_readme_data(full_name: str) -> dict:
    try:
        data = fetch_json(f"https://api.github.com/repos/{full_name}/readme")
    except RuntimeError as exc:
        if str(exc).startswith("404 "):
            return {
                "ok": False,
                "readme": "",
                "readme_path": "",
                "readme_url": "",
                "readme_last_error": str(exc),
            }
        raise

    readme = ""
    content = data.get("content") or ""
    if data.get("encoding") == "base64" and content:
        try:
            readme = base64.b64decode(content).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError) as exc:
            logger.warning(
                "repository_readme_decode_failed",
                repo_full_name=full_name,
                error=str(exc),
            )
            return {
                "ok": False,
                "readme": "",
                "readme_path": data.get("path") or "",
                "readme_url": data.get("download_url") or "",
                "readme_last_error": "Could not decode README content.",
            }

    download_url = data.get("download_url") or ""
    if not readme and download_url:
        readme = fetch_text(download_url)

    return {
        "ok": bool(readme),
        "readme": readme,
        "readme_path": data.get("path") or "",
        "readme_url": download_url,
        "readme_last_error": "" if readme else "README content was empty.",
    }


def fetch_repository_readme(full_name: str) -> str:
    return fetch_repository_readme_data(full_name)["readme"]


def dt(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def record_repository_snapshot(
    repository: Repository,
    *,
    captured_at=None,
    source: str = "github_api",
) -> RepositorySnapshot:
    captured_at = captured_at or timezone.now()
    return RepositorySnapshot.objects.create(
        repository=repository,
        captured_at=captured_at,
        source=source,
        description=repository.description,
        homepage_url=repository.homepage_url,
        language=repository.language,
        license_name=repository.license_name,
        topics=repository.topics,
        stars=repository.stars,
        forks=repository.forks,
        open_issues=repository.open_issues,
        watchers=repository.watchers,
        default_branch=repository.default_branch,
        is_archived=repository.is_archived,
        is_disabled=repository.is_disabled,
        is_fork=repository.is_fork,
        github_created_at=repository.github_created_at,
        github_updated_at=repository.github_updated_at,
        github_pushed_at=repository.github_pushed_at,
    )


@transaction.atomic
def _upsert_repository_metadata(
    data: dict,
    *,
    synced_at,
    readme_data: dict | None = None,
) -> Repository:
    full_name = data["full_name"]
    license_data = data.get("license") or {}
    default_branch = data.get("default_branch") or ""
    readme_defaults = {}
    if readme_data and readme_data["ok"]:
        readme_defaults = {
            "readme": readme_data["readme"],
            "readme_path": readme_data["readme_path"],
            "readme_url": readme_data["readme_url"],
            "readme_synced_at": synced_at,
            "readme_last_error": "",
        }
    elif readme_data and readme_data.get("readme_last_error"):
        readme_defaults = {
            "readme_last_error": readme_data["readme_last_error"],
        }

    repo, _ = Repository.objects.update_or_create(
        full_name=full_name,
        defaults={
            "host": "github",
            "owner": data.get("owner", {}).get("login", full_name.split("/", 1)[0]),
            "name": data.get("name", full_name.split("/", 1)[1]),
            "url": data.get("html_url", f"https://github.com/{full_name}"),
            "description": data.get("description") or "",
            "homepage_url": data.get("homepage") or "",
            "language": data.get("language") or "",
            "license_name": license_data.get("spdx_id") or license_data.get("name") or "",
            "topics": data.get("topics") or [],
            "stars": data.get("stargazers_count") or 0,
            "forks": data.get("forks_count") or 0,
            "open_issues": data.get("open_issues_count") or 0,
            "watchers": data.get("subscribers_count") or data.get("watchers_count") or 0,
            "default_branch": default_branch,
            "is_archived": bool(data.get("archived")),
            "is_disabled": bool(data.get("disabled")),
            "is_fork": bool(data.get("fork")),
            "github_created_at": dt(data.get("created_at")),
            "github_updated_at": dt(data.get("updated_at")),
            "github_pushed_at": dt(data.get("pushed_at")),
            "last_synced_at": synced_at,
            **readme_defaults,
            "raw": data,
        },
    )
    record_repository_snapshot(repo, captured_at=synced_at)
    return repo


def upsert_repository_from_github(full_name: str, *, include_readme: bool = True) -> Repository:
    data = fetch_json(f"https://api.github.com/repos/{full_name}")
    readme_data = None
    if include_readme:
        try:
            readme_data = fetch_repository_readme_data(data["full_name"])
        except Exception as exc:  # noqa: BLE001 - README fetch should not block metadata sync
            logger.warning(
                "repository_readme_fetch_failed",
                repo_full_name=data["full_name"],
                error=str(exc),
                exc_info=True,
            )
            readme_data = {
                "ok": False,
                "readme": "",
                "readme_path": "",
                "readme_url": "",
                "readme_last_error": str(exc),
            }

    repo = _upsert_repository_metadata(
        data,
        synced_at=timezone.now(),
        readme_data=readme_data,
    )
    if include_readme and repository_tagging_configured():
        sync_repository_tags(repo, repo.readme)
    if include_readme and repository_embeddings_configured():
        sync_repository_embedding(repo, repo.readme)

    return repo


def _format_delta(value: int | None) -> str:
    if value is None:
        return "baseline"
    if value > 0:
        return f"+{value}"
    return str(value)


def repository_performance_summary(repository: Repository, limit: int = 12) -> dict:
    snapshot_qs = repository.snapshots.order_by("-captured_at", "-id")
    snapshots_with_extra = list(snapshot_qs[: limit + 1])
    has_more_snapshots = len(snapshots_with_extra) > limit
    recent_snapshots = snapshots_with_extra[:limit]
    if has_more_snapshots:
        snapshot_count = repository.snapshots.count()
        first_snapshot = repository.snapshots.order_by("captured_at", "id").first()
    else:
        snapshot_count = len(recent_snapshots)
        first_snapshot = recent_snapshots[-1] if recent_snapshots else None
    latest_snapshot = recent_snapshots[0] if recent_snapshots else None
    previous_snapshot = recent_snapshots[1] if len(recent_snapshots) > 1 else None

    history = []
    for index, snapshot in enumerate(recent_snapshots):
        older_snapshot = recent_snapshots[index + 1] if index + 1 < len(recent_snapshots) else None
        stars_delta = snapshot.stars - older_snapshot.stars if older_snapshot else None
        forks_delta = snapshot.forks - older_snapshot.forks if older_snapshot else None
        history.append(
            {
                "snapshot": snapshot,
                "stars_delta": stars_delta,
                "stars_delta_label": _format_delta(stars_delta),
                "forks_delta": forks_delta,
                "forks_delta_label": _format_delta(forks_delta),
            }
        )

    stars_since_first = repository.stars - first_snapshot.stars if first_snapshot else 0
    forks_since_first = repository.forks - first_snapshot.forks if first_snapshot else 0
    watchers_since_first = repository.watchers - first_snapshot.watchers if first_snapshot else 0
    stars_since_previous = repository.stars - previous_snapshot.stars if previous_snapshot else None

    return {
        "has_history": bool(recent_snapshots),
        "snapshot_count": snapshot_count,
        "first_snapshot": first_snapshot,
        "latest_snapshot": latest_snapshot,
        "previous_snapshot": previous_snapshot,
        "stars_since_first": stars_since_first,
        "stars_since_first_label": _format_delta(stars_since_first),
        "forks_since_first": forks_since_first,
        "forks_since_first_label": _format_delta(forks_since_first),
        "watchers_since_first": watchers_since_first,
        "watchers_since_first_label": _format_delta(watchers_since_first),
        "stars_since_previous": stars_since_previous,
        "stars_since_previous_label": _format_delta(stars_since_previous),
        "history": history,
    }


def sync_awesome_list(awesome_list: AwesomeList, limit: int | None = None) -> dict:
    full_name = awesome_list.repo_full_name or parse_github_repo_url(awesome_list.source_url)
    logger.info(
        "awesome_list_scan_started",
        awesome_list_id=awesome_list.id,
        awesome_list_slug=awesome_list.slug,
        source_url=awesome_list.source_url,
        repo_full_name=full_name,
        limit=limit,
    )
    markdown, meta = fetch_awesome_readme(full_name)
    repo_names = extract_github_repos(markdown)
    if limit:
        repo_names = repo_names[:limit]

    if not repo_names:
        awesome_list.last_scanned_at = timezone.now()
        awesome_list.last_error = "No GitHub repository links found in README."
        awesome_list.save(update_fields=["last_scanned_at", "last_error", "updated_at"])
        logger.warning(
            "awesome_list_scan_found_no_repos",
            awesome_list_id=awesome_list.id,
            awesome_list_slug=awesome_list.slug,
            repo_full_name=full_name,
        )
        return {
            "awesome_list": awesome_list.slug,
            "discovered": 0,
            "synced": 0,
            "created_links": 0,
            "failures": [],
            "failure_count": 0,
        }

    awesome_list.repo_full_name = meta.get("full_name", full_name)
    awesome_list.description = meta.get("description") or awesome_list.description
    awesome_list.last_scanned_at = timezone.now()
    awesome_list.last_error = ""
    awesome_list.save(
        update_fields=[
            "repo_full_name",
            "description",
            "last_scanned_at",
            "last_error",
            "updated_at",
        ]
    )

    created_links = 0
    synced = 0
    failures = []
    for repo_name in repo_names:
        try:
            repo = upsert_repository_from_github(repo_name)
            _, created = AwesomeListItem.objects.get_or_create(
                awesome_list=awesome_list,
                repository=repo,
            )
            synced += 1
            created_links += int(created)
        except Exception as exc:  # noqa: BLE001 - keep one bad repo from killing a scan
            failures.append({"repo": repo_name, "error": str(exc)})

    if failures:
        awesome_list.last_error = f"{len(failures)} repo(s) failed to sync."
        awesome_list.save(update_fields=["last_error", "updated_at"])

    logger.info(
        "awesome_list_scan_finished",
        awesome_list_id=awesome_list.id,
        awesome_list_slug=awesome_list.slug,
        discovered=len(repo_names),
        synced=synced,
        created_links=created_links,
        failure_count=len(failures),
    )

    return {
        "awesome_list": awesome_list.slug,
        "discovered": len(repo_names),
        "synced": synced,
        "created_links": created_links,
        "failures": failures[:25],
        "failure_count": len(failures),
    }


def discover_missing_awesome_list_repositories(
    awesome_list: AwesomeList, limit: int | None = None
) -> dict:
    full_name = awesome_list.repo_full_name or parse_github_repo_url(awesome_list.source_url)
    logger.info(
        "awesome_list_missing_repo_discovery_started",
        awesome_list_id=awesome_list.id,
        awesome_list_slug=awesome_list.slug,
        source_url=awesome_list.source_url,
        repo_full_name=full_name,
        limit=limit,
    )
    markdown, meta = fetch_awesome_readme(full_name)
    repo_names = extract_github_repos(markdown)
    if limit:
        repo_names = repo_names[:limit]

    if not repo_names:
        awesome_list.last_scanned_at = timezone.now()
        awesome_list.last_error = "No GitHub repository links found in README."
        awesome_list.save(update_fields=["last_scanned_at", "last_error", "updated_at"])
        logger.warning(
            "awesome_list_missing_repo_discovery_found_no_repos",
            awesome_list_id=awesome_list.id,
            awesome_list_slug=awesome_list.slug,
            repo_full_name=full_name,
        )
        return {
            "awesome_list": awesome_list.slug,
            "discovered": 0,
            "missing": [],
            "missing_count": 0,
            "linked_existing": 0,
            "skipped_existing": 0,
        }

    awesome_list.repo_full_name = meta.get("full_name", full_name)
    awesome_list.description = meta.get("description") or awesome_list.description
    awesome_list.last_scanned_at = timezone.now()
    awesome_list.last_error = ""
    awesome_list.save(
        update_fields=[
            "repo_full_name",
            "description",
            "last_scanned_at",
            "last_error",
            "updated_at",
        ]
    )

    existing_repositories = {
        repo.full_name: repo
        for repo in Repository.objects.filter(full_name__in=repo_names).only("id", "full_name")
    }
    linked_existing = 0
    skipped_existing = 0
    missing = []

    for repo_name in repo_names:
        repo = existing_repositories.get(repo_name)
        if repo is None:
            missing.append(repo_name)
            continue

        _, created = AwesomeListItem.objects.get_or_create(
            awesome_list=awesome_list,
            repository=repo,
        )
        linked_existing += int(created)
        skipped_existing += int(not created)

    logger.info(
        "awesome_list_missing_repo_discovery_finished",
        awesome_list_id=awesome_list.id,
        awesome_list_slug=awesome_list.slug,
        discovered=len(repo_names),
        missing_count=len(missing),
        linked_existing=linked_existing,
        skipped_existing=skipped_existing,
    )

    return {
        "awesome_list": awesome_list.slug,
        "discovered": len(repo_names),
        "missing": missing,
        "missing_count": len(missing),
        "linked_existing": linked_existing,
        "skipped_existing": skipped_existing,
    }


def add_repository_to_awesome_list(awesome_list: AwesomeList, repo_full_name: str) -> dict:
    repo = Repository.objects.filter(full_name=repo_full_name).first()
    repository_created = repo is None
    if repo is None:
        repo = upsert_repository_from_github(repo_full_name)

    _, link_created = AwesomeListItem.objects.get_or_create(
        awesome_list=awesome_list,
        repository=repo,
    )

    return {
        "awesome_list": awesome_list.slug,
        "repository": repo.full_name,
        "repository_created": repository_created,
        "link_created": link_created,
    }


def refresh_repositories(
    queryset=None,
    limit: int | None = None,
    *,
    include_readme: bool = False,
) -> dict:
    queryset = queryset or Repository.objects.all()
    if limit:
        queryset = queryset[:limit]
    synced = 0
    failures = []
    for repo in queryset:
        try:
            upsert_repository_from_github(repo.full_name, include_readme=include_readme)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"repo": repo.full_name, "error": str(exc)})
    return {"synced": synced, "failure_count": len(failures), "failures": failures[:25]}


def _apply_repository_semantic_search(qs, q: str):
    try:
        response = generate_embedding(q, input_type="query")
    except Exception as exc:  # noqa: BLE001 - fall back to keyword search
        logger.warning("repository_semantic_search_failed", error=str(exc), exc_info=True)
        return qs, False

    if len(response.vector) != REPOSITORY_EMBEDDING_DIMENSIONS:
        logger.warning(
            "repository_semantic_search_dimension_mismatch",
            expected=REPOSITORY_EMBEDDING_DIMENSIONS,
            received=len(response.vector),
        )
        return qs, False

    return (
        qs.filter(
            vector__isnull=False,
            vector__model=settings.REPOSITORY_EMBEDDING_MODEL,
            vector__dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
        ).annotate(vector_distance=CosineDistance("vector__embedding", response.vector)),
        True,
    )


def _apply_repository_keyword_search(qs, q: str):
    return qs.filter(
        models.Q(full_name__icontains=q)
        | models.Q(description__icontains=q)
        | models.Q(language__icontains=q)
        | models.Q(topics__icontains=q)
        | models.Q(generated_tags__icontains=q)
    )


def _apply_repository_filters(qs, params):
    language = (params.get("language") or "").strip()
    if language:
        qs = qs.filter(language__iexact=language)
    list_slug = (params.get("list") or "").strip()
    if list_slug:
        qs = qs.filter(awesome_items__awesome_list__slug=list_slug)
    topic = normalize_repository_tag(params.get("topic") or "")
    if topic:
        qs = qs.filter(topics__contains=[topic])
    generated_tag = normalize_repository_tag(params.get("generated_tag") or "")
    if generated_tag:
        qs = qs.filter(generated_tags__contains=[generated_tag])
    min_stars = params.get("min_stars")
    if min_stars:
        qs = qs.filter(stars__gte=int(min_stars))
    archived = params.get("archived")
    if archived == "yes":
        qs = qs.filter(is_archived=True)
    elif archived == "no":
        qs = qs.filter(is_archived=False)
    updated_days = params.get("updated_days")
    if updated_days:
        cutoff = timezone.now() - timezone.timedelta(days=int(updated_days))
        qs = qs.filter(github_pushed_at__gte=cutoff)
    return qs


def repository_search_queryset(params):
    first_snapshot = RepositorySnapshot.objects.filter(repository=models.OuterRef("pk")).order_by(
        "captured_at", "id"
    )
    qs = Repository.objects.annotate(
        awesome_count=Count("awesome_items", distinct=True),
        snapshot_count=Count("snapshots", distinct=True),
        first_snapshot_stars=models.Subquery(
            first_snapshot.values("stars")[:1],
            output_field=models.PositiveIntegerField(),
        ),
    ).annotate(
        stars_since_first=models.Case(
            models.When(
                first_snapshot_stars__isnull=False,
                then=models.F("stars") - models.F("first_snapshot_stars"),
            ),
            default=models.Value(0),
            output_field=models.IntegerField(),
        )
    )
    q = (params.get("q") or "").strip()
    semantic_search = False
    if q and (params.get("mode") or "").strip() == "semantic":
        qs, semantic_search = _apply_repository_semantic_search(qs, q)

    if q and not semantic_search:
        qs = _apply_repository_keyword_search(qs, q)
    qs = _apply_repository_filters(qs, params)

    sort = params.get("sort") or "stars"
    sort_map = {
        "stars": "-stars",
        "recent": "-github_pushed_at",
        "created": "-github_created_at",
        "awesome": "-awesome_count",
        "name": "full_name",
    }
    if semantic_search:
        return qs.order_by("vector_distance", "-stars", "full_name")
    return qs.order_by(sort_map.get(sort, "-stars"), "full_name")
