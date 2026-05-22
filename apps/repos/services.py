from __future__ import annotations

import json
import os
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from django.db import models, transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.repos.models import AwesomeList, AwesomeListItem, Repository
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?][^\s)\]>'\"]*)?",
    re.IGNORECASE,
)
SKIP_REPO_NAMES = {"stargazers", "network", "issues", "pulls", "pull", "wiki", "releases"}
README_CANDIDATES = ("README.md", "readme.md", "README.markdown", "README.rst")


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
        "User-Agent": "awesome-repos-bot",
    }
    token = github_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _github_error_message(url: str, exc: HTTPError) -> str:
    retry_after = exc.headers.get("Retry-After") if exc.headers else None
    remaining = exc.headers.get("X-RateLimit-Remaining") if exc.headers else None
    reset = exc.headers.get("X-RateLimit-Reset") if exc.headers else None
    parts = [f"{exc.code} {exc.reason}", url]
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
            return json.loads(response.read().decode("utf-8"))
    except HTTPError as exc:
        raise RuntimeError(_github_error_message(url, exc)) from exc


def fetch_text(url: str) -> str:
    request = Request(url, headers=github_headers())
    try:
        with urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        raise RuntimeError(_github_error_message(url, exc)) from exc


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


def raw_readme_url(full_name: str, default_branch: str = "main") -> str:
    owner, repo = full_name.split("/", 1)
    return f"https://raw.githubusercontent.com/{owner}/{repo}/{default_branch}/README.md"


def fetch_awesome_readme(full_name: str) -> tuple[str, dict]:
    repo_meta = fetch_json(f"https://api.github.com/repos/{full_name}")
    branch = repo_meta.get("default_branch") or "main"
    owner, repo = full_name.split("/", 1)
    last_error = None
    for filename in README_CANDIDATES:
        url = f"https://raw.githubusercontent.com/{owner}/{repo}/{branch}/{filename}"
        try:
            return fetch_text(url), repo_meta
        except (HTTPError, URLError, TimeoutError) as exc:
            last_error = exc
    raise RuntimeError(f"Could not fetch README for {full_name}: {last_error}")


def dt(value: str | None):
    if not value:
        return None
    parsed = parse_datetime(value)
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def upsert_repository_from_github(full_name: str) -> Repository:
    data = fetch_json(f"https://api.github.com/repos/{full_name}")
    full_name = data["full_name"]
    license_data = data.get("license") or {}
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
            "default_branch": data.get("default_branch") or "",
            "is_archived": bool(data.get("archived")),
            "is_disabled": bool(data.get("disabled")),
            "is_fork": bool(data.get("fork")),
            "github_created_at": dt(data.get("created_at")),
            "github_updated_at": dt(data.get("updated_at")),
            "github_pushed_at": dt(data.get("pushed_at")),
            "last_synced_at": timezone.now(),
            "raw": data,
        },
    )
    return repo


@transaction.atomic
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


def refresh_repositories(queryset=None, limit: int | None = None) -> dict:
    queryset = queryset or Repository.objects.all()
    if limit:
        queryset = queryset[:limit]
    synced = 0
    failures = []
    for repo in queryset:
        try:
            upsert_repository_from_github(repo.full_name)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"repo": repo.full_name, "error": str(exc)})
    return {"synced": synced, "failure_count": len(failures), "failures": failures[:25]}


def repository_search_queryset(params):
    qs = Repository.objects.annotate(awesome_count=Count("awesome_items", distinct=True))
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            models.Q(full_name__icontains=q)
            | models.Q(description__icontains=q)
            | models.Q(language__icontains=q)
            | models.Q(topics__icontains=q)
        )
    language = (params.get("language") or "").strip()
    if language:
        qs = qs.filter(language__iexact=language)
    list_slug = (params.get("list") or "").strip()
    if list_slug:
        qs = qs.filter(awesome_items__awesome_list__slug=list_slug)
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

    sort = params.get("sort") or "stars"
    sort_map = {
        "stars": "-stars",
        "recent": "-github_pushed_at",
        "created": "-github_created_at",
        "awesome": "-awesome_count",
        "name": "full_name",
    }
    return qs.order_by(sort_map.get(sort, "-stars"), "full_name")
