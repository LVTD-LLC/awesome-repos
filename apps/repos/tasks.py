from math import ceil

from django.conf import settings
from django_q.tasks import async_task

from apps.repos.models import AwesomeList, Repository
from apps.repos.services import (
    add_repository_to_awesome_list,
    discover_missing_awesome_list_repositories,
    github_rate_limit_remaining,
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


def _github_refresh_budget_exhausted(min_remaining: int | None) -> bool:
    if min_remaining is None:
        return False

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
    failures = []
    checked_lists = 0
    remaining_budget = _daily_missing_repository_limit(daily_limit)
    awesome_lists = AwesomeList.objects.filter(is_active=True).order_by("name")
    for awesome_list in awesome_lists:
        if remaining_budget <= 0:
            break

        try:
            result = discover_missing_awesome_list_repositories(
                awesome_list,
                limit=limit_per_list,
            )
        except Exception as exc:  # noqa: BLE001 - one bad list should not block discovery
            failures.append({"awesome_list": awesome_list.slug, "error": str(exc)})
            awesome_list.last_error = str(exc)
            awesome_list.save(update_fields=["last_error", "updated_at"])
            if is_github_rate_limit_error(exc):
                logger.warning(
                    "awesome_list_missing_repo_syncs_stopped_for_rate_limit",
                    awesome_list_id=awesome_list.id,
                    awesome_list_slug=awesome_list.slug,
                    error=str(exc),
                )
                break
            logger.error(
                "awesome_list_missing_repo_discovery_failed",
                awesome_list_id=awesome_list.id,
                awesome_list_slug=awesome_list.slug,
                error=str(exc),
                exc_info=True,
            )
            continue

        checked_lists += 1
        for repo_full_name in result["missing"]:
            if remaining_budget <= 0:
                break
            task_ids.append(
                async_task(
                    "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
                    awesome_list.id,
                    repo_full_name,
                    group="Add missing awesome-list repos",
                )
            )
            remaining_budget -= 1

    logger.info(
        "awesome_list_missing_repo_syncs_queued",
        queued_count=len(task_ids),
        limit_per_list=limit_per_list,
        daily_limit=_daily_missing_repository_limit(daily_limit),
        checked_lists=checked_lists,
        failure_count=len(failures),
    )
    return {
        "queued": len(task_ids),
        "task_ids": task_ids,
        "checked_lists": checked_lists,
        "failure_count": len(failures),
        "failures": failures[:25],
    }


def enqueue_missing_repositories_for_awesome_list_task(
    awesome_list_id: int, limit: int | None = None
):
    awesome_list = AwesomeList.objects.get(id=awesome_list_id)
    try:
        logger.info(
            "awesome_list_missing_repo_discovery_task_started",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            limit=limit,
        )
        result = discover_missing_awesome_list_repositories(awesome_list, limit=limit)
        task_ids = []
        for repo_full_name in result["missing"]:
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
    queryset = queryset[:refresh_limit]

    synced = 0
    refreshed_repositories = []
    failures = []
    stopped_for_rate_limit = False
    for repository_id, full_name in queryset.iterator():
        if _github_refresh_budget_exhausted(min_remaining):
            stopped_for_rate_limit = True
            logger.warning(
                "repository_refresh_stopped_for_rate_limit_budget",
                remaining=github_rate_limit_remaining(),
                min_remaining=min_remaining,
                synced=synced,
                limit=refresh_limit,
            )
            break

        try:
            refreshed = upsert_repository_from_github(full_name, include_readme=include_readme)
        except Exception as exc:  # noqa: BLE001 - keep one bad repo from killing a batch
            failures.append(
                {"repository_id": repository_id, "full_name": full_name, "error": str(exc)}
            )
            if is_github_rate_limit_error(exc):
                stopped_for_rate_limit = True
                logger.warning(
                    "repository_refresh_stopped_for_rate_limit_error",
                    repository_id=repository_id,
                    repository_full_name=full_name,
                    error=str(exc),
                    synced=synced,
                    limit=refresh_limit,
                )
                break
            logger.error(
                "repository_refresh_failed",
                repository_id=repository_id,
                repository_full_name=full_name,
                error=str(exc),
                exc_info=True,
            )
            continue

        synced += 1
        refreshed_repositories.append(
            {
                "repository_id": refreshed.id,
                "full_name": refreshed.full_name,
            }
        )

    logger.info(
        "repository_refresh_batch_finished",
        synced=synced,
        failure_count=len(failures),
        limit=refresh_limit,
        total_repositories=total_repositories,
        include_readme=include_readme,
        stopped_for_rate_limit=stopped_for_rate_limit,
        rate_limit_remaining=github_rate_limit_remaining(),
    )
    return {
        "synced": synced,
        "failure_count": len(failures),
        "failures": failures[:25],
        "limit": refresh_limit,
        "total_repositories": total_repositories,
        "include_readme": include_readme,
        "stopped_for_rate_limit": stopped_for_rate_limit,
        "rate_limit_remaining": github_rate_limit_remaining(),
        "repositories": refreshed_repositories[:25],
    }
