from __future__ import annotations

import base64
import binascii
import json
import os
import re
import time
from collections.abc import Collection, Mapping
from datetime import UTC, datetime
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from allauth.socialaccount.models import SocialToken
from django.conf import settings
from django.db import connection, models, transaction
from django.db.models import Count
from django.db.models.functions import Coalesce
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
    AwesomeListSnapshot,
    Repository,
    RepositoryEmbedding,
    RepositoryLike,
    RepositorySnapshot,
    UserStarredRepository,
)
from apps.repos.stack_detection import MAX_STACK_FILE_BYTES, detect_repository_stack
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
RepositorySortDirection = Literal["asc", "desc"]
RepositorySortMap = Mapping[str, tuple[str, RepositorySortDirection]]
GITHUB_API_VERSION = "2026-03-10"
GITHUB_DEFAULT_ACCEPT = "application/vnd.github+json"
GITHUB_STARRED_ACCEPT = "application/vnd.github.star+json"

GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?][^\s)\]>'\"]*)?",
    re.IGNORECASE,
)
DESCRIPTION_URL_RE = re.compile(
    r"(?:https?://|www\.)[^\s<>\[\]{}\"']+",
    re.IGNORECASE,
)
SCHEMELESS_URL_RE = re.compile(
    r"^[A-Za-z0-9.-]+\.[A-Za-z]{2,}(?:[/:?#].*)?$",
    re.IGNORECASE,
)
DESCRIPTION_URL_TRAILING_PUNCTUATION = ".,;:!?)"
REPOSITORY_HOMEPAGE_URL_MAX_LENGTH = Repository._meta.get_field("homepage_url").max_length
SKIP_REPO_NAMES = {"stargazers", "network", "issues", "pulls", "pull", "wiki", "releases"}
README_CANDIDATES = ("README.md", "readme.md", "README.markdown", "README.rst")
AWESOME_LIST_MIN_REPOSITORY_LINKS = 3
AWESOME_LIST_TOPIC_MARKERS = {"awesome-list", "awesome-lists"}
AWESOME_LIST_TITLE_RE = re.compile(
    r"^\s{0,3}#{1,2}\s+awesome(?:[\s:,-]|$)",
    re.IGNORECASE | re.MULTILINE,
)
AWESOME_LIST_DESCRIPTION_RE = re.compile(
    r"\b(awesome[-\s]+list|curated\s+(?:list|collection)|"
    r"collection\s+of\s+(?:awesome\s+)?(?:projects|resources|repositories))\b",
    re.IGNORECASE,
)
AI_DEVELOPMENT_ANYWHERE_FILE_SIGNALS = {
    "agents.md": ("Agent instructions", "agent_instructions"),
    "agents.override.md": ("Codex", "codex_override_instructions"),
    "agent.md": ("Agent instructions", "agent_instructions"),
    "claude.md": ("Claude Code", "claude_memory"),
    "claude.local.md": ("Claude Code", "claude_local_memory"),
    "gemini.md": ("Gemini CLI", "gemini_context"),
    "codex.md": ("Codex", "codex_instructions"),
}
AI_DEVELOPMENT_EXACT_PATH_SIGNALS = {
    ".aider.conf.yml": ("Aider", "aider_config"),
    ".aider.conf.yaml": ("Aider", "aider_config"),
    ".coderabbit.yaml": ("CodeRabbit", "coderabbit_config"),
    ".coderabbit.yml": ("CodeRabbit", "coderabbit_config"),
    ".continue/config.json": ("Continue", "continue_config"),
    ".continue/config.ts": ("Continue", "continue_config"),
    ".continue/config.yaml": ("Continue", "continue_config"),
    ".continue/config.yml": ("Continue", "continue_config"),
    ".cursorrules": ("Cursor", "cursor_legacy_rules"),
    ".devin/config.json": ("Devin", "devin_config"),
    ".devin/config.local.json": ("Devin", "devin_local_config"),
    ".gemini/settings.json": ("Gemini CLI", "gemini_project_settings"),
    ".github/copilot-instructions.md": ("GitHub Copilot", "copilot_repo_instructions"),
    ".windsurfrules": ("Windsurf", "windsurf_legacy_rules"),
    "greptile.json": ("Greptile", "greptile_config"),
}
AI_DEVELOPMENT_DIRECTORY_SIGNALS = {
    ".agents": ("Agent workspace", "agent_directory"),
    ".claude": ("Claude Code", "claude_project_config"),
    ".claude/agents": ("Claude Code", "claude_subagents"),
    ".claude/commands": ("Claude Code", "claude_commands"),
    ".claude/skills": ("Claude Code", "claude_skills"),
    ".clinerules": ("Cline", "cline_workspace_rules"),
    ".continue": ("Continue", "continue_config"),
    ".cursor": ("Cursor", "cursor_project_config"),
    ".cursor/rules": ("Cursor", "cursor_project_rules"),
    ".devin": ("Devin", "devin_project_config"),
    ".gemini": ("Gemini CLI", "gemini_project_config"),
    ".github/instructions": ("GitHub Copilot", "copilot_path_instructions"),
    ".windsurf": ("Windsurf", "windsurf_project_config"),
    ".windsurf/rules": ("Windsurf", "windsurf_workspace_rules"),
}
AI_DEVELOPMENT_PATH_PREFIX_SIGNALS = (
    (".agents/", "Agent workspace", "agent_directory"),
    (".claude/", "Claude Code", "claude_project_config"),
    (".clinerules/", "Cline", "cline_workspace_rules"),
    (".continue/", "Continue", "continue_config"),
    (".cursor/rules/", "Cursor", "cursor_project_rules"),
    (".devin/", "Devin", "devin_project_config"),
    (".gemini/", "Gemini CLI", "gemini_project_config"),
    (".github/instructions/", "GitHub Copilot", "copilot_path_instructions"),
    (".windsurf/rules/", "Windsurf", "windsurf_workspace_rules"),
)
AWESOME_LIST_DERIVED_META_KEYS = {"commits_count", "first_commit_at"}
REPOSITORY_JSON_FILTER_FIELDS = {
    "dependency_ecosystems",
    "detected_stacks",
    "generated_tags",
    "package_managers",
    "topics",
}
MAX_UPDATED_DAYS_FILTER = 36500
MAX_AGE_YEARS_FILTER = 100


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


class GitHubTokenUnavailable(RuntimeError):
    pass


def github_token() -> str:
    return (
        os.environ.get("GITHUB_TOKEN")
        or os.environ.get("GH_TOKEN")
        or os.environ.get("GITHUB_API_TOKEN")
        or ""
    )


def github_user_tokens_for_repository_sync() -> list[str]:
    if not getattr(settings, "GITHUB_REPOSITORY_SYNC_USE_USER_TOKENS", True):
        return []

    now = timezone.now()
    tokens = []
    seen = set()
    queryset = (
        SocialToken.objects.filter(account__provider="github")
        .exclude(token="")
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gt=now))
        .order_by("id")
        .values_list("token", flat=True)
    )
    for token in queryset:
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def github_repository_sync_token_pool() -> list[str]:
    tokens = []
    seen = set()
    primary_token = github_token()
    if primary_token:
        tokens.append(primary_token)
        seen.add(primary_token)

    for token in github_user_tokens_for_repository_sync():
        if token in seen:
            continue
        tokens.append(token)
        seen.add(token)
    return tokens


def github_repository_sync_token_from_pool(
    token_pool: list[str],
    index: int = 0,
) -> str | None:
    if not token_pool:
        return None
    return token_pool[index % len(token_pool)]


def github_repository_sync_token_for_index(index: int = 0) -> str | None:
    return github_repository_sync_token_from_pool(
        github_repository_sync_token_pool(),
        index,
    )


def github_headers(
    *,
    token: str | None = None,
    accept: str = GITHUB_DEFAULT_ACCEPT,
):
    headers = {
        "Accept": accept,
        "X-GitHub-Api-Version": GITHUB_API_VERSION,
        "User-Agent": "awesome-repos-bot",
    }
    resolved_token = github_token() if token is None else token
    if resolved_token:
        headers["Authorization"] = f"Bearer {resolved_token}"
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


def fetch_json(
    url: str,
    *,
    token: str | None = None,
    accept: str = GITHUB_DEFAULT_ACCEPT,
):
    request = Request(url, headers=github_headers(token=token, accept=accept))
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


def fetch_json_page(
    url: str,
    *,
    token: str | None = None,
    accept: str = GITHUB_DEFAULT_ACCEPT,
) -> tuple[object, str]:
    request = Request(url, headers=github_headers(token=token, accept=accept))
    try:
        with urlopen(request, timeout=30) as response:
            _capture_github_rate_limit_headers(getattr(response, "headers", None))
            data = json.loads(response.read().decode("utf-8"))
            return data, response.headers.get("Link", "")
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


def fetch_text(
    url: str,
    *,
    token: str | None = None,
    accept: str = GITHUB_DEFAULT_ACCEPT,
) -> str:
    request = Request(url, headers=github_headers(token=token, accept=accept))
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


def _last_page_from_link_header(link_header: str) -> int | None:
    for link in link_header.split(","):
        if 'rel="last"' not in link:
            continue
        match = re.search(r"[?&]page=(\d+)", link)
        if match:
            return int(match.group(1))
    return None


def _commit_datetime(commit: dict) -> datetime | None:
    commit_data = commit.get("commit") or {}
    date_value = (commit_data.get("author") or {}).get("date") or (
        commit_data.get("committer") or {}
    ).get("date")
    return dt(date_value)


def _fetch_github_commits_page(
    full_name: str,
    default_branch: str,
    *,
    page: int | None = None,
    token: str | None = None,
) -> tuple[list[dict], str]:
    query_params = {"sha": default_branch, "per_page": 1}
    if page is not None:
        query_params["page"] = page
    url = f"https://api.github.com/repos/{full_name}/commits?{urlencode(query_params)}"
    request = Request(url, headers=github_headers(token=token))
    try:
        with urlopen(request, timeout=30) as response:
            _capture_github_rate_limit_headers(getattr(response, "headers", None))
            commits = json.loads(response.read().decode("utf-8"))
            link_header = response.headers.get("Link", "")
    except HTTPError as exc:
        _capture_github_rate_limit_headers(exc.headers)
        if exc.code == 409:
            return [], ""
        raise RuntimeError(_github_error_message(url, exc)) from exc

    return (commits if isinstance(commits, list) else []), link_header


def fetch_github_commit_count_and_first_commit_at(
    full_name: str,
    default_branch: str,
    *,
    token: str | None = None,
) -> tuple[int, datetime | None]:
    if not default_branch:
        raise ValueError("Cannot fetch commit count without a default branch.")

    commits, link_header = _fetch_github_commits_page(
        full_name,
        default_branch,
        token=token,
    )
    last_page = _last_page_from_link_header(link_header)
    if last_page is not None:
        first_commit_page = commits
        if last_page > 1:
            first_commit_page, _link_header = _fetch_github_commits_page(
                full_name,
                default_branch,
                page=last_page,
                token=token,
            )
        return last_page, _commit_datetime(first_commit_page[0]) if first_commit_page else None
    return len(commits), _commit_datetime(commits[0]) if commits else None


def fetch_github_commit_count(
    full_name: str,
    default_branch: str,
    *,
    token: str | None = None,
) -> int:
    if not default_branch:
        raise ValueError("Cannot fetch commit count without a default branch.")

    commits, link_header = _fetch_github_commits_page(
        full_name,
        default_branch,
        token=token,
    )
    last_page = _last_page_from_link_header(link_header)
    if last_page is not None:
        return last_page
    return len(commits)


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


def github_social_token_for_profile(profile) -> SocialToken | None:
    return (
        SocialToken.objects.filter(
            account__user_id=profile.user_id,
            account__provider="github",
        )
        .exclude(token="")
        .select_related("account")
        .order_by("-id")
        .first()
    )


def github_social_token_is_usable(social_token: SocialToken | None) -> bool:
    return bool(
        social_token
        and social_token.token
        and (not social_token.expires_at or social_token.expires_at > timezone.now())
    )


def github_token_for_profile(profile) -> str:
    social_token = github_social_token_for_profile(profile)
    if social_token is None:
        raise GitHubTokenUnavailable("Connect GitHub before importing starred repositories.")
    if social_token.expires_at and social_token.expires_at <= timezone.now():
        raise GitHubTokenUnavailable(
            "Reconnect GitHub before importing starred repositories; the saved authorization "
            "has expired."
        )
    return social_token.token


def profile_has_github_token(profile) -> bool:
    try:
        return bool(github_token_for_profile(profile))
    except GitHubTokenUnavailable:
        return False


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


def normalize_homepage_url(url: str | None) -> str:
    value = str(url or "").strip().strip("<>")
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://")):
        candidate = value
    elif SCHEMELESS_URL_RE.match(value):
        candidate = f"https://{value}"
    else:
        return ""

    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    if len(candidate) > REPOSITORY_HOMEPAGE_URL_MAX_LENGTH:
        return ""
    return candidate


def extract_homepage_url_from_description(description: str) -> str:
    for match in DESCRIPTION_URL_RE.finditer(description or ""):
        url = match.group(0).rstrip(DESCRIPTION_URL_TRAILING_PUNCTUATION)
        normalized_url = normalize_homepage_url(url)
        if normalized_url:
            return normalized_url
    return ""


def is_same_repository_url_or_subpath(url: str, repository_url: str) -> bool:
    parsed_url = urlparse(url)
    parsed_repository_url = urlparse(repository_url)
    if parsed_url.netloc.lower() != parsed_repository_url.netloc.lower():
        return False

    url_path = parsed_url.path.rstrip("/")
    repository_path = parsed_repository_url.path.rstrip("/")
    return url_path == repository_path or url_path.startswith(f"{repository_path}/")


def repository_homepage_url(data: dict) -> str:
    homepage_url = normalize_homepage_url(data.get("homepage"))
    if homepage_url:
        return homepage_url

    description_url = extract_homepage_url_from_description(data.get("description") or "")
    github_url = normalize_homepage_url(data.get("html_url"))
    if description_url and not is_same_repository_url_or_subpath(description_url, github_url):
        return description_url
    return ""


def fetch_user_starred_repositories(
    token: str,
    *,
    limit: int | None = None,
) -> list[dict]:
    if not token:
        raise GitHubTokenUnavailable("A GitHub token is required to fetch starred repositories.")
    if limit is not None and limit <= 0:
        return []

    starred = []
    page = 1
    per_page = 100
    while True:
        query = urlencode({"per_page": per_page, "page": page})
        url = f"https://api.github.com/user/starred?{query}"
        data, link_header = fetch_json_page(
            url,
            token=token,
            accept=GITHUB_STARRED_ACCEPT,
        )
        if not isinstance(data, list):
            raise RuntimeError("GitHub returned an unexpected starred repository response.")

        for item in data:
            repo_data = item.get("repo") if isinstance(item, dict) else None
            repo_data = repo_data or item
            if not isinstance(repo_data, dict) or not repo_data.get("full_name"):
                continue
            starred.append(
                {
                    "repository": repo_data,
                    "starred_at": dt(item.get("starred_at")) if isinstance(item, dict) else None,
                }
            )
            if limit is not None and len(starred) >= limit:
                return starred

        if len(data) < per_page or 'rel="next"' not in link_header:
            return starred
        page += 1


def _starred_repository_full_names(starred_items: list[dict]) -> list[str]:
    full_names = []
    for item in starred_items:
        repository_data = item.get("repository") or {}
        if repository_data.get("full_name"):
            full_names.append(repository_data["full_name"])
    return full_names


def _starred_import_last_error(
    failures: list[dict],
    *,
    stopped_for_rate_limit: bool,
) -> str:
    if stopped_for_rate_limit:
        return (
            "Import stopped early because GitHub rate limit was reached; "
            f"{len(failures)} starred repo(s) failed before stopping."
        )
    if failures:
        return f"{len(failures)} starred repo(s) failed to sync."
    return ""


def import_starred_repositories_for_profile(
    profile,
    *,
    limit: int | None = None,
    refresh_existing: bool = True,
) -> dict:
    token = github_token_for_profile(profile)
    starred_items = fetch_user_starred_repositories(token, limit=limit)
    imported_at = timezone.now()
    active_source_full_names = active_awesome_list_source_repository_name_set()
    starred_full_names = _starred_repository_full_names(starred_items)
    # Bulk-load matching rows once; daily imports may process thousands of stars per user.
    existing_repositories = {
        repository.full_name: repository
        for repository in Repository.objects.filter(full_name__in=starred_full_names)
    }
    linked_count = 0
    link_created_count = 0
    repositories_created = 0
    repositories_refreshed = 0
    failures = []
    stopped_for_rate_limit = False

    for item in starred_items:
        data = item["repository"]
        full_name = data["full_name"]
        repository = existing_repositories.get(full_name)
        repository_existed = repository is not None
        sync_error = ""
        synced = False

        if refresh_existing or repository is None:
            try:
                repository = upsert_repository_from_github(
                    full_name,
                    active_source_full_names=active_source_full_names,
                    github_access_token=token,
                )
                existing_repositories[repository.full_name] = repository
                synced = True
                repositories_refreshed += 1
                repositories_created += int(not repository_existed)
            except Exception as exc:  # noqa: BLE001 - keep importing the rest of the user's stars
                sync_error = str(exc)
                failures.append({"repo": full_name, "error": sync_error})
                if is_github_rate_limit_error(exc):
                    stopped_for_rate_limit = True
                    break
                if repository is None:
                    continue

        defaults = {
            "last_seen_at": imported_at,
            "sync_error": "" if synced else sync_error,
        }
        if item.get("starred_at") is not None:
            defaults["starred_at"] = item["starred_at"]
        if synced:
            defaults["last_synced_at"] = imported_at

        _link, created = UserStarredRepository.objects.update_or_create(
            profile=profile,
            repository=repository,
            defaults=defaults,
        )
        linked_count += 1
        link_created_count += int(created)

    profile.github_starred_repos_import_enabled = True
    profile.github_starred_repos_last_imported_at = imported_at
    profile.github_starred_repos_last_error = _starred_import_last_error(
        failures,
        stopped_for_rate_limit=stopped_for_rate_limit,
    )
    profile.save(
        update_fields=[
            "github_starred_repos_import_enabled",
            "github_starred_repos_last_imported_at",
            "github_starred_repos_last_error",
            "updated_at",
        ]
    )

    return {
        "profile_id": profile.id,
        "discovered": len(starred_items),
        "linked": linked_count,
        "created_links": link_created_count,
        "repositories_created": repositories_created,
        "repositories_refreshed": repositories_refreshed,
        "failure_count": len(failures),
        "failures": failures[:25],
        "stopped_for_rate_limit": stopped_for_rate_limit,
    }


def active_awesome_list_source_repository_names():
    return (
        AwesomeList.objects.filter(is_active=True)
        .exclude(repo_full_name="")
        .values("repo_full_name")
    )


def active_awesome_list_source_repository_name_set() -> set[str]:
    return {
        full_name.casefold()
        for full_name in AwesomeList.objects.filter(is_active=True)
        .exclude(repo_full_name="")
        .values_list("repo_full_name", flat=True)
    }


def visible_repository_queryset():
    return Repository.objects.exclude(is_awesome_list_candidate=True).exclude(
        full_name__in=active_awesome_list_source_repository_names()
    )


def with_repository_like_state(queryset, user):
    if not user.is_authenticated:
        return queryset
    return queryset.annotate(
        is_liked=models.Exists(
            RepositoryLike.objects.filter(
                repository=models.OuterRef("pk"),
                user=user,
            )
        )
    )


def detect_awesome_list_candidate(
    data: dict,
    readme_text: str = "",
    *,
    active_source_full_names: Collection[str] = (),
) -> dict:
    full_name = data.get("full_name") or ""
    normalized_full_name = full_name.casefold()
    repo_name = data.get("name") or full_name.rsplit("/", 1)[-1]
    repo_name = repo_name.lower()
    description = data.get("description") or ""
    topics = {normalize_repository_tag(str(topic)) for topic in data.get("topics") or []}
    detected_repos = extract_github_repos(readme_text or "")
    detected_repo_count = len(detected_repos)
    has_link_list = detected_repo_count >= AWESOME_LIST_MIN_REPOSITORY_LINKS
    has_awesome_title = bool(AWESOME_LIST_TITLE_RE.search(readme_text or ""))
    has_awesome_name = repo_name == "awesome" or repo_name.startswith(("awesome-", "awesome_"))
    has_awesome_description = bool(AWESOME_LIST_DESCRIPTION_RE.search(description))

    reasons = []
    if normalized_full_name and normalized_full_name in active_source_full_names:
        reasons.append("tracked_awesome_list_source")
    if topics & AWESOME_LIST_TOPIC_MARKERS:
        reasons.append("github_topic_awesome_list")
    if "awesome" in topics and has_link_list:
        reasons.append("github_topic_awesome")
    if has_awesome_title and has_link_list:
        reasons.append("awesome_readme_title")
    if has_awesome_name and (has_awesome_title or has_link_list):
        reasons.append("awesome_repo_name")
    if has_awesome_description and has_link_list:
        reasons.append("awesome_description")

    return {
        "is_candidate": bool(reasons),
        "detected_repo_count": detected_repo_count,
        "reasons": reasons,
    }


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


def fetch_awesome_readme(full_name: str, *, token: str | None = None) -> tuple[str, dict]:
    repo_meta = fetch_json(f"https://api.github.com/repos/{full_name}", token=token)
    branch = repo_meta.get("default_branch") or "main"
    last_error = None
    for _filename, url in readme_candidate_urls(full_name, branch):
        try:
            return fetch_text(url, token=token), repo_meta
        except (RuntimeError, URLError, TimeoutError) as exc:
            last_error = exc
    raise RuntimeError(f"Could not fetch README for {full_name}: {last_error}")


def attach_awesome_list_commit_count(
    full_name: str,
    meta: dict,
    *,
    existing_first_commit_at=None,
    token: str | None = None,
) -> None:
    default_branch = meta.get("default_branch") or ""
    if not default_branch:
        logger.warning(
            "awesome_list_commit_count_skipped",
            repo_full_name=full_name,
            reason="missing_default_branch",
        )
        return

    try:
        first_commit_at = None
        if existing_first_commit_at is not None:
            commit_count = fetch_github_commit_count(full_name, default_branch, token=token)
        else:
            commit_count, first_commit_at = fetch_github_commit_count_and_first_commit_at(
                full_name,
                default_branch,
                token=token,
            )
        meta["commits_count"] = commit_count
        if first_commit_at is not None:
            meta["first_commit_at"] = first_commit_at
    except Exception as exc:  # noqa: BLE001 - commit count is useful but optional
        logger.warning(
            "awesome_list_commit_activity_fetch_failed",
            repo_full_name=full_name,
            error=str(exc),
            exc_info=True,
        )


def fetch_repository_readme_data(
    full_name: str,
    *,
    token: str | None = None,
) -> dict:
    try:
        data = fetch_json(
            f"https://api.github.com/repos/{full_name}/readme",
            token=token,
        )
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
        readme = fetch_text(download_url, token=token)

    return {
        "ok": bool(readme),
        "readme": readme,
        "readme_path": data.get("path") or "",
        "readme_url": download_url,
        "readme_last_error": "" if readme else "README content was empty.",
    }


def fetch_repository_readme(full_name: str) -> str:
    return fetch_repository_readme_data(full_name)["readme"]


def _append_ai_development_signal(
    signals: list[dict],
    seen: set[str],
    *,
    path: str,
    kind: str,
    tool: str,
    signal: str,
) -> None:
    key = path.lower()
    if key in seen:
        return
    seen.add(key)
    signals.append(
        {
            "path": path,
            "kind": "directory" if kind == "tree" else "file",
            "tool": tool,
            "signal": signal,
        }
    )


def detect_ai_development_signals(tree_items: list[dict]) -> list[dict]:
    signals = []
    seen = set()

    for item in tree_items:
        path = (item.get("path") or "").strip("/")
        kind = item.get("type") or ""
        if not path or kind not in {"blob", "tree"}:
            continue

        normalized_path = path.lower()
        basename = normalized_path.rsplit("/", 1)[-1]

        exact_match = AI_DEVELOPMENT_EXACT_PATH_SIGNALS.get(normalized_path)
        if kind == "blob" and exact_match:
            tool, signal = exact_match
            _append_ai_development_signal(
                signals,
                seen,
                path=path,
                kind=kind,
                tool=tool,
                signal=signal,
            )

        directory_match = AI_DEVELOPMENT_DIRECTORY_SIGNALS.get(normalized_path)
        if kind == "tree" and directory_match:
            tool, signal = directory_match
            _append_ai_development_signal(
                signals,
                seen,
                path=path,
                kind=kind,
                tool=tool,
                signal=signal,
            )

        file_match = AI_DEVELOPMENT_ANYWHERE_FILE_SIGNALS.get(basename)
        if kind == "blob" and file_match:
            tool, signal = file_match
            _append_ai_development_signal(
                signals,
                seen,
                path=path,
                kind=kind,
                tool=tool,
                signal=signal,
            )

        for prefix, tool, signal in AI_DEVELOPMENT_PATH_PREFIX_SIGNALS:
            if normalized_path.startswith(prefix):
                _append_ai_development_signal(
                    signals,
                    seen,
                    path=path,
                    kind=kind,
                    tool=tool,
                    signal=signal,
                )

    return sorted(signals, key=lambda signal: signal["path"].lower())


def fetch_repository_tree_items(
    full_name: str,
    default_branch: str,
    *,
    token: str | None = None,
) -> list[dict]:
    ref = quote(default_branch or "HEAD", safe="")
    data = fetch_json(
        f"https://api.github.com/repos/{full_name}/git/trees/{ref}?recursive=1",
        token=token,
    )
    if data.get("truncated"):
        logger.warning(
            "repository_tree_truncated",
            repo_full_name=full_name,
            default_branch=default_branch,
        )
        raise RuntimeError(f"GitHub tree for {full_name} is truncated; skipping AI signal update")
    return data.get("tree") or []


def fetch_repository_blob_text(
    blob_url: str,
    *,
    max_bytes: int = MAX_STACK_FILE_BYTES,
    token: str | None = None,
) -> str:
    if not blob_url:
        raise ValueError("Cannot fetch repository dependency file without a blob URL.")

    data = fetch_json(blob_url, token=token)
    size = data.get("size") or 0
    if size > max_bytes:
        raise ValueError(f"Repository dependency file is larger than {max_bytes} bytes.")

    content = data.get("content") or ""
    if data.get("encoding") == "base64" and content:
        try:
            return base64.b64decode(content).decode("utf-8", errors="replace")
        except (binascii.Error, ValueError) as exc:
            raise ValueError("Could not decode repository dependency file content.") from exc
    return content


def fetch_repository_ai_development_signals(
    full_name: str,
    default_branch: str,
    *,
    token: str | None = None,
) -> list[dict]:
    tree_items = fetch_repository_tree_items(full_name, default_branch, token=token)
    return detect_ai_development_signals(tree_items)


def fetch_repository_stack_detection(
    full_name: str,
    default_branch: str,
    *,
    tree_items: list[dict] | None = None,
    token: str | None = None,
) -> dict:
    if tree_items is None:
        tree_items = fetch_repository_tree_items(full_name, default_branch, token=token)
    return detect_repository_stack(
        tree_items,
        fetch_file_text=lambda candidate: fetch_repository_blob_text(
            candidate.get("url") or "",
            token=token,
        ),
    )


def dt(value: str | datetime | None):
    if not value:
        return None
    if isinstance(value, datetime):
        if timezone.is_naive(value):
            return timezone.make_aware(value)
        return value
    parsed = parse_datetime(value)
    if parsed and timezone.is_naive(parsed):
        return timezone.make_aware(parsed)
    return parsed


def update_awesome_list_metadata(
    awesome_list: AwesomeList,
    meta: dict,
    *,
    repo_full_name: str,
    readme_repository_count: int,
    scanned_at,
    last_error: str = "",
) -> None:
    awesome_list.repo_full_name = meta.get("full_name", repo_full_name)
    awesome_list.description = meta.get("description") or awesome_list.description
    awesome_list.topics = meta.get("topics") or []
    awesome_list.stars = meta.get("stargazers_count") or 0
    awesome_list.forks = meta.get("forks_count") or 0
    awesome_list.open_issues = meta.get("open_issues_count") or 0
    awesome_list.watchers = meta.get("subscribers_count") or meta.get("watchers_count") or 0
    update_fields = [
        "repo_full_name",
        "description",
        "topics",
        "stars",
        "forks",
        "open_issues",
        "watchers",
        "readme_repository_count",
        "default_branch",
        "is_archived",
        "is_disabled",
        "github_created_at",
        "github_updated_at",
        "github_pushed_at",
        "last_scanned_at",
        "last_error",
        "raw",
        "updated_at",
    ]
    if meta.get("commits_count") is not None:
        awesome_list.commits_count = meta["commits_count"]
        update_fields.append("commits_count")
    if meta.get("first_commit_at") is not None:
        awesome_list.first_commit_at = dt(meta.get("first_commit_at"))
        update_fields.append("first_commit_at")
    awesome_list.readme_repository_count = readme_repository_count
    awesome_list.default_branch = meta.get("default_branch") or ""
    awesome_list.is_archived = bool(meta.get("archived"))
    awesome_list.is_disabled = bool(meta.get("disabled"))
    awesome_list.github_created_at = dt(meta.get("created_at"))
    awesome_list.github_updated_at = dt(meta.get("updated_at"))
    awesome_list.github_pushed_at = dt(meta.get("pushed_at"))
    awesome_list.last_scanned_at = scanned_at
    awesome_list.last_error = last_error
    awesome_list.raw = {
        key: value for key, value in meta.items() if key not in AWESOME_LIST_DERIVED_META_KEYS
    }
    awesome_list.save(update_fields=update_fields)
    if meta.get("commits_count") is None or meta.get("first_commit_at") is None:
        awesome_list.refresh_from_db(fields=["commits_count", "first_commit_at"])
    if not last_error:
        record_awesome_list_snapshot(awesome_list, captured_at=scanned_at)


def record_awesome_list_snapshot(
    awesome_list: AwesomeList,
    *,
    captured_at: datetime | None = None,
    source: str = "github_api",
) -> AwesomeListSnapshot:
    captured_at = captured_at or timezone.now()
    return AwesomeListSnapshot.objects.create(
        awesome_list=awesome_list,
        captured_at=captured_at,
        source=source,
        repo_full_name=awesome_list.repo_full_name,
        description=awesome_list.description,
        topics=awesome_list.topics,
        stars=awesome_list.stars,
        forks=awesome_list.forks,
        open_issues=awesome_list.open_issues,
        watchers=awesome_list.watchers,
        commits_count=awesome_list.commits_count,
        readme_repository_count=awesome_list.readme_repository_count,
        default_branch=awesome_list.default_branch,
        is_archived=awesome_list.is_archived,
        is_disabled=awesome_list.is_disabled,
        github_created_at=awesome_list.github_created_at,
        github_updated_at=awesome_list.github_updated_at,
        github_pushed_at=awesome_list.github_pushed_at,
        first_commit_at=awesome_list.first_commit_at,
    )


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
        commit_count=repository.commit_count,
        open_issues=repository.open_issues,
        watchers=repository.watchers,
        default_branch=repository.default_branch,
        is_archived=repository.is_archived,
        is_disabled=repository.is_disabled,
        is_fork=repository.is_fork,
        github_created_at=repository.github_created_at,
        github_updated_at=repository.github_updated_at,
        github_pushed_at=repository.github_pushed_at,
        first_commit_at=repository.first_commit_at,
    )


@transaction.atomic
def _upsert_repository_metadata(
    data: dict,
    *,
    synced_at,
    readme_data: dict | None = None,
    ai_development_signals: list[dict] | None = None,
    stack_detection: dict | None = None,
    commit_count: int | None = None,
    first_commit_at: datetime | None = None,
    active_source_full_names: Collection[str] = (),
) -> Repository:
    full_name = data["full_name"]
    license_data = data.get("license") or {}
    default_branch = data.get("default_branch") or ""
    stored_readme = ""
    if readme_data is None or not readme_data.get("ok"):
        stored_readme = (
            Repository.objects.filter(full_name=full_name).values_list("readme", flat=True).first()
            or ""
        )
    detection_readme = readme_data["readme"] if readme_data and readme_data["ok"] else stored_readme
    awesome_list_detection = detect_awesome_list_candidate(
        data,
        detection_readme,
        active_source_full_names=active_source_full_names,
    )
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
    ai_development_defaults = {}
    if ai_development_signals is not None:
        ai_development_defaults = {
            "uses_ai_for_development": bool(ai_development_signals),
            "ai_development_signals": ai_development_signals,
        }
    stack_detection_defaults = {}
    if stack_detection is not None:
        if stack_detection.get("ok"):
            stack_detection_defaults = {
                "dependency_files": stack_detection.get("dependency_files") or [],
                "dependency_ecosystems": stack_detection.get("dependency_ecosystems") or [],
                "package_managers": stack_detection.get("package_managers") or [],
                "detected_stacks": stack_detection.get("detected_stacks") or [],
                "stack_signals": stack_detection.get("stack_signals") or [],
                "stack_detected_at": synced_at,
                "stack_detection_last_error": "",
            }
        elif stack_detection.get("error"):
            stack_detection_defaults = {
                "stack_detection_last_error": str(stack_detection["error"])[:1000],
            }
    defaults = {
        "host": "github",
        "owner": data.get("owner", {}).get("login", full_name.split("/", 1)[0]),
        "name": data.get("name", full_name.split("/", 1)[1]),
        "url": data.get("html_url", f"https://github.com/{full_name}"),
        "description": data.get("description") or "",
        "homepage_url": repository_homepage_url(data),
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
        "is_awesome_list_candidate": awesome_list_detection["is_candidate"],
        "awesome_list_detected_repo_count": awesome_list_detection["detected_repo_count"],
        "awesome_list_detection_reasons": awesome_list_detection["reasons"],
        "github_created_at": dt(data.get("created_at")),
        "github_updated_at": dt(data.get("updated_at")),
        "github_pushed_at": dt(data.get("pushed_at")),
        "last_synced_at": synced_at,
        **readme_defaults,
        **ai_development_defaults,
        **stack_detection_defaults,
        "raw": data,
    }
    if commit_count is not None:
        defaults["commit_count"] = commit_count
    if first_commit_at is not None:
        defaults["first_commit_at"] = dt(first_commit_at)

    repo, _ = Repository.objects.update_or_create(
        full_name=full_name,
        defaults=defaults,
    )
    record_repository_snapshot(repo, captured_at=synced_at)
    return repo


def upsert_repository_from_github(
    full_name: str,
    *,
    include_readme: bool = True,
    active_source_full_names: Collection[str] | None = None,
    github_access_token: str | None = None,
) -> Repository:
    data = fetch_json(
        f"https://api.github.com/repos/{full_name}",
        token=github_access_token,
    )
    if active_source_full_names is None:
        active_source_full_names = active_awesome_list_source_repository_name_set()
    default_branch = data.get("default_branch") or ""
    existing_first_commit_at = (
        Repository.objects.filter(full_name=data["full_name"])
        .values_list("first_commit_at", flat=True)
        .first()
    )
    readme_data = None
    if include_readme:
        try:
            readme_data = fetch_repository_readme_data(
                data["full_name"],
                token=github_access_token,
            )
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
    tree_items = None
    tree_error = ""
    try:
        tree_items = fetch_repository_tree_items(
            data["full_name"],
            default_branch,
            token=github_access_token,
        )
    except Exception as exc:  # noqa: BLE001 - tree fetch should not block metadata sync
        tree_error = str(exc)
        logger.warning(
            "repository_tree_fetch_failed",
            repo_full_name=data["full_name"],
            default_branch=default_branch,
            error=str(exc),
            exc_info=True,
        )
    if tree_items is not None:
        ai_development_signals = detect_ai_development_signals(tree_items)
        stack_detection = fetch_repository_stack_detection(
            data["full_name"],
            default_branch,
            tree_items=tree_items,
            token=github_access_token,
        )
    else:
        ai_development_signals = None
        stack_detection = {"ok": False, "error": tree_error} if tree_error else None
    first_commit_at = None
    try:
        if existing_first_commit_at is not None:
            commit_count = fetch_github_commit_count(
                data["full_name"],
                default_branch,
                token=github_access_token,
            )
        else:
            commit_count, first_commit_at = fetch_github_commit_count_and_first_commit_at(
                data["full_name"],
                default_branch,
                token=github_access_token,
            )
    except Exception as exc:  # noqa: BLE001 - commit counts are useful but optional
        logger.warning(
            "repository_commit_activity_fetch_failed",
            repo_full_name=data["full_name"],
            default_branch=default_branch,
            error=str(exc),
            exc_info=True,
        )
        commit_count = None

    repo = _upsert_repository_metadata(
        data,
        synced_at=timezone.now(),
        readme_data=readme_data,
        ai_development_signals=ai_development_signals,
        stack_detection=stack_detection,
        commit_count=commit_count,
        first_commit_at=first_commit_at,
        active_source_full_names=active_source_full_names,
    )
    if repository_tagging_configured() and (include_readme or not repo.generated_tags):
        sync_repository_tags(repo, repo.readme)
    if include_readme and repository_embeddings_configured():
        sync_repository_embedding(repo, repo.readme)

    return repo


def sync_repository_stack_detection(
    repository: Repository,
    *,
    token: str | None = None,
) -> dict:
    detected_at = timezone.now()
    if not repository.default_branch:
        result = {
            "ok": False,
            "error": "Cannot detect repository stack without a default branch.",
        }
        repository.stack_detection_last_error = result["error"]
        repository.save(update_fields=["stack_detection_last_error", "updated_at"])
        return result

    try:
        result = fetch_repository_stack_detection(
            repository.full_name,
            repository.default_branch,
            token=token,
        )
    except Exception as exc:  # noqa: BLE001 - management backfills should keep going
        result = {"ok": False, "error": str(exc)}

    if result.get("ok"):
        repository.dependency_files = result.get("dependency_files") or []
        repository.dependency_ecosystems = result.get("dependency_ecosystems") or []
        repository.package_managers = result.get("package_managers") or []
        repository.detected_stacks = result.get("detected_stacks") or []
        repository.stack_signals = result.get("stack_signals") or []
        repository.stack_detected_at = detected_at
        repository.stack_detection_last_error = ""
        repository.save(
            update_fields=[
                "dependency_files",
                "dependency_ecosystems",
                "package_managers",
                "detected_stacks",
                "stack_signals",
                "stack_detected_at",
                "stack_detection_last_error",
                "updated_at",
            ]
        )
    else:
        repository.stack_detection_last_error = str(result.get("error") or "")[:1000]
        repository.save(update_fields=["stack_detection_last_error", "updated_at"])
    return result


def _format_delta(value: int | None, *, none_label: str = "baseline") -> str:
    if value is None:
        return none_label
    if value > 0:
        return f"+{value}"
    return str(value)


def _optional_delta(current: int | None, previous: int | None) -> int | None:
    if current is None or previous is None:
        return None
    return current - previous


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
        commit_delta = (
            _optional_delta(snapshot.commit_count, older_snapshot.commit_count)
            if older_snapshot
            else None
        )
        history.append(
            {
                "snapshot": snapshot,
                "stars_delta": stars_delta,
                "stars_delta_label": _format_delta(stars_delta),
                "forks_delta": forks_delta,
                "forks_delta_label": _format_delta(forks_delta),
                "commit_delta": commit_delta,
                "commit_delta_label": _format_delta(commit_delta),
            }
        )

    stars_since_first = repository.stars - first_snapshot.stars if first_snapshot else 0
    forks_since_first = repository.forks - first_snapshot.forks if first_snapshot else 0
    watchers_since_first = repository.watchers - first_snapshot.watchers if first_snapshot else 0
    stars_since_previous = repository.stars - previous_snapshot.stars if previous_snapshot else None
    commits_since_first = (
        _optional_delta(repository.commit_count, first_snapshot.commit_count)
        if first_snapshot
        else None
    )
    commits_since_previous = (
        _optional_delta(repository.commit_count, previous_snapshot.commit_count)
        if previous_snapshot
        else None
    )

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
        "commits_since_first": commits_since_first,
        "commits_since_first_label": _format_delta(commits_since_first, none_label="—"),
        "commits_since_previous": commits_since_previous,
        "commits_since_previous_label": _format_delta(
            commits_since_previous,
            none_label="—",
        ),
        "history": history,
    }


def repository_history_chart_data(
    repository: Repository,
    *,
    limit: int = 365,
) -> list[dict[str, int | str | None]]:
    snapshots = list(
        repository.snapshots.order_by("-captured_at", "-id").only(
            "captured_at",
            "stars",
            "commit_count",
        )[:limit]
    )
    return [
        {
            "captured_at": snapshot.captured_at.isoformat(),
            "stars": snapshot.stars,
            "commit_count": snapshot.commit_count,
        }
        for snapshot in reversed(snapshots)
    ]


def awesome_list_history_chart_data(
    awesome_list: AwesomeList,
    *,
    limit: int = 365,
) -> list[dict[str, int | str | None]]:
    if limit <= 0:
        return []

    snapshots = list(
        awesome_list.snapshots.order_by("-captured_at", "-id").only(
            "captured_at",
            "stars",
            "commits_count",
        )[:limit]
    )
    if snapshots:
        return [
            {
                "captured_at": snapshot.captured_at.isoformat(),
                "stars": snapshot.stars,
                "commit_count": snapshot.commits_count,
            }
            for snapshot in reversed(snapshots)
        ]

    has_current_metadata = awesome_list.stars > 0 or awesome_list.commits_count is not None
    if not has_current_metadata:
        return []

    captured_at = awesome_list.last_scanned_at or awesome_list.updated_at or timezone.now()
    return [
        {
            "captured_at": captured_at.isoformat(),
            "stars": awesome_list.stars,
            "commit_count": awesome_list.commits_count,
        }
    ]


def _awesome_list_history_point(captured_at, latest_by_repo: dict[int, dict]) -> dict:
    commit_values = [
        repo_state["commit_count"]
        for repo_state in latest_by_repo.values()
        if repo_state["commit_count"] is not None
    ]
    return {
        "captured_at": captured_at.isoformat(),
        "stars": sum(repo_state["stars"] for repo_state in latest_by_repo.values()),
        "commit_count": sum(commit_values) if commit_values else None,
    }


def awesome_list_repository_history_chart_data(
    awesome_list: AwesomeList,
    *,
    limit: int = 365,
) -> list[dict[str, int | str | None]]:
    if limit <= 0:
        return []

    cutoff = timezone.now() - timezone.timedelta(days=limit)
    list_snapshots = RepositorySnapshot.objects.filter(
        repository__awesome_items__awesome_list=awesome_list
    )
    seed_snapshots = (
        list_snapshots.filter(captured_at__lt=cutoff)
        .order_by("repository_id", "-captured_at", "-id")
        .distinct("repository_id")
        .values("repository_id", "stars", "commit_count")
    )
    snapshots = (
        list_snapshots.filter(captured_at__gte=cutoff)
        .order_by("captured_at", "id")
        .values("repository_id", "captured_at", "stars", "commit_count")
    )

    latest_by_repo = {
        snapshot["repository_id"]: {
            "stars": snapshot["stars"],
            "commit_count": snapshot["commit_count"],
        }
        for snapshot in seed_snapshots
    }
    points = []
    current_date = None
    current_captured_at = None
    for snapshot in snapshots:
        captured_at = snapshot["captured_at"]
        captured_date = captured_at.date()
        if current_date is not None and captured_date != current_date:
            points.append(_awesome_list_history_point(current_captured_at, latest_by_repo))

        current_date = captured_date
        current_captured_at = captured_at
        latest_by_repo[snapshot["repository_id"]] = {
            "stars": snapshot["stars"],
            "commit_count": snapshot["commit_count"],
        }

    if current_date is not None:
        points.append(_awesome_list_history_point(current_captured_at, latest_by_repo))

    return points[-limit:]


def sync_awesome_list(awesome_list: AwesomeList, limit: int | None = None) -> dict:
    full_name = awesome_list.repo_full_name or parse_github_repo_url(awesome_list.source_url)
    sync_token_pool = github_repository_sync_token_pool()
    list_sync_token = github_repository_sync_token_from_pool(sync_token_pool, 0)
    logger.info(
        "awesome_list_scan_started",
        awesome_list_id=awesome_list.id,
        awesome_list_slug=awesome_list.slug,
        source_url=awesome_list.source_url,
        repo_full_name=full_name,
        limit=limit,
    )
    markdown, meta = fetch_awesome_readme(full_name, token=list_sync_token)
    attach_awesome_list_commit_count(
        full_name,
        meta,
        existing_first_commit_at=awesome_list.first_commit_at,
        token=list_sync_token,
    )
    discovered_repo_names = extract_github_repos(markdown)
    scanned_at = timezone.now()
    repo_names = discovered_repo_names
    if limit:
        repo_names = repo_names[:limit]

    if not repo_names:
        update_awesome_list_metadata(
            awesome_list,
            meta,
            repo_full_name=full_name,
            readme_repository_count=len(discovered_repo_names),
            scanned_at=scanned_at,
            last_error="No GitHub repository links found in README.",
        )
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

    update_awesome_list_metadata(
        awesome_list,
        meta,
        repo_full_name=full_name,
        readme_repository_count=len(discovered_repo_names),
        scanned_at=scanned_at,
        last_error="",
    )

    created_links = 0
    synced = 0
    failures = []
    active_source_full_names = active_awesome_list_source_repository_name_set()
    for index, repo_name in enumerate(repo_names, start=1):
        try:
            sync_kwargs = {
                "active_source_full_names": active_source_full_names,
            }
            repository_sync_token = github_repository_sync_token_from_pool(
                sync_token_pool,
                index,
            )
            if repository_sync_token:
                sync_kwargs["github_access_token"] = repository_sync_token
            repo = upsert_repository_from_github(
                repo_name,
                **sync_kwargs,
            )
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
    list_sync_token = github_repository_sync_token_for_index(0)
    logger.info(
        "awesome_list_missing_repo_discovery_started",
        awesome_list_id=awesome_list.id,
        awesome_list_slug=awesome_list.slug,
        source_url=awesome_list.source_url,
        repo_full_name=full_name,
        limit=limit,
    )
    markdown, meta = fetch_awesome_readme(full_name, token=list_sync_token)
    discovered_repo_names = extract_github_repos(markdown)
    scanned_at = timezone.now()
    repo_names = discovered_repo_names
    if limit:
        repo_names = repo_names[:limit]

    if not repo_names:
        update_awesome_list_metadata(
            awesome_list,
            meta,
            repo_full_name=full_name,
            readme_repository_count=len(discovered_repo_names),
            scanned_at=scanned_at,
            last_error="No GitHub repository links found in README.",
        )
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

    update_awesome_list_metadata(
        awesome_list,
        meta,
        repo_full_name=full_name,
        readme_repository_count=len(discovered_repo_names),
        scanned_at=scanned_at,
        last_error="",
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


def add_repository_to_awesome_list(
    awesome_list: AwesomeList,
    repo_full_name: str,
    *,
    github_access_token: str | None = None,
) -> dict:
    repo = Repository.objects.filter(full_name=repo_full_name).first()
    repository_created = repo is None
    if repo is None:
        repo = upsert_repository_from_github(
            repo_full_name,
            github_access_token=github_access_token,
        )

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
    include_readme: bool = True,
) -> dict:
    queryset = queryset or Repository.objects.all()
    if limit:
        queryset = queryset[:limit]
    synced = 0
    failures = []
    active_source_full_names = active_awesome_list_source_repository_name_set()
    sync_token_pool = github_repository_sync_token_pool()
    for index, repo in enumerate(queryset):
        try:
            kwargs = {
                "include_readme": include_readme,
                "active_source_full_names": active_source_full_names,
            }
            repository_sync_token = github_repository_sync_token_from_pool(
                sync_token_pool,
                index,
            )
            if repository_sync_token:
                kwargs["github_access_token"] = repository_sync_token
            upsert_repository_from_github(repo.full_name, **kwargs)
            synced += 1
        except Exception as exc:  # noqa: BLE001
            failures.append({"repo": repo.full_name, "error": str(exc)})
    return {"synced": synced, "failure_count": len(failures), "failures": failures[:25]}


def repository_json_value_counts(
    field_name: str,
    *,
    awesome_list: AwesomeList | None = None,
    profile=None,
    limit: int = 200,
) -> list[dict[str, int | str]]:
    if field_name not in REPOSITORY_JSON_FILTER_FIELDS:
        raise ValueError(f"Unsupported repository JSON filter field: {field_name}")
    if awesome_list is not None and profile is not None:
        raise ValueError(
            "Filter repository JSON values by either awesome_list or profile, not both."
        )

    table_name = connection.ops.quote_name(Repository._meta.db_table)
    repository_pk = connection.ops.quote_name(Repository._meta.pk.column)
    repository_full_name = connection.ops.quote_name(Repository._meta.get_field("full_name").column)
    column_name = connection.ops.quote_name(field_name)
    list_table = connection.ops.quote_name(AwesomeList._meta.db_table)
    join_clauses = []
    where_clauses = [
        f"jsonb_typeof(repository.{column_name}) = 'array'",
        "item.value <> ''",
    ]
    params = []
    if awesome_list is not None:
        item_table = connection.ops.quote_name(AwesomeListItem._meta.db_table)
        item_repository_id = connection.ops.quote_name(
            AwesomeListItem._meta.get_field("repository").column
        )
        item_list_id = connection.ops.quote_name(
            AwesomeListItem._meta.get_field("awesome_list").column
        )
        join_clauses.append(
            f"""
        INNER JOIN {item_table} AS list_item
            ON list_item.{item_repository_id} = repository.{repository_pk}
            AND list_item.{item_list_id} = %s
        """
        )
        params.append(awesome_list.pk)
    if profile is not None:
        star_table = connection.ops.quote_name(UserStarredRepository._meta.db_table)
        star_repository_id = connection.ops.quote_name(
            UserStarredRepository._meta.get_field("repository").column
        )
        star_profile_id = connection.ops.quote_name(
            UserStarredRepository._meta.get_field("profile").column
        )
        join_clauses.append(
            f"""
        INNER JOIN {star_table} AS starred_repo
            ON starred_repo.{star_repository_id} = repository.{repository_pk}
            AND starred_repo.{star_profile_id} = %s
        """
        )
        params.append(profile.pk)
    if profile is None:
        repository_is_awesome_list_candidate = connection.ops.quote_name(
            Repository._meta.get_field("is_awesome_list_candidate").column
        )
        list_active = connection.ops.quote_name(AwesomeList._meta.get_field("is_active").column)
        list_repo_full_name = connection.ops.quote_name(
            AwesomeList._meta.get_field("repo_full_name").column
        )
        where_clauses.extend(
            [
                f"NOT repository.{repository_is_awesome_list_candidate}",
                f"""NOT EXISTS (
                SELECT 1
                FROM {list_table} AS tracked_list
                WHERE tracked_list.{list_active}
                    AND tracked_list.{list_repo_full_name} = repository.{repository_full_name}
            )""",
            ]
        )

    join_clause = "\n".join(join_clauses)
    where_clause = "\n            AND ".join(where_clauses)

    query = f"""
        SELECT item.value AS name, COUNT(*) AS count
        FROM {table_name} AS repository
        {join_clause}
        CROSS JOIN LATERAL jsonb_array_elements_text(repository.{column_name}) AS item(value)
        WHERE {where_clause}
        GROUP BY item.value
        ORDER BY count DESC, item.value ASC
        LIMIT %s
    """
    params.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [{"name": name, "count": count} for name, count in cursor.fetchall()]


def awesome_list_directory_totals() -> dict:
    list_table = connection.ops.quote_name(AwesomeList._meta.db_table)
    item_table = connection.ops.quote_name(AwesomeListItem._meta.db_table)
    repo_table = connection.ops.quote_name(Repository._meta.db_table)
    list_pk = connection.ops.quote_name(AwesomeList._meta.pk.column)
    list_active = connection.ops.quote_name("is_active")
    list_last_scanned_at = connection.ops.quote_name("last_scanned_at")
    list_readme_repository_count = connection.ops.quote_name("readme_repository_count")
    list_stars = connection.ops.quote_name("stars")
    list_repo_full_name = connection.ops.quote_name(
        AwesomeList._meta.get_field("repo_full_name").column
    )
    item_list_id = connection.ops.quote_name(AwesomeListItem._meta.get_field("awesome_list").column)
    item_repository_id = connection.ops.quote_name(
        AwesomeListItem._meta.get_field("repository").column
    )
    repo_pk = connection.ops.quote_name(Repository._meta.pk.column)
    repo_full_name = connection.ops.quote_name(Repository._meta.get_field("full_name").column)
    repo_is_awesome_list_candidate = connection.ops.quote_name(
        Repository._meta.get_field("is_awesome_list_candidate").column
    )
    query = f"""
        SELECT
            COUNT(*) AS total_lists,
            COALESCE(SUM(awesome_list.{list_readme_repository_count}), 0)
                AS total_readme_repositories,
            COALESCE(SUM(awesome_list.{list_stars}), 0) AS total_list_stars,
            MAX(awesome_list.{list_last_scanned_at}) AS latest_scan,
            (
                SELECT COUNT(*)
                FROM {item_table} AS item
                INNER JOIN {list_table} AS item_list
                    ON item.{item_list_id} = item_list.{list_pk}
                INNER JOIN {repo_table} AS item_repo
                    ON item.{item_repository_id} = item_repo.{repo_pk}
                WHERE item_list.{list_active}
                    AND NOT item_repo.{repo_is_awesome_list_candidate}
                    AND NOT EXISTS (
                        SELECT 1
                        FROM {list_table} AS tracked_list
                        WHERE tracked_list.{list_active}
                            AND tracked_list.{list_repo_full_name} = item_repo.{repo_full_name}
                    )
            ) AS total_indexed_links
        FROM {list_table} AS awesome_list
        WHERE awesome_list.{list_active}
    """
    with connection.cursor() as cursor:
        cursor.execute(query)
        (
            total_lists,
            total_readme_repositories,
            total_list_stars,
            latest_scan,
            total_indexed_links,
        ) = cursor.fetchone()

    return {
        "total_lists": total_lists,
        "total_readme_repositories": total_readme_repositories,
        "total_list_stars": total_list_stars,
        "latest_scan": latest_scan,
        "total_indexed_links": total_indexed_links,
    }


def _positive_int_param(params, name: str) -> int | None:
    value = (params.get(name) or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


def _first_positive_int_param(params, *names: str) -> int | None:
    for name in names:
        value = _positive_int_param(params, name)
        if value is not None:
            return value
    return None


def minimum_age_cutoff(params, name: str = "min_age_years"):
    years = _positive_int_param(params, name)
    if not years or years > MAX_AGE_YEARS_FILTER:
        return None
    cutoff = timezone.now().replace(microsecond=0)
    try:
        return cutoff.replace(year=cutoff.year - years)
    except ValueError:
        return cutoff.replace(year=cutoff.year - years, day=28)


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
        | models.Q(license_name__icontains=q)
        | models.Q(topics__icontains=q)
        | models.Q(generated_tags__icontains=q)
        | models.Q(package_managers__icontains=q)
        | models.Q(detected_stacks__icontains=q)
    )


def _apply_repository_taxonomy_filters(qs, params, *, allow_list_filter: bool):
    language = (params.get("language") or "").strip()
    if language:
        qs = qs.filter(language__iexact=language)

    list_slug = (params.get("list") or "").strip() if allow_list_filter else ""
    if list_slug:
        qs = qs.filter(awesome_items__awesome_list__slug=list_slug)

    topic = normalize_repository_tag(params.get("topic") or "")
    if topic:
        qs = qs.filter(topics__contains=[topic])
    generated_tag = normalize_repository_tag(params.get("generated_tag") or "")
    if generated_tag:
        qs = qs.filter(generated_tags__contains=[generated_tag])
    stack = normalize_repository_tag(params.get("framework") or params.get("stack") or "")
    if stack:
        qs = qs.filter(detected_stacks__contains=[stack])
    package_manager = normalize_repository_tag(params.get("package_manager") or "")
    if package_manager:
        qs = qs.filter(package_managers__contains=[package_manager])
    return qs


def _apply_repository_state_filters(qs, params):
    min_stars = _positive_int_param(params, "min_stars")
    if min_stars is not None:
        qs = qs.filter(stars__gte=min_stars)
    archived = {"yes": True, "no": False}.get(params.get("archived"))
    if archived is not None:
        qs = qs.filter(is_archived=archived)
    ai_development = {"yes": True, "no": False}.get(params.get("ai_development"))
    if ai_development is not None:
        qs = qs.filter(uses_ai_for_development=ai_development)
    unmaintained_days = _positive_int_param(params, "unmaintained_days")
    updated_days = _positive_int_param(params, "updated_days")
    valid_unmaintained_days = (
        unmaintained_days
        if unmaintained_days and unmaintained_days <= MAX_UPDATED_DAYS_FILTER
        else None
    )
    if updated_days and updated_days <= MAX_UPDATED_DAYS_FILTER and not valid_unmaintained_days:
        cutoff = timezone.now() - timezone.timedelta(days=updated_days)
        qs = qs.filter(github_pushed_at__gte=cutoff)
    if valid_unmaintained_days:
        cutoff = timezone.now() - timezone.timedelta(days=valid_unmaintained_days)
        qs = qs.filter(github_pushed_at__lte=cutoff)
    age_cutoff = minimum_age_cutoff(params)
    if age_cutoff:
        qs = qs.filter(first_commit_at__lte=age_cutoff)
    min_velocity_percent = _positive_int_param(params, "min_velocity_percent")
    if min_velocity_percent is not None:
        qs = qs.filter(commits_growth_percent__gte=min_velocity_percent)
    min_star_growth_percent = _first_positive_int_param(
        params,
        "min_star_growth_percent",
        "min_liability_percent",
    )
    if min_star_growth_percent is not None:
        qs = qs.filter(stars_growth_percent__gte=min_star_growth_percent)
    return qs


def _apply_repository_filters(qs, params, *, allow_list_filter: bool):
    qs = _apply_repository_taxonomy_filters(qs, params, allow_list_filter=allow_list_filter)
    return _apply_repository_state_filters(qs, params)


def _sort_direction(params, default_direction: str) -> str:
    direction = (params.get("sort_direction") or "").strip().lower()
    if direction in {"asc", "desc"}:
        return direction
    return default_direction


def _sort_expression(field_name: str, direction: str):
    field = models.F(field_name)
    if direction == "asc":
        return field.asc(nulls_last=True)
    return field.desc(nulls_last=True)


def _order_repositories(
    qs,
    params,
    *,
    extra_sort_map: RepositorySortMap | None = None,
):
    sort = params.get("sort") or "stars"
    sort_map = {
        "stars": ("stars", "desc"),
        "forks": ("forks", "desc"),
        "recent": ("github_pushed_at", "desc"),
        "created": ("github_created_at", "desc"),
        "oldest": ("first_commit_at", "asc"),
        "commits": ("commit_count", "desc"),
        "velocity": ("commits_growth_percent", "desc"),
        "star_growth": ("stars_growth_percent", "desc"),
        "liability": ("stars_growth_percent", "desc"),
        "awesome": ("awesome_count", "desc"),
        "least_awesome": ("awesome_count", "asc"),
        "name": ("full_name", "asc"),
    }
    if extra_sort_map:
        for sort_key, sort_value in extra_sort_map.items():
            sort_map.setdefault(sort_key, sort_value)
    field_name, default_direction = sort_map.get(sort, sort_map["stars"])
    direction = _sort_direction(params, default_direction)
    ordering = [_sort_expression(field_name, direction)]
    if field_name != "full_name":
        ordering.append("full_name")
    return qs.order_by(*ordering)


def _requires_repository_snapshot_metrics(params) -> bool:
    sort = (params.get("sort") or "").strip()
    if sort in {"velocity", "star_growth", "liability"}:
        return True
    return any(
        _positive_int_param(params, name) is not None
        for name in (
            "min_velocity_percent",
            "min_star_growth_percent",
            "min_liability_percent",
        )
    )


def _annotate_repository_snapshot_metrics(qs):
    first_snapshot = RepositorySnapshot.objects.filter(repository=models.OuterRef("pk")).order_by(
        "captured_at",
        "id",
    )
    return qs.annotate(
        snapshot_count=Count("snapshots", distinct=True),
        first_snapshot_stars=models.Subquery(
            first_snapshot.values("stars")[:1],
            output_field=models.PositiveIntegerField(),
        ),
        first_snapshot_commit_count=models.Subquery(
            first_snapshot.values("commit_count")[:1],
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
        ),
        commits_since_first=models.Case(
            models.When(
                commit_count__isnull=False,
                first_snapshot_commit_count__isnull=False,
                then=models.F("commit_count") - models.F("first_snapshot_commit_count"),
            ),
            default=models.Value(None),
            output_field=models.IntegerField(),
        ),
    ).annotate(
        stars_growth_percent=models.Case(
            models.When(
                first_snapshot_stars__gt=0,
                then=models.ExpressionWrapper(
                    (models.F("stars_since_first") * models.Value(100.0))
                    / models.F("first_snapshot_stars"),
                    output_field=models.FloatField(),
                ),
            ),
            default=models.Value(None),
            output_field=models.FloatField(),
        ),
        commits_growth_percent=models.Case(
            models.When(
                commit_count__isnull=False,
                first_snapshot_commit_count__gt=0,
                then=models.ExpressionWrapper(
                    (models.F("commits_since_first") * models.Value(100.0))
                    / models.F("first_snapshot_commit_count"),
                    output_field=models.FloatField(),
                ),
            ),
            default=models.Value(None),
            output_field=models.FloatField(),
        ),
    )


def repository_search_queryset(
    params,
    queryset=None,
    *,
    allow_list_filter: bool = True,
    include_snapshot_metrics: bool = True,
    extra_sort_map: RepositorySortMap | None = None,
):
    mention_count = (
        AwesomeListItem.objects.filter(repository=models.OuterRef("pk"))
        .values("repository")
        .annotate(total=Count("id"))
        .values("total")
    )
    base_queryset = queryset if queryset is not None else visible_repository_queryset()
    qs = base_queryset.annotate(
        awesome_count=Coalesce(
            models.Subquery(
                mention_count,
                output_field=models.PositiveIntegerField(),
            ),
            models.Value(0),
        ),
    )
    if include_snapshot_metrics or _requires_repository_snapshot_metrics(params):
        qs = _annotate_repository_snapshot_metrics(qs)
    q = (params.get("q") or "").strip()
    semantic_search = False
    if q and (params.get("mode") or "").strip() == "semantic":
        qs, semantic_search = _apply_repository_semantic_search(qs, q)

    if q and not semantic_search:
        qs = _apply_repository_keyword_search(qs, q)
    qs = _apply_repository_filters(qs, params, allow_list_filter=allow_list_filter)

    if semantic_search:
        return qs.order_by("vector_distance", "-stars", "full_name")
    return _order_repositories(qs, params, extra_sort_map=extra_sort_map)


def awesome_list_repository_queryset(awesome_list: AwesomeList, params):
    return repository_search_queryset(
        params,
        queryset=visible_repository_queryset().filter(awesome_items__awesome_list=awesome_list),
        allow_list_filter=False,
        include_snapshot_metrics=False,
    )


def similar_repositories_for_repository(repository: Repository, *, limit: int = 6):
    source_embedding = RepositoryEmbedding.objects.filter(
        repository=repository,
        model=settings.REPOSITORY_EMBEDDING_MODEL,
        dimensions=REPOSITORY_EMBEDDING_DIMENSIONS,
    ).first()
    if source_embedding is None:
        return Repository.objects.none()

    return (
        visible_repository_queryset()
        .filter(
            vector__model=source_embedding.model,
            vector__dimensions=source_embedding.dimensions,
        )
        .exclude(pk=repository.pk)
        .annotate(
            awesome_count=Count("awesome_items", distinct=True),
            vector_distance=CosineDistance("vector__embedding", source_embedding.embedding),
        )
        .order_by("vector_distance", "-stars", "full_name")[:limit]
    )
