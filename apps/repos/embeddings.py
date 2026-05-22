from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from asgiref.sync import async_to_sync
from django.conf import settings
from django.utils import timezone
from pydantic_ai.embeddings.openai import OpenAIEmbeddingModel
from pydantic_ai.providers.openai import OpenAIProvider

from apps.repos.models import (
    REPOSITORY_EMBEDDING_DIMENSIONS,
    Repository,
    RepositoryEmbedding,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

EmbedInputType = Literal["document", "query"]


class RepositoryEmbeddingNotConfigured(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmbeddingPayload:
    text: str
    text_hash: str


@dataclass(frozen=True, slots=True)
class EmbeddingResponse:
    vector: list[float]
    model: str


def repository_embeddings_configured() -> bool:
    return bool(settings.REPOSITORY_EMBEDDINGS_ENABLED and settings.OPENROUTER_API_KEY)


def build_repository_embedding_text(repository: Repository, readme_text: str) -> str:
    content_parts = []
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
    max_chars = settings.REPOSITORY_EMBEDDING_MAX_CHARS
    if max_chars > 0:
        text = text[:max_chars]
    return text


def build_repository_embedding_payload(
    repository: Repository,
    readme_text: str,
) -> EmbeddingPayload | None:
    text = build_repository_embedding_text(repository, readme_text)
    if not text:
        return None
    return EmbeddingPayload(
        text=text,
        text_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
    )


def repository_embedding_is_current(
    repository: Repository,
    payload: EmbeddingPayload,
) -> bool:
    return get_current_repository_embedding(repository, payload) is not None


def get_current_repository_embedding(
    repository: Repository,
    payload: EmbeddingPayload,
) -> RepositoryEmbedding | None:
    return RepositoryEmbedding.objects.filter(
        repository=repository,
        model=settings.REPOSITORY_EMBEDDING_MODEL,
        dimensions=settings.REPOSITORY_EMBEDDING_DIMENSIONS,
        source_text_hash=payload.text_hash,
    ).first()


def _embedding_model() -> OpenAIEmbeddingModel:
    provider = OpenAIProvider(
        base_url=settings.OPENROUTER_BASE_URL,
        api_key=settings.OPENROUTER_API_KEY,
    )
    return OpenAIEmbeddingModel(
        settings.REPOSITORY_EMBEDDING_MODEL,
        provider=provider,
        settings={"dimensions": settings.REPOSITORY_EMBEDDING_DIMENSIONS},
    )


async def _embed_text_async(text: str, input_type: EmbedInputType) -> EmbeddingResponse:
    result = await _embedding_model().embed(text, input_type=input_type)
    vector = list(result.embeddings[0])
    return EmbeddingResponse(vector=vector, model=result.model_name)


def generate_embedding(text: str, input_type: EmbedInputType = "document") -> EmbeddingResponse:
    if not repository_embeddings_configured():
        raise RepositoryEmbeddingNotConfigured("OPENROUTER_API_KEY is not configured.")
    return async_to_sync(_embed_text_async)(text, input_type)


def save_repository_embedding(
    repository: Repository,
    readme_text: str,
    *,
    force: bool = False,
) -> RepositoryEmbedding | None:
    payload = build_repository_embedding_payload(repository, readme_text)
    if payload is None:
        return None

    expected_model = settings.REPOSITORY_EMBEDDING_MODEL
    dimensions = settings.REPOSITORY_EMBEDDING_DIMENSIONS
    if dimensions != REPOSITORY_EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"REPOSITORY_EMBEDDING_DIMENSIONS must be {REPOSITORY_EMBEDDING_DIMENSIONS} "
            "for the current pgvector column."
        )

    if not force:
        current_embedding = get_current_repository_embedding(repository, payload)
        if current_embedding is not None:
            return current_embedding

    response = generate_embedding(payload.text, input_type="document")
    if len(response.vector) != dimensions:
        raise ValueError(
            f"Expected {dimensions} embedding dimensions, received {len(response.vector)}."
        )

    embedding, _ = RepositoryEmbedding.objects.update_or_create(
        repository=repository,
        defaults={
            "model": expected_model,
            "dimensions": dimensions,
            "source_text_hash": payload.text_hash,
            "source_text_chars": len(payload.text),
            "embedding": response.vector,
            "embedded_at": timezone.now(),
        },
    )
    return embedding


def sync_repository_embedding(
    repository: Repository,
    readme_text: str,
    *,
    force: bool = False,
) -> RepositoryEmbedding | None:
    if not repository_embeddings_configured():
        logger.info(
            "repository_embedding_skipped",
            repo_full_name=repository.full_name,
            reason="openrouter_not_configured",
        )
        return RepositoryEmbedding.objects.filter(repository=repository).first()

    try:
        return save_repository_embedding(repository, readme_text, force=force)
    except Exception as exc:  # noqa: BLE001 - embedding failures should not block repo sync
        logger.warning(
            "repository_embedding_failed",
            repo_full_name=repository.full_name,
            error=str(exc),
            exc_info=True,
        )
        return None
