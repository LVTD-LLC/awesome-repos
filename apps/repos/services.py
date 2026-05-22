from __future__ import annotations

import base64
import binascii
import json
import os
import re
from datetime import UTC, datetime
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlparse
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

GITHUB_REPO_RE = re.compile(
    r"https?://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)(?:[/#?][^\s)\]>'\"]*)?",
    re.IGNORECASE,
)
SKIP_REPO_NAMES = {"stargazers", "network", "issues", "pulls", "pull", "wiki", "releases"}
README_CANDIDATES = ("README.md", "readme.md", "README.markdown", "README.rst")
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
        if exact_match:
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


def fetch_repository_tree_items(full_name: str, default_branch: str) -> list[dict]:
    ref = quote(default_branch or "HEAD", safe="")
    data = fetch_json(f"https://api.github.com/repos/{full_name}/git/trees/{ref}?recursive=1")
    if data.get("truncated"):
        logger.warning(
            "repository_tree_truncated",
            repo_full_name=full_name,
            default_branch=default_branch,
        )
        raise RuntimeError(f"GitHub tree for {full_name} is truncated; skipping AI signal update")
    return data.get("tree") or []


def fetch_repository_ai_development_signals(
    full_name: str,
    default_branch: str,
) -> list[dict]:
    tree_items = fetch_repository_tree_items(full_name, default_branch)
    return detect_ai_development_signals(tree_items)


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
    readme_data: dict,
    ai_development_signals: list[dict] | None,
) -> Repository:
    full_name = data["full_name"]
    license_data = data.get("license") or {}
    default_branch = data.get("default_branch") or ""
    if readme_data["ok"]:
        readme_defaults = {
            "readme": readme_data["readme"],
            "readme_path": readme_data["readme_path"],
            "readme_url": readme_data["readme_url"],
            "readme_synced_at": synced_at,
            "readme_last_error": "",
        }
    else:
        readme_defaults = {
            "readme_last_error": readme_data["readme_last_error"],
        }
    ai_development_defaults = {}
    if ai_development_signals is not None:
        ai_development_defaults = {
            "uses_ai_for_development": bool(ai_development_signals),
            "ai_development_signals": ai_development_signals,
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
            **ai_development_defaults,
            "raw": data,
        },
    )
    record_repository_snapshot(repo, captured_at=synced_at)
    return repo


def upsert_repository_from_github(full_name: str) -> Repository:
    data = fetch_json(f"https://api.github.com/repos/{full_name}")
    default_branch = data.get("default_branch") or ""
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
    try:
        ai_development_signals = fetch_repository_ai_development_signals(
            data["full_name"],
            default_branch,
        )
    except Exception as exc:  # noqa: BLE001 - tree fetch should not block metadata sync
        logger.warning(
            "repository_ai_development_signal_fetch_failed",
            repo_full_name=data["full_name"],
            default_branch=default_branch,
            error=str(exc),
            exc_info=True,
        )
        ai_development_signals = None

    repo = _upsert_repository_metadata(
        data,
        synced_at=timezone.now(),
        readme_data=readme_data,
        ai_development_signals=ai_development_signals,
    )
    if repository_tagging_configured():
        sync_repository_tags(repo, repo.readme)
    if repository_embeddings_configured():
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


def _apply_repository_taxonomy_filters(qs, params):
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
    return qs


def _apply_repository_state_filters(qs, params):
    min_stars = params.get("min_stars")
    if min_stars:
        qs = qs.filter(stars__gte=int(min_stars))
    archived = params.get("archived")
    if archived == "yes":
        qs = qs.filter(is_archived=True)
    elif archived == "no":
        qs = qs.filter(is_archived=False)
    ai_development = params.get("ai_development")
    if ai_development == "yes":
        qs = qs.filter(uses_ai_for_development=True)
    elif ai_development == "no":
        qs = qs.filter(uses_ai_for_development=False)
    updated_days = params.get("updated_days")
    if updated_days:
        cutoff = timezone.now() - timezone.timedelta(days=int(updated_days))
        qs = qs.filter(github_pushed_at__gte=cutoff)
    return qs


def _apply_repository_filters(qs, params):
    qs = _apply_repository_taxonomy_filters(qs, params)
    return _apply_repository_state_filters(qs, params)


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
