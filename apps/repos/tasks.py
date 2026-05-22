from django_q.tasks import async_task

from apps.repos.models import AwesomeList, Repository
from apps.repos.services import (
    add_repository_to_awesome_list,
    discover_missing_awesome_list_repositories,
    sync_awesome_list,
    upsert_repository_from_github,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


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
    task_ids = []
    awesome_lists = AwesomeList.objects.filter(is_active=True).order_by("name")
    for awesome_list in awesome_lists:
        task_ids.append(
            async_task(
                "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
                awesome_list.id,
                limit=limit_per_list,
                group="Daily awesome-list missing repo discovery",
            )
        )

    logger.info(
        "awesome_list_missing_repo_syncs_queued",
        queued_count=len(task_ids),
        limit_per_list=limit_per_list,
    )
    return {"queued": len(task_ids), "task_ids": task_ids}


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


def refresh_repository_task(repository_id: int, full_name: str):
    logger.info(
        "repository_refresh_task_started",
        repository_id=repository_id,
        repository_full_name=full_name,
    )
    try:
        refreshed = upsert_repository_from_github(full_name)
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


def refresh_repositories_task(limit: int | None = None):
    queryset = Repository.objects.order_by("last_synced_at", "full_name").values_list(
        "id",
        "full_name",
    )
    if limit is not None:
        queryset = queryset[:limit]

    queued = []
    for repository_id, full_name in queryset.iterator():
        task_id = async_task(
            "apps.repos.tasks.refresh_repository_task",
            repository_id,
            full_name,
            group="Refresh repositories",
        )
        queued.append(
            {
                "repository_id": repository_id,
                "full_name": full_name,
                "task_id": task_id,
            }
        )

    logger.info("repository_refresh_fanout_finished", queued=len(queued), limit=limit)
    return {
        "queued": len(queued),
        "repositories": queued[:25],
    }
