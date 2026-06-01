from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass

from django.conf import settings
from django.db import models
from django.utils import timezone
from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Agent

from apps.core.agents.base import build_model
from apps.repos.models import Repository
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

TAG_SEPARATOR_RE = re.compile(r"[\s_/]+")
TAG_DISALLOWED_RE = re.compile(r"[^a-z0-9.+#-]+")
NO_USABLE_TAGS_ERROR = "Repository tag generation returned no usable tags."


class EmptyRepositoryTagsError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class RepositoryTaggingPayload:
    text: str
    text_hash: str


class RepositoryTagsOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tags: list[str] = Field(
        default_factory=list,
        description="Short normalized tags that describe what the repository is useful for.",
    )


def repository_tagging_model_name() -> str:
    supported: dict[str, dict[str, str]] = settings.SUPPORTED_AI_MODELS
    provider = settings.REPOSITORY_TAGGING_PROVIDER
    label = settings.REPOSITORY_TAGGING_MODEL_LABEL
    return supported[provider][label]


def repository_tagging_model_id() -> str:
    return f"{settings.REPOSITORY_TAGGING_PROVIDER}/{repository_tagging_model_name()}"


def repository_tagging_configured() -> bool:
    if not settings.REPOSITORY_TAGGING_ENABLED:
        return False

    provider = settings.REPOSITORY_TAGGING_PROVIDER
    provider_env_keys = {
        "openai": ("OPENAI_API_KEY",),
        "anthropic": ("ANTHROPIC_API_KEY",),
        "google": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    }
    return any(os.environ.get(key) for key in provider_env_keys.get(provider, ()))


def normalize_repository_tag(value: str) -> str:
    tag = TAG_SEPARATOR_RE.sub("-", value.strip().lower())
    tag = TAG_DISALLOWED_RE.sub("", tag)
    return tag.strip(".-")


def normalize_repository_tags(values: list[str]) -> list[str]:
    tags = []
    seen = set()
    max_tags = max(settings.REPOSITORY_TAGGING_MAX_TAGS, 0)
    for value in values:
        tag = normalize_repository_tag(value)
        if not tag or tag in seen:
            continue
        tags.append(tag)
        seen.add(tag)
        if max_tags and len(tags) >= max_tags:
            break
    return tags


def _clean_metadata_values(values) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value).strip() for value in values if str(value).strip()]


def _format_ai_development_signals(signals) -> list[str]:
    if not isinstance(signals, list):
        return []

    labels = []
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        tool = str(signal.get("tool") or "").strip()
        path = str(signal.get("path") or "").strip()
        if tool and path:
            labels.append(f"{tool} ({path})")
        elif tool:
            labels.append(tool)
        elif path:
            labels.append(path)
    return labels


def build_repository_tagging_text(repository: Repository, readme_text: str) -> str:
    content_parts = []
    language = (repository.language or "").strip()
    if language:
        content_parts.append(f"Primary language:\n{language}")

    topics = _clean_metadata_values(repository.topics)
    if topics:
        content_parts.append(f"GitHub topics:\n{', '.join(topics[:25])}")

    ai_development_signals = _format_ai_development_signals(repository.ai_development_signals)
    if ai_development_signals:
        content_parts.append("AI development signals:\n" + "\n".join(ai_development_signals[:20]))

    description = (repository.description or "").strip()
    if description:
        content_parts.append(f"Description:\n{description}")

    readme_text = (readme_text or "").strip()
    if readme_text:
        content_parts.append(f"README:\n{readme_text}")

    if not content_parts:
        return ""

    text = f"Repository: {repository.full_name}\n\n" + "\n\n".join(content_parts)
    text = text.replace("\x00", "").strip()
    max_chars = settings.REPOSITORY_TAGGING_MAX_CHARS
    if max_chars > 0:
        text = text[:max_chars]
    return text


def build_repository_tagging_payload(
    repository: Repository,
    readme_text: str,
) -> RepositoryTaggingPayload | None:
    text = build_repository_tagging_text(repository, readme_text)
    if not text:
        return None
    return RepositoryTaggingPayload(
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def repository_tags_are_current(
    repository: Repository,
    payload: RepositoryTaggingPayload,
) -> bool:
    return bool(
        repository.generated_tags
        and repository.generated_tags_synced_at
        and not repository.generated_tags_last_error
        and repository.generated_tags_model == repository_tagging_model_id()
        and repository.generated_tags_source_hash == payload.text_hash
    )


def _tagging_agent() -> Agent[None, RepositoryTagsOutput]:
    model = build_model(
        provider=settings.REPOSITORY_TAGGING_PROVIDER,
        label=settings.REPOSITORY_TAGGING_MODEL_LABEL,
    )
    return Agent(
        model,
        output_type=RepositoryTagsOutput,
        instructions=(
            "Generate concise discovery tags for a GitHub repository from its description and "
            "README. Return 3 to 8 tags. Prefer capability, domain, framework, runtime, "
            "architecture, and integration tags a developer would use to filter repositories. "
            "Use lowercase words or short hyphenated phrases. Do not include stars, popularity, "
            "license, owner names, or generic terms like repository, github, software, or tool."
        ),
    )


def generate_repository_tags(text: str) -> list[str]:
    result = _tagging_agent().run_sync(
        "Create repository discovery tags for this GitHub repository. "
        "Return only the structured tag list.\n\n"
        f"{text}"
    )
    return _require_generated_tags(normalize_repository_tags(result.output.tags))


def _require_generated_tags(tags: list[str]) -> list[str]:
    if not tags:
        raise EmptyRepositoryTagsError(NO_USABLE_TAGS_ERROR)
    return tags


def _clear_repository_tags(repository: Repository) -> list[str]:
    repository.generated_tags = []
    repository.generated_tags_model = ""
    repository.generated_tags_source_hash = ""
    repository.generated_tags_synced_at = timezone.now()
    repository.generated_tags_last_error = ""
    repository.save(
        update_fields=[
            "generated_tags",
            "generated_tags_model",
            "generated_tags_source_hash",
            "generated_tags_synced_at",
            "generated_tags_last_error",
            "updated_at",
        ]
    )
    return []


def _record_repository_tagging_failure(
    repository: Repository,
    payload: RepositoryTaggingPayload,
    error: Exception,
) -> None:
    repository.generated_tags = []
    repository.generated_tags_model = repository_tagging_model_id()
    repository.generated_tags_source_hash = payload.text_hash
    repository.generated_tags_synced_at = timezone.now()
    repository.generated_tags_last_error = str(error)
    repository.save(
        update_fields=[
            "generated_tags",
            "generated_tags_model",
            "generated_tags_source_hash",
            "generated_tags_synced_at",
            "generated_tags_last_error",
            "updated_at",
        ]
    )


def save_repository_tags(
    repository: Repository,
    readme_text: str,
    *,
    force: bool = False,
) -> list[str]:
    payload = build_repository_tagging_payload(repository, readme_text)
    if payload is None:
        return _clear_repository_tags(repository)

    if not force:
        if repository_tags_are_current(repository, payload):
            return repository.generated_tags
        if (
            repository.generated_tags_last_error
            and repository.generated_tags_model == repository_tagging_model_id()
            and repository.generated_tags_source_hash == payload.text_hash
        ):
            return repository.generated_tags

    try:
        tags = generate_repository_tags(payload.text)
    except EmptyRepositoryTagsError as exc:
        _record_repository_tagging_failure(repository, payload, exc)
        raise

    repository.generated_tags = tags
    repository.generated_tags_model = repository_tagging_model_id()
    repository.generated_tags_source_hash = payload.text_hash
    repository.generated_tags_synced_at = timezone.now()
    repository.generated_tags_last_error = ""
    repository.save(
        update_fields=[
            "generated_tags",
            "generated_tags_model",
            "generated_tags_source_hash",
            "generated_tags_synced_at",
            "generated_tags_last_error",
            "updated_at",
        ]
    )
    return tags


def sync_repository_tags(
    repository: Repository,
    readme_text: str,
    *,
    force: bool = False,
) -> list[str]:
    if not repository_tagging_configured():
        logger.info(
            "repository_tagging_skipped",
            repo_full_name=repository.full_name,
            reason="tagging_not_configured",
        )
        return repository.generated_tags

    try:
        return save_repository_tags(repository, readme_text, force=force)
    except EmptyRepositoryTagsError as exc:
        logger.warning(
            "repository_tagging_failed",
            repo_full_name=repository.full_name,
            error=str(exc),
            exc_info=True,
        )
        return repository.generated_tags
    except Exception as exc:  # noqa: BLE001 - tagging failures should not block repo sync
        logger.warning(
            "repository_tagging_failed",
            repo_full_name=repository.full_name,
            error=str(exc),
            exc_info=True,
        )
        repository.generated_tags_last_error = str(exc)
        repository.save(update_fields=["generated_tags_last_error", "updated_at"])
        return repository.generated_tags


def tag_repository_batch(
    queryset=None,
    *,
    limit: int | None = None,
    force: bool = False,
) -> dict:
    if queryset is None:
        queryset = Repository.objects.all()
    queryset = queryset.order_by(
        models.F("generated_tags_synced_at").asc(nulls_first=True),
        "full_name",
    )
    if limit is not None:
        queryset = queryset[: max(limit, 0)]

    tagged = 0
    skipped = 0
    unchanged = 0
    failures = []
    for repository in queryset:
        try:
            payload = build_repository_tagging_payload(repository, repository.readme)
            if payload is None:
                skipped += 1
                continue

            if not force and repository_tags_are_current(repository, payload):
                unchanged += 1
                continue

            tags = save_repository_tags(
                repository,
                repository.readme,
                force=force,
            )
        except Exception as exc:  # noqa: BLE001 - report and continue batch backfills
            failures.append({"repo": repository.full_name, "error": str(exc)})
            continue

        if not tags:
            skipped += 1
            continue
        tagged += 1

    return {
        "tagged": tagged,
        "skipped": skipped,
        "unchanged": unchanged,
        "failure_count": len(failures),
        "failures": failures[:25],
    }
