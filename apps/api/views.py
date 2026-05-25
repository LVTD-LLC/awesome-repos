from django.core.cache import cache
from django.db import connection, transaction
from django.http import HttpRequest
from django.shortcuts import get_object_or_404
from django_q.tasks import async_task
from ninja import NinjaAPI, Query
from ninja.errors import HttpError
from ninja.responses import Status

from apps.api.auth import api_key_auth, session_auth, superuser_api_auth
from apps.api.schemas import (
    AwesomeListCreateIn,
    AwesomeListDetailOut,
    AwesomeListMutationOut,
    AwesomeListSearchOut,
    QueuedTaskOut,
    RepositoryDetailOut,
    RepositorySearchOut,
    SubmitFeedbackIn,
    SubmitFeedbackOut,
    UserInfoOut,
    UserSettingsOut,
)
from apps.api.services import (
    serialize_user_info,
)
from apps.core.models import Feedback
from apps.repos.forms import AwesomeListCreateForm
from apps.repos.models import AwesomeList, Repository
from apps.repos.search_services import (
    DEFAULT_API_PAGE_SIZE,
    get_awesome_list_detail_payload,
    get_awesome_list_repository_options_payload,
    get_repository_detail_payload,
    search_awesome_list_repositories_payload,
    search_awesome_lists_payload,
    search_repositories_payload,
    serialize_awesome_list_summary,
)
from apps.repos.views import (
    AWESOME_LIST_SCAN_TASK_GROUP,
    MISSING_REPOSITORY_DISCOVERY_TASK_GROUP,
    REPOSITORY_REFRESH_TASK_GROUP,
)
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

api = NinjaAPI()
REPOSITORY_SEARCH_PAGE_SIZE = DEFAULT_API_PAGE_SIZE
AWESOME_LIST_SEARCH_PAGE_SIZE = DEFAULT_API_PAGE_SIZE
AWESOME_LIST_REPOSITORY_PAGE_SIZE = 50


@api.get("/healthcheck", auth=None, include_in_schema=False, tags=["private"])
def healthcheck(request: HttpRequest):
    """
    Comprehensive healthcheck endpoint for monitoring and load balancers.

    Checks database and Redis connectivity.

    Returns:
    - 200 OK if all services are healthy
    - 503 if any service is down

    NOTE: We intentionally return boolean health fields (instead of "healthy"/"unhealthy"
    strings) to make healthcheck consumption trivial for load balancers and scripts.
    """

    checks = {
        "database": False,
        "redis": False,
    }

    # Check database connectivity
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["database"] = True
    except Exception as e:
        logger.error(
            "Healthcheck failed: Database connection error",
            error=str(e),
            exc_info=True,
        )

    # Check Redis connectivity
    try:
        cache_key = "healthcheck_test"
        cache_value = "ok"
        cache.set(cache_key, cache_value, timeout=10)
        retrieved_value = cache.get(cache_key)

        if retrieved_value == cache_value:
            checks["redis"] = True
        else:
            logger.error(
                "Healthcheck failed: Redis value mismatch",
                expected=cache_value,
                retrieved=retrieved_value,
            )
    except Exception as e:
        logger.error(
            "Healthcheck failed: Redis connection error",
            error=str(e),
            exc_info=True,
        )

    healthy = all(checks.values())
    payload = {
        "healthy": healthy,
        "checks": checks,
    }

    if healthy:
        logger.info("Healthcheck passed", **checks)
        return payload

    logger.error("Healthcheck failed", **checks)
    return 503, payload


@api.post(
    "/submit-feedback",
    response=SubmitFeedbackOut,
    auth=[session_auth],
    include_in_schema=False,
    tags=["private"],
)
def submit_feedback(request: HttpRequest, data: SubmitFeedbackIn):
    profile = request.auth
    try:
        Feedback.objects.create(profile=profile, feedback=data.feedback, page=data.page)
        return {"status": True, "message": "Feedback submitted successfully"}
    except Exception as e:
        logger.error("Failed to submit feedback", error=str(e), profile_id=profile.id)
        return {"status": False, "message": "Failed to submit feedback. Please try again."}


@api.get(
    "/user",
    response=UserInfoOut,
    auth=api_key_auth,
    tags=["user"],
)
def get_user_info(request: HttpRequest):
    """Return safe profile and account details for the authenticated API key."""
    return serialize_user_info(request.auth)


@api.get(
    "/repositories",
    response=RepositorySearchOut,
    auth=api_key_auth,
    tags=["repositories"],
)
def search_repositories(
    request: HttpRequest,
    q: str = "",
    mode: str = "",
    list_slug: str = Query("", alias="list"),
    language: str = "",
    topic: str = "",
    generated_tag: str = "",
    min_stars: int | None = None,
    updated_days: int | None = None,
    archived: str = "",
    ai_development: str = "",
    sort: str = "stars",
    page: int = 1,
    page_size: int = REPOSITORY_SEARCH_PAGE_SIZE,
):
    """Search indexed GitHub repositories with the same filters as the UI."""
    return search_repositories_payload(
        q=q,
        mode=mode,
        list_slug=list_slug,
        language=language,
        topic=topic,
        generated_tag=generated_tag,
        min_stars=min_stars,
        updated_days=updated_days,
        archived=archived,
        ai_development=ai_development,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@api.get(
    "/repositories/{owner}/{name}",
    response=RepositoryDetailOut,
    auth=api_key_auth,
    tags=["repositories"],
)
def get_repository(request: HttpRequest, owner: str, name: str):
    """Return repository metadata, list membership, growth history, and similar repos."""
    return get_repository_detail_payload(owner=owner, name=name)


@api.get(
    "/awesome-lists",
    response=AwesomeListSearchOut,
    auth=api_key_auth,
    tags=["awesome-lists"],
)
def search_awesome_lists(
    request: HttpRequest,
    q: str = "",
    sort: str = "stars",
    page: int = 1,
    page_size: int = AWESOME_LIST_SEARCH_PAGE_SIZE,
):
    """Search active awesome lists with the same search and sort controls as the UI."""
    return search_awesome_lists_payload(q=q, sort=sort, page=page, page_size=page_size)


@api.post(
    "/awesome-lists",
    response={201: AwesomeListMutationOut},
    auth=superuser_api_auth,
    include_in_schema=False,
    tags=["awesome-lists"],
)
def create_awesome_list(request: HttpRequest, data: AwesomeListCreateIn):
    """Create an awesome-list source and optionally queue its first scan."""
    form = AwesomeListCreateForm(data={"source_url": data.source_url})
    if not form.is_valid():
        errors = form.errors.get_json_data()
        message = errors.get("source_url", [{"message": "Invalid awesome-list URL."}])[0]["message"]
        raise HttpError(400, message)

    awesome_list = form.save()
    queued = False
    if data.queue_scan:
        transaction.on_commit(
            lambda: async_task(
                "apps.repos.tasks.sync_awesome_list_task",
                awesome_list.id,
                group=AWESOME_LIST_SCAN_TASK_GROUP,
            )
        )
        queued = True

    return Status(
        201,
        {
            "queued": queued,
            "message": (
                f"Added {awesome_list.name} and queued a scan."
                if queued
                else f"Added {awesome_list.name}."
            ),
            "awesome_list": serialize_awesome_list_summary(awesome_list),
        },
    )


@api.get(
    "/awesome-lists/{slug}",
    response=AwesomeListDetailOut,
    auth=api_key_auth,
    tags=["awesome-lists"],
)
def get_awesome_list(request: HttpRequest, slug: str):
    """Return awesome-list metadata and aggregate stats for indexed repositories."""
    return get_awesome_list_detail_payload(slug=slug)


@api.get(
    "/awesome-lists/{slug}/repositories",
    response=RepositorySearchOut,
    auth=api_key_auth,
    tags=["awesome-lists"],
)
def search_awesome_list_repositories(
    request: HttpRequest,
    slug: str,
    q: str = "",
    language: str = "",
    topic: str = "",
    generated_tag: str = "",
    min_stars: int | None = None,
    updated_days: int | None = None,
    archived: str = "",
    ai_development: str = "",
    sort: str = "stars",
    page: int = 1,
    page_size: int = AWESOME_LIST_REPOSITORY_PAGE_SIZE,
):
    """Search repositories indexed from one awesome list."""
    return search_awesome_list_repositories_payload(
        slug=slug,
        q=q,
        language=language,
        topic=topic,
        generated_tag=generated_tag,
        min_stars=min_stars,
        updated_days=updated_days,
        archived=archived,
        ai_development=ai_development,
        sort=sort,
        page=page,
        page_size=page_size,
    )


@api.get(
    "/awesome-lists/{slug}/repository-options",
    auth=api_key_auth,
    tags=["awesome-lists"],
)
def get_awesome_list_repository_options(request: HttpRequest, slug: str):
    """Return filter options used by the awesome-list repository browser."""
    return get_awesome_list_repository_options_payload(slug=slug)


@api.post(
    "/awesome-lists/{slug}/rescan",
    response=QueuedTaskOut,
    auth=superuser_api_auth,
    include_in_schema=False,
    tags=["awesome-lists"],
)
def queue_awesome_list_rescan(request: HttpRequest, slug: str):
    """Queue a full rescan for an active awesome list."""
    awesome_list = get_object_or_404(AwesomeList.objects.filter(is_active=True), slug=slug)
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.sync_awesome_list_task",
            awesome_list.id,
            group=AWESOME_LIST_SCAN_TASK_GROUP,
        )
    )
    return {
        "queued": True,
        "message": f"Queued a rescan for {awesome_list.name}.",
        "task": "apps.repos.tasks.sync_awesome_list_task",
    }


@api.post(
    "/awesome-lists/{slug}/discover-missing",
    response=QueuedTaskOut,
    auth=superuser_api_auth,
    include_in_schema=False,
    tags=["awesome-lists"],
)
def queue_awesome_list_missing_repo_discovery(request: HttpRequest, slug: str):
    """Queue missing repository discovery for an active awesome list."""
    awesome_list = get_object_or_404(AwesomeList.objects.filter(is_active=True), slug=slug)
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
            awesome_list.id,
            group=MISSING_REPOSITORY_DISCOVERY_TASK_GROUP,
        )
    )
    return {
        "queued": True,
        "message": f"Queued missing repository discovery for {awesome_list.name}.",
        "task": "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
    }


@api.post(
    "/repositories/{owner}/{name}/rescan",
    response=QueuedTaskOut,
    auth=superuser_api_auth,
    include_in_schema=False,
    tags=["repositories"],
)
def queue_repository_rescan(request: HttpRequest, owner: str, name: str):
    """Queue a metadata and README refresh for a repository."""
    repository = get_object_or_404(Repository, full_name=f"{owner}/{name}")
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.refresh_repository_task",
            repository.id,
            repository.full_name,
            group=REPOSITORY_REFRESH_TASK_GROUP,
        )
    )
    return {
        "queued": True,
        "message": f"Queued a rescan for {repository.full_name}.",
        "task": "apps.repos.tasks.refresh_repository_task",
    }


@api.get(
    "/user/settings",
    response=UserSettingsOut,
    auth=[session_auth],
    include_in_schema=False,
    tags=["private"],
)
def user_settings(request: HttpRequest):
    profile = request.auth
    try:
        profile_data = {}
        data = {"profile": profile_data}

        return data
    except Exception as e:
        logger.error(
            "Error fetching user settings",
            error=str(e),
            profile_id=profile.id,
            exc_info=True,
        )
        raise HttpError(500, "An unexpected error occurred.") from e
