from apps.repos.models import AwesomeList, Repository
from apps.repos.services import refresh_repositories, sync_awesome_list
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


def refresh_repositories_task(limit: int | None = None):
    return refresh_repositories(
        Repository.objects.order_by("last_synced_at", "full_name"),
        limit=limit,
    )
