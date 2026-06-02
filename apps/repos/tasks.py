from datetime import datetime
from math import ceil

from allauth.socialaccount.models import SocialToken
from django.conf import settings
from django.core.cache import cache
from django.db.models import Q
from django.utils import timezone
from django_q.tasks import async_task

from apps.core.models import Profile
from apps.repos.models import AwesomeList, Repository
from apps.repos.newsletters import (
    generate_due_newsletter_issues,
    generate_repository_newsletter_issue,
    newsletter_period_for_cadence,
    poll_repository_commits,
    poll_tracked_repositories,
    send_issue_to_subscribers,
    summarize_pending_commits,
)
from apps.repos.services import (
    GitHubTokenUnavailable,
    add_repository_to_awesome_list,
    github_rate_limit_remaining,
    github_rate_limit_status,
    github_repository_sync_token_from_pool,
    github_repository_sync_token_pool,
    github_repository_sync_token_pool_size,
    import_starred_repositories_for_profile,
    is_github_rate_limit_error,
)
from apps.repos.tags import repository_tagging_configured, tag_repository_batch
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
    if github_repository_sync_token_pool_size() > 1:
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
    if github_repository_sync_token_pool_size() > 1:
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
        result = awesome_list.sync_from_source(limit=limit)
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
        result = awesome_list.discover_missing_repositories_from_source(limit=limit)
        task_ids = []
        budget_exhausted = False
        sync_token_pool = github_repository_sync_token_pool()
        for repo_full_name in result["missing"]:
            if not _try_reserve_daily_missing_repository_slot(resolved_daily_limit):
                budget_exhausted = True
                break
            task_kwargs = {"group": "Add missing awesome-list repos"}
            repository_sync_token = github_repository_sync_token_from_pool(
                sync_token_pool,
                len(task_ids),
            )
            if repository_sync_token:
                task_kwargs["github_access_token"] = repository_sync_token
            task_ids.append(
                async_task(
                    "apps.repos.tasks.add_missing_repository_to_awesome_list_task",
                    awesome_list.id,
                    repo_full_name,
                    **task_kwargs,
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


def add_missing_repository_to_awesome_list_task(
    awesome_list_id: int,
    repo_full_name: str,
    *,
    github_access_token: str | None = None,
):
    awesome_list = AwesomeList.objects.get(id=awesome_list_id)
    try:
        logger.info(
            "awesome_list_missing_repo_add_task_started",
            awesome_list_id=awesome_list_id,
            awesome_list_slug=awesome_list.slug,
            repo_full_name=repo_full_name,
        )
        kwargs = {}
        if github_access_token:
            kwargs["github_access_token"] = github_access_token
        result = add_repository_to_awesome_list(awesome_list, repo_full_name, **kwargs)
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


def _starred_repository_import_limit(limit: int | None) -> int | None:
    if limit is not None:
        return max(0, limit) or None
    configured_limit = max(0, settings.GITHUB_STARRED_REPOSITORY_IMPORT_LIMIT)
    return configured_limit or None


def import_starred_repositories_task(
    profile_id: int,
    limit: int | None = None,
    *,
    refresh_existing: bool = True,
):
    profile = Profile.objects.select_related("user").get(id=profile_id)
    resolved_limit = _starred_repository_import_limit(limit)
    logger.info(
        "github_starred_repository_import_task_started",
        profile_id=profile_id,
        user_id=profile.user_id,
        limit=resolved_limit,
        refresh_existing=refresh_existing,
    )
    try:
        result = import_starred_repositories_for_profile(
            profile,
            limit=resolved_limit,
            refresh_existing=refresh_existing,
        )
        logger.info(
            "github_starred_repository_import_task_finished",
            profile_id=profile_id,
            result={
                **result,
                "failures": result["failures"][:25],
            },
        )
        return result
    except GitHubTokenUnavailable as exc:
        profile.github_starred_repos_last_error = str(exc)
        profile.save(update_fields=["github_starred_repos_last_error", "updated_at"])
        logger.warning(
            "github_starred_repository_import_token_unavailable",
            profile_id=profile_id,
            user_id=profile.user_id,
            error=str(exc),
        )
        return {
            "profile_id": profile_id,
            "token_unavailable": True,
            "error": str(exc),
        }
    except Exception as exc:
        profile.github_starred_repos_last_error = str(exc)
        profile.save(update_fields=["github_starred_repos_last_error", "updated_at"])
        if is_github_rate_limit_error(exc):
            logger.warning(
                "github_starred_repository_import_stopped_for_rate_limit",
                profile_id=profile_id,
                user_id=profile.user_id,
                error=str(exc),
            )
            return {
                "profile_id": profile_id,
                "stopped_for_rate_limit": True,
                "error": str(exc),
            }
        logger.error(
            "github_starred_repository_import_task_failed",
            profile_id=profile_id,
            user_id=profile.user_id,
            error=str(exc),
            exc_info=True,
        )
        raise


def enqueue_starred_repository_imports_task(
    limit_per_user: int | None = None,
    *,
    refresh_existing: bool = True,
):
    now = timezone.now()
    token_user_ids = (
        SocialToken.objects.filter(account__provider="github")
        .exclude(token="")
        .filter(Q(expires_at__isnull=True) | Q(expires_at__gt=now))
        .values("account__user_id")
    )
    profile_ids = (
        Profile.objects.filter(
            github_starred_repos_import_enabled=True,
            user_id__in=token_user_ids,
        )
        .order_by("id")
        .values_list("id", flat=True)
    )
    task_ids = []
    for profile_id in profile_ids:
        task_ids.append(
            async_task(
                "apps.repos.tasks.import_starred_repositories_task",
                profile_id,
                limit=limit_per_user,
                refresh_existing=refresh_existing,
                group="Import GitHub starred repositories",
            )
        )

    logger.info(
        "github_starred_repository_imports_queued",
        queued=len(task_ids),
        limit_per_user=limit_per_user,
        refresh_existing=refresh_existing,
    )
    return {
        "queued": len(task_ids),
        "task_ids": task_ids,
        "limit_per_user": limit_per_user,
        "refresh_existing": refresh_existing,
    }


def refresh_repository_task(
    repository_id: int,
    full_name: str,
    *,
    include_readme: bool | None = None,
    github_access_token: str | None = None,
):
    # Keep legacy kwargs in the signature so older queued jobs still deserialize.
    logger.info(
        "repository_refresh_task_started",
        repository_id=repository_id,
        repository_full_name=full_name,
    )
    try:
        if github_access_token:
            refreshed = Repository.sync_from_source(
                full_name,
                github_access_token=github_access_token,
            )
        else:
            refreshed = Repository.sync_from_source(full_name)
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
    include_readme: bool | None = None,
    min_rate_limit_remaining: int | None = None,
):
    # Keep include_readme in the signature so older queued jobs still deserialize;
    # scheduled repository refreshes should always use the model's full sync path.
    include_readme = True
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
    sync_token_pool = github_repository_sync_token_pool()
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

        task_kwargs = {"group": "Refresh repositories"}
        repository_sync_token = github_repository_sync_token_from_pool(
            sync_token_pool,
            len(queued),
        )
        if repository_sync_token:
            task_kwargs["github_access_token"] = repository_sync_token
        task_id = async_task(
            "apps.repos.tasks.refresh_repository_task",
            repository_id,
            full_name,
            **task_kwargs,
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


def tag_repositories_task(limit: int | None = None, *, force: bool = False):
    if not repository_tagging_configured():
        logger.info(
            "repository_tagging_task_skipped",
            reason="tagging_not_configured",
        )
        return {
            "tagged": 0,
            "skipped": 0,
            "unchanged": 0,
            "failure_count": 0,
            "failures": [],
            "skipped_reason": "tagging_not_configured",
        }

    resolved_limit = settings.REPOSITORY_DAILY_TAGGING_LIMIT if limit is None else limit
    result = tag_repository_batch(limit=resolved_limit, force=force)
    logger.info(
        "repository_tagging_task_finished",
        limit=resolved_limit,
        force=force,
        result=result,
    )
    return result


def poll_tracked_repository_commits_task(repository_id: int):
    repository = Repository.objects.get(id=repository_id)
    logger.info(
        "repository_newsletter_commit_poll_task_started",
        repository_id=repository_id,
        repository_full_name=repository.full_name,
    )
    result = poll_repository_commits(repository)
    logger.info(
        "repository_newsletter_commit_poll_task_finished",
        repository_id=repository_id,
        repository_full_name=repository.full_name,
        result=result,
    )
    return result


def poll_tracked_repositories_task(limit: int | None = None):
    result = poll_tracked_repositories(limit=limit)
    logger.info("repository_newsletter_commit_poll_batch_finished", result=result)
    return result


def summarize_newsletter_commits_task(limit: int | None = None):
    result = summarize_pending_commits(limit=limit)
    logger.info("repository_newsletter_commit_summary_batch_finished", result=result)
    return result


def generate_repository_newsletter_issue_task(
    repository_id: int,
    cadence: str,
    *,
    reference_date: str | None = None,
    send: bool = True,
):
    repository = Repository.objects.get(id=repository_id)
    parsed_reference_date = (
        datetime.fromisoformat(reference_date).date() if reference_date else None
    )
    period = newsletter_period_for_cadence(cadence, parsed_reference_date)
    issue = generate_repository_newsletter_issue(
        repository,
        cadence=cadence,
        period=period,
    )
    delivery_result = {"sent": 0}
    if issue is not None and send:
        delivery_result = send_issue_to_subscribers(issue)
    result = {
        "repository_id": repository_id,
        "cadence": cadence,
        "issue_id": issue.id if issue else None,
        "sent": delivery_result.get("sent", 0),
        "failed": delivery_result.get("failed", 0),
    }
    logger.info(
        "repository_newsletter_issue_task_finished",
        repository_id=repository_id,
        repository_full_name=repository.full_name,
        result=result,
    )
    return result


def generate_weekly_newsletters_task(reference_date: str | None = None):
    parsed_reference_date = (
        datetime.fromisoformat(reference_date).date() if reference_date else None
    )
    result = generate_due_newsletter_issues(
        cadence="weekly",
        reference_date=parsed_reference_date,
    )
    logger.info("repository_weekly_newsletters_finished", result=result)
    return result


def generate_monthly_newsletters_task(reference_date: str | None = None):
    parsed_reference_date = (
        datetime.fromisoformat(reference_date).date() if reference_date else None
    )
    result = generate_due_newsletter_issues(
        cadence="monthly",
        reference_date=parsed_reference_date,
    )
    logger.info("repository_monthly_newsletters_finished", result=result)
    return result
