from collections.abc import Callable
from typing import Any

from django.core.paginator import Paginator
from django.db.models import Count, F, Max, Q, Sum
from django.shortcuts import get_object_or_404

from apps.repos.models import AwesomeList, Repository, RepositorySnapshot
from apps.repos.services import (
    awesome_list_directory_totals,
    awesome_list_repository_queryset,
    minimum_age_cutoff,
    repository_history_chart_data,
    repository_json_value_counts,
    repository_performance_summary,
    repository_search_queryset,
    similar_repositories_for_repository,
)

DEFAULT_API_PAGE_SIZE = 30
MAX_API_PAGE_SIZE = 100


def normalized_query_params(**params) -> dict[str, str]:
    """Return string params compatible with the existing repo UI query helpers."""
    return {name: str(value) for name, value in params.items() if value is not None and value != ""}


def paginate_queryset(
    queryset,
    *,
    page: int,
    page_size: int,
    serializer: Callable[[Any], dict],
) -> dict:
    page = max(page, 1)
    page_size = min(max(page_size, 1), MAX_API_PAGE_SIZE)
    paginator = Paginator(queryset, page_size)
    page_obj = paginator.get_page(page)
    return {
        "pagination": {
            "count": paginator.count,
            "page": page_obj.number,
            "page_size": page_size,
            "num_pages": paginator.num_pages,
            "has_next": page_obj.has_next(),
            "has_previous": page_obj.has_previous(),
        },
        "results": [serializer(item) for item in page_obj.object_list],
    }


def awesome_list_search_queryset(params):
    qs = AwesomeList.objects.filter(is_active=True).annotate(
        indexed_repo_count=Count("items", distinct=True)
    )
    q = (params.get("q") or "").strip()
    if q:
        qs = qs.filter(
            Q(name__icontains=q)
            | Q(description__icontains=q)
            | Q(repo_full_name__icontains=q)
            | Q(topics__icontains=q)
        )

    age_cutoff = minimum_age_cutoff(params)
    if age_cutoff:
        qs = qs.filter(first_commit_at__lte=age_cutoff)

    sort = params.get("sort") or "stars"
    sort_map = {
        "stars": "-stars",
        "repos": "-readme_repository_count",
        "indexed": "-indexed_repo_count",
        "commits": F("commits_count").desc(nulls_last=True),
        "recent": F("github_pushed_at").desc(nulls_last=True),
        "oldest": F("first_commit_at").asc(nulls_last=True),
        "scanned": F("last_scanned_at").desc(nulls_last=True),
        "name": "name",
    }
    return qs.order_by(sort_map.get(sort, "-stars"), "name")


def serialize_awesome_list_reference(awesome_list: AwesomeList) -> dict:
    return {
        "id": awesome_list.id,
        "name": awesome_list.name,
        "slug": awesome_list.slug,
        "source_url": awesome_list.source_url,
        "repo_full_name": awesome_list.repo_full_name,
        "stars": awesome_list.stars,
    }


def serialize_awesome_list_summary(awesome_list: AwesomeList) -> dict:
    indexed_repo_count = getattr(awesome_list, "indexed_repo_count", None)
    if indexed_repo_count is None:
        indexed_repo_count = getattr(awesome_list, "item_count", None)
    if indexed_repo_count is None:
        indexed_repo_count = awesome_list.items.count()

    return {
        "id": awesome_list.id,
        "name": awesome_list.name,
        "slug": awesome_list.slug,
        "source_url": awesome_list.source_url,
        "repo_full_name": awesome_list.repo_full_name,
        "description": awesome_list.description,
        "topics": awesome_list.topics,
        "stars": awesome_list.stars,
        "forks": awesome_list.forks,
        "commits_count": awesome_list.commits_count,
        "open_issues": awesome_list.open_issues,
        "watchers": awesome_list.watchers,
        "readme_repository_count": awesome_list.readme_repository_count,
        "indexed_repo_count": indexed_repo_count,
        "default_branch": awesome_list.default_branch,
        "is_archived": awesome_list.is_archived,
        "is_disabled": awesome_list.is_disabled,
        "is_active": awesome_list.is_active,
        "github_created_at": awesome_list.github_created_at,
        "github_updated_at": awesome_list.github_updated_at,
        "github_pushed_at": awesome_list.github_pushed_at,
        "first_commit_at": awesome_list.first_commit_at,
        "last_scanned_at": awesome_list.last_scanned_at,
        "last_error": awesome_list.last_error,
    }


def serialize_repository_summary(repository: Repository) -> dict:
    awesome_count = getattr(repository, "awesome_count", None)
    if awesome_count is None:
        awesome_count = repository.awesome_items.count()

    return {
        "id": repository.id,
        "host": repository.host,
        "full_name": repository.full_name,
        "owner": repository.owner,
        "name": repository.name,
        "url": repository.url,
        "description": repository.description,
        "homepage_url": repository.homepage_url,
        "language": repository.language,
        "license_name": repository.license_name,
        "topics": repository.topics,
        "generated_tags": repository.generated_tags,
        "stars": repository.stars,
        "forks": repository.forks,
        "commit_count": repository.commit_count,
        "open_issues": repository.open_issues,
        "watchers": repository.watchers,
        "default_branch": repository.default_branch,
        "is_archived": repository.is_archived,
        "is_disabled": repository.is_disabled,
        "is_fork": repository.is_fork,
        "uses_ai_for_development": repository.uses_ai_for_development,
        "awesome_count": awesome_count,
        "snapshot_count": getattr(repository, "snapshot_count", None),
        "stars_since_first": getattr(repository, "stars_since_first", None),
        "commits_since_first": getattr(repository, "commits_since_first", None),
        "github_created_at": repository.github_created_at,
        "github_updated_at": repository.github_updated_at,
        "github_pushed_at": repository.github_pushed_at,
        "first_commit_at": repository.first_commit_at,
        "last_synced_at": repository.last_synced_at,
        "awesome_lists": [
            serialize_awesome_list_reference(item.awesome_list)
            for item in repository.awesome_items.all()
        ],
    }


def serialize_repository_snapshot(snapshot: RepositorySnapshot | None) -> dict | None:
    if snapshot is None:
        return None
    return {
        "captured_at": snapshot.captured_at,
        "source": snapshot.source,
        "stars": snapshot.stars,
        "forks": snapshot.forks,
        "commit_count": snapshot.commit_count,
        "open_issues": snapshot.open_issues,
        "watchers": snapshot.watchers,
        "first_commit_at": snapshot.first_commit_at,
    }


def serialize_repository_performance(performance: dict) -> dict:
    return {
        "has_history": performance["has_history"],
        "snapshot_count": performance["snapshot_count"],
        "first_snapshot": serialize_repository_snapshot(performance["first_snapshot"]),
        "latest_snapshot": serialize_repository_snapshot(performance["latest_snapshot"]),
        "previous_snapshot": serialize_repository_snapshot(performance["previous_snapshot"]),
        "stars_since_first": performance["stars_since_first"],
        "forks_since_first": performance["forks_since_first"],
        "watchers_since_first": performance["watchers_since_first"],
        "stars_since_previous": performance["stars_since_previous"],
        "commits_since_first": performance["commits_since_first"],
        "commits_since_previous": performance["commits_since_previous"],
    }


def serialize_repository_detail(
    repository: Repository,
    *,
    performance: dict,
    history: list[dict],
    similar_repositories,
) -> dict:
    data = serialize_repository_summary(repository)
    data.update(
        {
            "readme": repository.readme,
            "readme_path": repository.readme_path,
            "readme_url": repository.readme_url,
            "readme_synced_at": repository.readme_synced_at,
            "readme_last_error": repository.readme_last_error,
            "ai_development_signals": repository.ai_development_signals,
            "performance": serialize_repository_performance(performance),
            "history": history,
            "similar_repositories": [
                serialize_repository_summary(similar_repo) for similar_repo in similar_repositories
            ],
        }
    )
    return data


def serialize_awesome_list_repo_stats(stats: dict) -> dict:
    return {
        "total_stars": stats["total_stars"] or 0,
        "total_forks": stats["total_forks"] or 0,
        "active_count": stats["active_count"] or 0,
        "archived_count": stats["archived_count"] or 0,
        "latest_repo_push": stats["latest_repo_push"],
    }


def serialize_value_counts(rows) -> list[dict]:
    return [{"name": row["name"], "count": row["count"]} for row in rows]


def search_repositories_payload(
    *,
    q: str = "",
    mode: str = "",
    list_slug: str = "",
    language: str = "",
    topic: str = "",
    generated_tag: str = "",
    min_stars: int | None = None,
    updated_days: int | None = None,
    min_age_years: int | None = None,
    archived: str = "",
    ai_development: str = "",
    sort: str = "stars",
    page: int = 1,
    page_size: int = DEFAULT_API_PAGE_SIZE,
) -> dict:
    params = normalized_query_params(
        q=q,
        mode=mode,
        list=list_slug,
        language=language,
        topic=topic,
        generated_tag=generated_tag,
        min_stars=min_stars,
        updated_days=updated_days,
        min_age_years=min_age_years,
        archived=archived,
        ai_development=ai_development,
        sort=sort,
    )
    qs = repository_search_queryset(params).prefetch_related("awesome_items__awesome_list")
    return paginate_queryset(
        qs,
        page=page,
        page_size=page_size,
        serializer=serialize_repository_summary,
    )


def get_repository_detail_payload(
    *,
    owner: str,
    name: str,
) -> dict:
    repository = get_object_or_404(
        Repository.objects.prefetch_related("awesome_items__awesome_list"),
        full_name=f"{owner}/{name}",
    )
    performance = repository_performance_summary(repository)
    history = []
    if performance["has_history"]:
        history = repository_history_chart_data(repository)
    similar_repositories = similar_repositories_for_repository(repository).prefetch_related(
        "awesome_items__awesome_list"
    )
    return serialize_repository_detail(
        repository,
        performance=performance,
        history=history,
        similar_repositories=similar_repositories,
    )


def search_awesome_lists_payload(
    *,
    q: str = "",
    min_age_years: int | None = None,
    sort: str = "stars",
    page: int = 1,
    page_size: int = DEFAULT_API_PAGE_SIZE,
) -> dict:
    params = normalized_query_params(q=q, min_age_years=min_age_years, sort=sort)
    page_data = paginate_queryset(
        awesome_list_search_queryset(params),
        page=page,
        page_size=page_size,
        serializer=serialize_awesome_list_summary,
    )
    return {
        **page_data,
        "totals": awesome_list_directory_totals(),
    }


def get_awesome_list_detail_payload(*, slug: str) -> dict:
    awesome_list = get_object_or_404(
        AwesomeList.objects.filter(is_active=True).annotate(
            indexed_repo_count=Count("items", distinct=True)
        ),
        slug=slug,
    )
    repos = Repository.objects.filter(awesome_items__awesome_list=awesome_list)
    repo_stats = repos.aggregate(
        total_stars=Sum("stars"),
        total_forks=Sum("forks"),
        active_count=Count("id", filter=Q(is_archived=False)),
        archived_count=Count("id", filter=Q(is_archived=True)),
        latest_repo_push=Max("github_pushed_at"),
    )
    language_counts = (
        repos.exclude(language="")
        .values("language")
        .annotate(count=Count("id"))
        .order_by("-count", "language")[:12]
    )
    return {
        "awesome_list": serialize_awesome_list_summary(awesome_list),
        "repo_stats": serialize_awesome_list_repo_stats(repo_stats),
        "language_counts": [
            {"name": row["language"], "count": row["count"]} for row in language_counts
        ],
    }


def search_awesome_list_repositories_payload(
    *,
    slug: str,
    q: str = "",
    language: str = "",
    topic: str = "",
    generated_tag: str = "",
    min_stars: int | None = None,
    updated_days: int | None = None,
    min_age_years: int | None = None,
    archived: str = "",
    ai_development: str = "",
    sort: str = "stars",
    page: int = 1,
    page_size: int = 50,
) -> dict:
    awesome_list = get_object_or_404(AwesomeList.objects.filter(is_active=True), slug=slug)
    params = normalized_query_params(
        q=q,
        language=language,
        topic=topic,
        generated_tag=generated_tag,
        min_stars=min_stars,
        updated_days=updated_days,
        min_age_years=min_age_years,
        archived=archived,
        ai_development=ai_development,
        sort=sort,
    )
    qs = awesome_list_repository_queryset(awesome_list, params).prefetch_related(
        "awesome_items__awesome_list"
    )
    return paginate_queryset(
        qs,
        page=page,
        page_size=page_size,
        serializer=serialize_repository_summary,
    )


def get_awesome_list_repository_options_payload(*, slug: str) -> dict:
    awesome_list = get_object_or_404(AwesomeList.objects.filter(is_active=True), slug=slug)
    repos = Repository.objects.filter(awesome_items__awesome_list=awesome_list)
    return {
        "languages": list(
            repos.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        ),
        "topics": serialize_value_counts(
            repository_json_value_counts("topics", awesome_list=awesome_list)
        ),
        "generated_tags": serialize_value_counts(
            repository_json_value_counts("generated_tags", awesome_list=awesome_list)
        ),
    }
