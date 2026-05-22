from math import ceil

from django.conf import settings
from django.core.cache import cache
from django.utils import timezone
from django_q.tasks import async_task

from apps.repos.models import AwesomeList, Repository
from apps.repos.services import (
    add_repository_to_awesome_list,
    discover_missing_awesome_list_repositories,
    github_rate_limit_remaining,
    github_rate_limit_status,
    is_github_rate_limit_error,
    sync_awesome_list,
    upsert_repository_from_github,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


def daily_repository_refresh_limit(total_repositories: int) -> int:
    if total_repositories <= 0:
        return 0

    target_days = max(1, settings.GITHUB_REPOSITORY_REFRESH_TARGET_DAYS)
    configured_cap = max(1, settings.GITHUB_DAILY_REPOSITORY_REFRESH_LIMIT)
    cycle_limit = max(1, ceil(total_repositories / target_days))
    return min(configured_cap, cycle_limit)


def _daily_missing_repository_limit(limit: int | None) -> int:
    if limit is not None:
        return max(0, limit)
    return max(0, settings.GITHUB_DAILY_DISCOVERY_REPOSITORY_LIMIT)


def _daily_missing_repository_budget_key() -> str:
    return f"github-missing-repo-budget:{timezone.now():%Y%m%d}"


def _try_reserve_daily_missing_repository_slot(daily_limit: int) -> bool:
    if daily_limit <= 0:
        return False

    key = _daily_missing_repository_budget_key()
    cache.add(key, 0, timeout=60 * 60 * 36)
    try:
        used = cache.incr(key)
    except ValueError:
        cache.add(key, 0, timeout=60 * 60 * 36)
        used = cache.incr(key)
    return used <= daily_limit


def _available_repository_refresh_limit(refresh_limit: int, min_remaining: int | None) -> int:
    if min_remaining is None or refresh_limit <= 0:
        return refresh_limit

    if github_rate_limit_remaining() is None:
        github_rate_limit_status()

    remaining = github_rate_limit_remaining()
    if remaining is None:
        return refresh_limit
    return min(refresh_limit, max(0, remaining - min_remaining))


def _github_refresh_budget_exhausted(min_remaining: int | None) -> bool:
    if min_remaining is None:
        return False

    # Rate-limit state is process-local and only populated after a GitHub response.
    # This guard is proactive best effort; individual refresh tasks still handle
    # actual 403/429 rate-limit responses without retrying.
    remaining = github_rate_limit_remaining()
    return remaining is not None and remaining <= min_remaining


def sync_awesome_list_task(awesome_list_id: int, limit: int | None = None):
    awesome_list = AwesomeList.objects.get(id=awesome_list_id)
    try:
        logger.info(
            "awesome_list_scan_task_started",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            limit=limit,
        )
        result = sync_awesome_list(awesome_list, limit=limit)
        logger.info(
            "awesome_list_scan_task_finished",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            result=result,
        )
        return result
    except Exception as exc:
        awesome_list.last_error = str(exc)
        awesome_list.save(update_fields=["last_error", "updated_at"])
        logger.error(
            "awesome_list_scan_task_failed",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            error=str(exc),
            exc_info=True,
        )
        raise


def sync_all_awesome_lists_task(limit_per_list: int | None = None):
    results = []
    for awesome_list in AwesomeList.objects.filter(is_active=True):
        results.append(sync_awesome_list_task(awesome_list.id, limit=limit_per_list))
    return results


def enqueue_awesome_list_missing_repo_syncs_task(limit_per_list: int | None = None):
    return enqueue_missing_repositories_from_awesome_lists_task(limit_per_list=limit_per_list)


def enqueue_missing_repositories_from_awesome_lists_task(
    limit_per_list: int | None = None,
    daily_limit: int | None = None,
):
    task_ids = []
    resolved_daily_limit = _daily_missing_repository_limit(daily_limit)
    awesome_lists = AwesomeList.objects.filter(is_active=True).order_by("name")
    for awesome_list in awesome_lists:
        task_ids.append(
            async_task(
                "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
                awesome_list.id,
                limit=limit_per_list,
                daily_limit=resolved_daily_limit,
                group="Daily awesome-list missing repo discovery",
            )
        )

    logger.info(
        "awesome_list_missing_repo_syncs_queued",
        queued_count=len(task_ids),
        limit_per_list=limit_per_list,
        daily_limit=resolved_daily_limit,
    )
    return {
        "queued": len(task_ids),
        "task_ids": task_ids,
        "daily_limit": resolved_daily_limit,
    }


def enqueue_missing_repositories_for_awesome_list_task(
    awesome_list_id: int, limit: int | None = None, daily_limit: int | None = None
):
    awesome_list = AwesomeList.objects.get(id=awesome_list_id)
    resolved_daily_limit = _daily_missing_repository_limit(daily_limit)
    try:
        logger.info(
            "awesome_list_missing_repo_discovery_task_started",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            limit=limit,
            daily_limit=resolved_daily_limit,
        )
        result = discover_missing_awesome_list_repositories(awesome_list, limit=limit)
        task_ids = []
        budget_exhausted = False
        for repo_full_name in result["missing"]:
            if not _try_reserve_daily_missing_repository_slot(resolved_daily_limit):
                budget_exhausted = True
                break
            task_ids.append(
                async_task(
                    "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
                    awesome_list.id,
                    repo_full_name,
                    group="Add missing awesome-list repos",
                )
            )

        result["queued"] = len(task_ids)
        result["task_ids"] = task_ids
        result["daily_limit"] = resolved_daily_limit
        result["budget_exhausted"] = budget_exhausted
        logged_result = {
            **result,
            "missing": result["missing"][:25],
            "task_ids": task_ids[:25],
        }
        logger.info(
            "awesome_list_missing_repo_discovery_task_finished",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            result=logged_result,
        )
        return result
    except Exception as exc:
        awesome_list.last_error = str(exc)
        awesome_list.save(update_fields=["last_error", "updated_at"])
        logger.error(
            "awesome_list_missing_repo_discovery_task_failed",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            error=str(exc),
            exc_info=True,
        )
        raise


def add_missing_repository_to_awesome_list_task(awesome_list_id: int, repo_full_name: str):
    awesome_list = AwesomeList.objects.get(id=awesome_list_id)
    try:
        logger.info(
            "awesome_list_missing_repo_add_task_started",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            repo_full_name=repo_full_name,
        )
        result = add_repository_to_awesome_list(awesome_list, repo_full_name)
        logger.info(
            "awesome_list_missing_repo_add_task_finished",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            repo_full_name=repo_full_name,
            result=result,
        )
        return result
    except Exception as exc:
        awesome_list.last_error = str(exc)
        awesome_list.save(update_fields=["last_error", "updated_at"])
        logger.error(
            "awesome_list_missing_repo_add_task_failed",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            repo_full_name=repo_full_name,
            error=str(exc),
            exc_info=True,
        )
        raise


def refresh_repository_task(
    repository_id: int,
    full_name: str,
    *,
    include_readme: bool = True,
):
    logger.info(
        "repository_refresh_task_started",
        repository_id=repository_id,
        repository_full_name=full_name,
    )
    try:
        refreshed = upsert_repository_from_github(full_name, include_readme=include_readme)
        logger.info(
            "repository_refresh_task_finished",
            requested_repository_id=repository_id,
            repository_id=refreshed.id,
            repository_full_name=refreshed.full_name,
        )
        return {
            "repository_id": refreshed.id,
            "full_name": refreshed.full_name,
        }
    except Exception as exc:
        if is_github_rate_limit_error(exc):
            logger.warning(
                "repository_refresh_task_stopped_for_rate_limit",
                repository_id=repository_id,
                repository_full_name=full_name,
                error=str(exc),
            )
            return {
                "repository_id": repository_id,
                "full_name": full_name,
                "stopped_for_rate_limit": True,
            }
        logger.error(
            "repository_refresh_task_failed",
            repository_id=repository_id,
            repository_full_name=full_name,
            error=str(exc),
            exc_info=True,
        )
        raise


def refresh_repositories_task(
    limit: int | None = None,
    *,
    include_readme: bool = False,
    min_rate_limit_remaining: int | None = None,
):
    total_repositories = Repository.objects.count()
    refresh_limit = (
        limit if limit is not None else daily_repository_refresh_limit(total_repositories)
    )
    min_remaining = (
        settings.GITHUB_REPOSITORY_REFRESH_MIN_RATE_LIMIT_REMAINING
        if min_rate_limit_remaining is None
        else min_rate_limit_remaining
    )
    queryset = Repository.objects.order_by("last_synced_at", "full_name").values_list(
        "id",
        "full_name",
    )
    refresh_limit = _available_repository_refresh_limit(refresh_limit, min_remaining)
    queryset = queryset[:refresh_limit]

    queued = []
    for repository_id, full_name in queryset.iterator():
        if _github_refresh_budget_exhausted(min_remaining):
            logger.warning(
                "repository_refresh_stopped_for_rate_limit_budget",
                remaining=github_rate_limit_remaining(),
                min_remaining=min_remaining,
                queued=len(queued),
                limit=refresh_limit,
            )
            break

        task_id = async_task(
            "apps.repos.tasks.refresh_repository_task",
            repository_id,
            full_name,
            include_readme=include_readme,
            group="Refresh repositories",
        )
        queued.append(
            {
                "repository_id": repository_id,
                "full_name": full_name,
                "task_id": task_id,
            }
        )

    logger.info(
        "repository_refresh_fanout_finished",
        queued=len(queued),
        limit=refresh_limit,
        total_repositories=total_repositories,
        include_readme=include_readme,
        rate_limit_remaining=github_rate_limit_remaining(),
    )
    return {
        "queued": len(queued),
        "limit": refresh_limit,
        "total_repositories": total_repositories,
        "include_readme": include_readme,
        "rate_limit_remaining": github_rate_limit_remaining(),
        "repositories": queued[:25],
    }
