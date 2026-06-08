from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.contrib.syndication.views import Feed
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView
from django_q.tasks import async_task

from apps.core.analytics import queue_track_event
from apps.core.models import Profile
from apps.repos.badges import repository_badge_svg
from apps.repos.cache import (
    PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_KEY,
    PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_TIMEOUT_SECONDS,
)
from apps.repos.forms import AwesomeListRequestForm, NewsletterSubscriptionForm
from apps.repos.models import (
    AwesomeList,
    NewsletterCadence,
    NewsletterSubscription,
    Repository,
    RepositoryLike,
    RepositoryNewsletterIssue,
    RepositorySnapshot,
)
from apps.repos.newsletters import (
    disable_repository_newsletter_tracking,
    unsubscribe_newsletter,
    upsert_newsletter_subscription,
)
from apps.repos.search_services import (
    awesome_list_search_queryset,
    visible_awesome_list_item_count,
)
from apps.repos.services import (
    awesome_list_directory_totals,
    awesome_list_history_chart_data,
    awesome_list_repository_queryset,
    minimum_age_cutoff,
    repository_history_chart_data,
    repository_json_value_counts,
    repository_performance_summary,
    repository_search_queryset,
    similar_repositories_for_repository,
    visible_repository_queryset,
    with_repository_like_state,
)
from apps.repos.stack_detection import package_manager_label, stack_label

AWESOME_LIST_SCAN_TASK_GROUP = "Scan awesome list"
MISSING_REPOSITORY_DISCOVERY_TASK_GROUP = "Manual awesome-list missing repo discovery"
REPOSITORY_REFRESH_TASK_GROUP = "Refresh repositories"
NEWSLETTER_COMMIT_POLL_TASK_GROUP = "Poll repository newsletter commits"
AWESOME_LIST_REQUEST_RATE_LIMIT = 5
AWESOME_LIST_REQUEST_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
AI_DEVELOPMENT_VISIBLE_PATH_LIMIT = 6
AI_DEVELOPMENT_DETAIL_PATH_LIMIT = 24
AI_DEVELOPMENT_VISIBLE_TOOL_LIMIT = 5


def _ai_development_signal_summary(signals):
    normalized_signals = []
    seen_paths = set()

    for signal in signals or []:
        if not isinstance(signal, dict):
            continue

        path = str(signal.get("path") or "").strip()
        if not path:
            continue

        path_key = path.lower()
        if path_key in seen_paths:
            continue

        seen_paths.add(path_key)
        kind = signal.get("kind") if signal.get("kind") in {"directory", "file"} else "file"
        tool = str(signal.get("tool") or "AI agent").strip() or "AI agent"
        normalized_signals.append(
            {
                "path": path,
                "path_key": path_key,
                "kind": kind,
                "kind_label": "dir" if kind == "directory" else "file",
                "tool": tool,
                "signal": signal.get("signal") or "",
            }
        )

    normalized_signals.sort(key=lambda item: item["path_key"])

    tool_counts = {}
    for signal in normalized_signals:
        tool_counts[signal["tool"]] = tool_counts.get(signal["tool"], 0) + 1

    tools = [
        {"name": tool, "count": count}
        for tool, count in sorted(tool_counts.items(), key=lambda item: item[0].lower())
    ]

    key_signals = []
    covered_prefixes = []
    for signal in normalized_signals:
        if any(signal["path_key"].startswith(prefix) for prefix in covered_prefixes):
            continue

        key_signals.append(signal)
        if signal["kind"] == "directory":
            covered_prefixes.append(f"{signal['path_key'].rstrip('/')}/")

    visible_signals = key_signals[:AI_DEVELOPMENT_VISIBLE_PATH_LIMIT]
    detail_signals = normalized_signals[:AI_DEVELOPMENT_DETAIL_PATH_LIMIT]
    total_count = len(normalized_signals)

    return {
        "has_signals": bool(normalized_signals),
        "total_count": total_count,
        "file_count": sum(1 for signal in normalized_signals if signal["kind"] == "file"),
        "directory_count": sum(1 for signal in normalized_signals if signal["kind"] == "directory"),
        "tools": tools,
        "visible_tools": tools[:AI_DEVELOPMENT_VISIBLE_TOOL_LIMIT],
        "extra_tool_count": max(len(tools) - AI_DEVELOPMENT_VISIBLE_TOOL_LIMIT, 0),
        "visible_signals": visible_signals,
        "hidden_signal_count": max(len(key_signals) - len(visible_signals), 0),
        "detail_signals": detail_signals,
        "detail_hidden_count": max(total_count - len(detail_signals), 0),
        "show_detail_signals": total_count > len(visible_signals),
    }


REPOSITORY_FILTER_PARAM_NAMES = (
    "q",
    "mode",
    "list",
    "language",
    "topic",
    "generated_tag",
    "framework",
    "stack",
    "package_manager",
    "min_stars",
    "updated_days",
    "unmaintained_days",
    "min_age_years",
    "min_velocity_percent",
    "min_star_growth_percent",
    "min_liability_percent",
    "archived",
    "ai_development",
    "sort_direction",
)
REPOSITORY_ADVANCED_FILTER_PARAM_NAMES = (
    "topic",
    "generated_tag",
    "framework",
    "stack",
    "package_manager",
    "min_stars",
    "unmaintained_days",
    "min_age_years",
    "min_velocity_percent",
    "min_star_growth_percent",
    "min_liability_percent",
    "archived",
    "ai_development",
)
REPOSITORY_SORT_LABELS = {
    "stars": "Stars",
    "forks": "Forks",
    "recent": "Recently updated",
    "created": "Recently created",
    "oldest": "Oldest first commit",
    "commits": "Commits",
    "velocity": "Commit velocity",
    "star_growth": "Star growth",
    "liability": "Star growth",
    "awesome": "Awesome-list mentions",
    "least_awesome": "Fewest list mentions",
    "name": "Name",
}
REPOSITORY_FILTER_LABELS = {
    "q": "Search",
    "mode": "Mode",
    "list": "List",
    "language": "Language",
    "topic": "Topic",
    "generated_tag": "Tag",
    "framework": "Framework",
    "stack": "Framework",
    "package_manager": "Package manager",
    "min_stars": "Min stars",
    "updated_days": "Updated",
    "unmaintained_days": "Unmaintained",
    "min_age_years": "Age",
    "min_velocity_percent": "Velocity",
    "min_star_growth_percent": "Star growth",
    "min_liability_percent": "Star growth",
    "archived": "Archived",
    "ai_development": "AI dev",
    "sort": "Sort",
    "sort_direction": "Direction",
}
REPOSITORY_FILTER_VALUE_LABELS = {
    "mode": {"semantic": "Semantic relevance"},
    "archived": {"yes": "Archived only", "no": "Active only"},
    "ai_development": {"yes": "Has signals", "no": "No signals"},
    "sort_direction": {"asc": "Ascending", "desc": "Descending"},
}
REPOSITORY_PERCENT_FILTER_PARAM_NAMES = (
    "min_velocity_percent",
    "min_star_growth_percent",
    "min_liability_percent",
)


def _require_superuser(request):
    if not request.user.is_superuser:
        raise PermissionDenied("Only superusers can queue repository scans.")


def _active_awesome_list_or_404(slug: str):
    return get_object_or_404(AwesomeList.objects.filter(is_active=True), slug=slug)


@login_required(login_url="account_login")
@require_POST
def queue_awesome_list_rescan(request, slug: str):
    _require_superuser(request)
    awesome_list = _active_awesome_list_or_404(slug)
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.sync_awesome_list_task",
            awesome_list.id,
            group=AWESOME_LIST_SCAN_TASK_GROUP,
        )
    )
    messages.success(request, f"Queued a rescan for {awesome_list.name}.")
    return redirect(awesome_list.get_absolute_url())


@login_required(login_url="account_login")
@require_POST
def queue_awesome_list_missing_repo_discovery(request, slug: str):
    _require_superuser(request)
    awesome_list = _active_awesome_list_or_404(slug)
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.enqueue_missing_repositories_for_awesome_list_task",
            awesome_list.id,
            group=MISSING_REPOSITORY_DISCOVERY_TASK_GROUP,
        )
    )
    messages.success(request, f"Queued missing repository discovery for {awesome_list.name}.")
    return redirect(awesome_list.get_absolute_url())


@login_required(login_url="account_login")
@require_POST
def queue_repository_rescan(request, owner: str, name: str):
    _require_superuser(request)
    repository = get_object_or_404(Repository, full_name=f"{owner}/{name}")
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.refresh_repository_task",
            repository.id,
            repository.full_name,
            group=REPOSITORY_REFRESH_TASK_GROUP,
        )
    )
    messages.success(request, f"Queued a rescan for {repository.full_name}.")
    return redirect(repository.get_absolute_url())


@login_required(login_url="account_login")
@require_POST
def upsert_repository_newsletter_subscription(request, owner: str, name: str):
    _require_superuser(request)
    repository = get_object_or_404(Repository, full_name=f"{owner}/{name}")
    form = NewsletterSubscriptionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose a valid delivery email and cadence.")
        return redirect(repository.get_absolute_url())

    subscription = upsert_newsletter_subscription(
        user=request.user,
        repository=repository,
        email=form.cleaned_data["email"],
        cadence=form.cleaned_data["cadence"],
    )
    transaction.on_commit(
        lambda: async_task(
            "apps.repos.tasks.poll_tracked_repository_commits_task",
            repository.id,
            group=NEWSLETTER_COMMIT_POLL_TASK_GROUP,
        )
    )
    messages.success(
        request,
        (
            f"{repository.full_name} newsletter is tracking and will send "
            f"{subscription.get_cadence_display().lower()} updates to {subscription.email}."
        ),
    )
    return redirect(repository.get_absolute_url())


@login_required(login_url="account_login")
@require_POST
def disable_repository_newsletter(request, owner: str, name: str):
    _require_superuser(request)
    repository = get_object_or_404(Repository, full_name=f"{owner}/{name}")
    disable_repository_newsletter_tracking(repository)
    messages.success(request, f"Stopped newsletter tracking for {repository.full_name}.")
    return redirect(repository.get_absolute_url())


@login_required(login_url="account_login")
@require_POST
def toggle_repository_like(request, owner: str, name: str):
    repository = get_object_or_404(Repository, full_name=f"{owner}/{name}")
    like, created = RepositoryLike.objects.get_or_create(
        repository=repository,
        user=request.user,
    )
    if created:
        repository.is_liked = True
    else:
        like.delete()
        repository.is_liked = False

    queue_track_event(
        event_name="repository_liked" if created else "repository_unliked",
        profile_id=request.user.profile.id,
        properties={
            "repository_id": repository.id,
            "repository_full_name": repository.full_name,
            "repository_language": repository.language,
            "repository_stars": repository.stars,
        },
        source_function="toggle_repository_like",
    )

    next_url = request.POST.get("next") or repository.get_absolute_url()
    if not url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = repository.get_absolute_url()

    if request.headers.get("HX-Request") == "true":
        return render(
            request,
            "repos/_repository_like_button.html",
            {
                "repository": repository,
                "next_url": next_url,
            },
        )

    return redirect(next_url)


def repository_badge(request, owner: str, name: str):
    repository = get_object_or_404(
        Repository.objects.annotate(awesome_count=Count("awesome_items", distinct=True)),
        full_name=f"{owner}/{name}",
    )
    try:
        days = int(request.GET.get("days", "7"))
    except ValueError:
        days = 7
    svg = repository_badge_svg(
        repository,
        metric=request.GET.get("metric", "stars"),
        theme=request.GET.get("theme", "light"),
        variant=request.GET.get("variant", "history"),
        days=days,
    )
    response = HttpResponse(svg, content_type="image/svg+xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=3600"
    response["X-Content-Type-Options"] = "nosniff"
    return response


REPOSITORY_BADGE_EMBED_OPTIONS = (
    {
        "key": "star-history",
        "label": "Star history",
        "query": {"metric": "stars"},
    },
    {
        "key": "commit-history",
        "label": "Commit history",
        "query": {"metric": "commits"},
    },
    {
        "key": "star-growth-7",
        "label": "7-day star growth",
        "query": {"metric": "stars", "variant": "growth", "days": "7"},
    },
    {
        "key": "star-growth-30",
        "label": "30-day star growth",
        "query": {"metric": "stars", "variant": "growth", "days": "30"},
    },
    {
        "key": "commit-velocity-7",
        "label": "7-day commit velocity",
        "query": {"metric": "commits", "variant": "growth", "days": "7"},
    },
    {
        "key": "commit-velocity-30",
        "label": "30-day commit velocity",
        "query": {"metric": "commits", "variant": "growth", "days": "30"},
    },
)


def repository_badge_url(request, repository: Repository, query: dict[str, str]) -> str:
    path = reverse("repos:repo_badge", kwargs={"owner": repository.owner, "name": repository.name})
    url = request.build_absolute_uri(path)
    if query:
        url = f"{url}?{urlencode(query)}"
    return url


def repository_badge_embed_context(request, repository: Repository) -> dict:
    detail_url = request.build_absolute_uri(repository.get_absolute_url())
    options = []
    for option in REPOSITORY_BADGE_EMBED_OPTIONS:
        badge_url = repository_badge_url(request, repository, option["query"])
        alt_text = f"{repository.full_name} {option['label']} on Awesome"
        markdown_alt = alt_text.replace("\\", "\\\\").replace("]", "\\]")
        options.append(
            {
                "key": option["key"],
                "label": option["label"],
                "badge_url": badge_url,
                "badge_markdown": f"[![{markdown_alt}]({badge_url})]({detail_url})",
            }
        )

    return {"options": options}


def awesome_list_request_client_ip(request) -> str:
    # X-Forwarded-For is only safe behind a trusted proxy that strips spoofed headers.
    return request.META.get("REMOTE_ADDR", "unknown")


def awesome_list_request_rate_limit_key(request) -> str:
    return f"awesome-list-request:{awesome_list_request_client_ip(request)}"


def labeled_repository_value_counts(field_name: str, labeler, **kwargs) -> list[dict]:
    return [
        {**row, "label": labeler(row["name"])}
        for row in repository_json_value_counts(field_name, **kwargs)
    ]


def public_repository_filter_options() -> dict:
    options = cache.get(PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_KEY)
    if options is not None:
        return options

    base_queryset = visible_repository_queryset()
    options = {
        "awesome_lists": list(
            AwesomeList.objects.filter(is_active=True)
            .annotate(repo_count=visible_awesome_list_item_count())
            .order_by("name")
        ),
        "languages": list(
            base_queryset.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        ),
        "topic_options": repository_json_value_counts("topics"),
        "generated_tag_options": repository_json_value_counts("generated_tags"),
        "stack_options": labeled_repository_value_counts("detected_stacks", stack_label),
        "package_manager_options": labeled_repository_value_counts(
            "package_managers",
            package_manager_label,
        ),
        "total_repositories": base_queryset.count(),
        "total_lists": AwesomeList.objects.filter(is_active=True).count(),
    }
    cache.set(
        PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_KEY,
        options,
        PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_TIMEOUT_SECONDS,
    )
    return options


def attach_repository_snapshot_counts(repositories):
    repository_list = list(repositories)
    repository_ids = [
        repository.pk
        for repository in repository_list
        if "snapshot_count" not in repository.__dict__
    ]
    if not repository_ids:
        return repository_list

    snapshot_counts = dict(
        RepositorySnapshot.objects.filter(repository_id__in=repository_ids)
        .values_list("repository_id")
        .annotate(total=Count("id"))
    )
    for repository in repository_list:
        if "snapshot_count" not in repository.__dict__:
            repository.snapshot_count = snapshot_counts.get(repository.pk, 0)
    return repository_list


def repository_search_params(request):
    params = request.GET.copy()
    params.pop("page", None)
    return params


def repository_filters_applied(params, *, include_sort: bool = False) -> bool:
    names = REPOSITORY_FILTER_PARAM_NAMES + (("sort",) if include_sort else ())
    return any(params.get(name) for name in names)


def repository_advanced_filters_applied(params) -> bool:
    return any(params.get(name) for name in REPOSITORY_ADVANCED_FILTER_PARAM_NAMES)


def repository_filter_remove_querystring(params, name: str) -> str:
    next_params = params.copy()
    next_params.pop("page", None)
    names_to_remove = {name}
    if name in {"framework", "stack"}:
        names_to_remove.update({"framework", "stack"})
    elif name in {"min_star_growth_percent", "min_liability_percent"}:
        names_to_remove.update({"min_star_growth_percent", "min_liability_percent"})
    elif name in {"sort", "sort_direction"}:
        names_to_remove.update({"sort", "sort_direction"})

    for remove_name in names_to_remove:
        next_params.pop(remove_name, None)
    return next_params.urlencode()


def _repository_filter_chip_value(
    name: str,
    value: str,
    sort_labels: dict[str, str],
) -> str | None:
    if name == "updated_days":
        return f"{value} days"
    if name == "unmaintained_days":
        return f"{value}+ days"
    if name == "min_age_years":
        return f"{value}+ years"
    if name in REPOSITORY_PERCENT_FILTER_PARAM_NAMES:
        return f"{value}%+"
    if name == "sort":
        return sort_labels.get(value)
    return REPOSITORY_FILTER_VALUE_LABELS.get(name, {}).get(value, value)


def active_repository_filter_chips(
    params,
    *,
    sort_labels: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    resolved_sort_labels = {**REPOSITORY_SORT_LABELS, **(sort_labels or {})}
    chips = []
    for name in (*REPOSITORY_FILTER_PARAM_NAMES, "sort"):
        if name == "stack" and params.get("framework"):
            continue
        if name == "min_liability_percent" and params.get("min_star_growth_percent"):
            continue
        value = (params.get(name) or "").strip()
        if not value:
            continue
        value = _repository_filter_chip_value(name, value, resolved_sort_labels)
        if value is None:
            continue
        chips.append(
            {
                "label": REPOSITORY_FILTER_LABELS[name],
                "value": value,
                "remove_querystring": repository_filter_remove_querystring(params, name),
            }
        )
    return chips


def repository_filter_context(
    *,
    request,
    base_queryset,
    search_url: str,
    reset_url: str,
    awesome_lists=None,
    awesome_list=None,
    profile=None,
    show_list_filter: bool = True,
    show_search_mode: bool = True,
    search_field_class: str = "",
    filter_id_prefix: str = "repo-filter",
    sort_labels: dict[str, str] | None = None,
    languages=None,
    topic_options=None,
    generated_tag_options=None,
    stack_options=None,
    package_manager_options=None,
):
    params = repository_search_params(request)
    if not show_list_filter:
        params.pop("list", None)
    if awesome_lists is None and show_list_filter:
        awesome_lists = (
            AwesomeList.objects.filter(is_active=True)
            .annotate(repo_count=visible_awesome_list_item_count())
            .order_by("name")
        )
    elif awesome_lists is None:
        awesome_lists = []
    if languages is None:
        languages = (
            base_queryset.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        )
    if topic_options is None:
        topic_options = repository_json_value_counts(
            "topics",
            awesome_list=awesome_list,
            profile=profile,
        )
    if generated_tag_options is None:
        generated_tag_options = repository_json_value_counts(
            "generated_tags",
            awesome_list=awesome_list,
            profile=profile,
        )
    if stack_options is None:
        stack_options = labeled_repository_value_counts(
            "detected_stacks",
            stack_label,
            awesome_list=awesome_list,
            profile=profile,
        )
    if package_manager_options is None:
        package_manager_options = labeled_repository_value_counts(
            "package_managers",
            package_manager_label,
            awesome_list=awesome_list,
            profile=profile,
        )
    active_filters = active_repository_filter_chips(
        params,
        sort_labels=sort_labels,
    )
    return {
        "params": params,
        "querystring": params.urlencode(),
        "filters_applied": repository_filters_applied(params),
        "advanced_repository_filters_applied": repository_advanced_filters_applied(params),
        "active_repository_filters": active_filters,
        "active_repository_filter_count": len(active_filters),
        "awesome_lists": awesome_lists,
        "languages": languages,
        "topic_options": topic_options,
        "generated_tag_options": generated_tag_options,
        "stack_options": stack_options,
        "package_manager_options": package_manager_options,
        "filter_id_prefix": filter_id_prefix,
        "search_field_class": search_field_class,
        "show_list_filter": show_list_filter,
        "show_search_mode": show_search_mode,
        "search_action_url": search_url,
        "search_reset_url": reset_url,
    }


class RepositorySearchView(ListView):
    template_name = "repos/search.html"
    context_object_name = "repositories"
    paginate_by = 30

    def get_queryset(self):
        return with_repository_like_state(
            repository_search_queryset(
                self.request.GET,
                include_snapshot_metrics=False,
            ),
            self.request.user,
        ).prefetch_related(
            "awesome_items__awesome_list",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["repositories"] = attach_repository_snapshot_counts(context["repositories"])
        context["object_list"] = context["repositories"]
        context["page_obj"].object_list = context["repositories"]
        search_page = self.request.GET.get("page") or "1"
        if (
            self.request.user.is_authenticated
            and repository_filters_applied(
                self.request.GET,
                include_sort=True,
            )
            and search_page == "1"
        ):
            params = repository_search_params(self.request)
            queue_track_event(
                event_name="search_performed",
                profile_id=self.request.user.profile.id,
                properties={
                    "query": params.get("q", ""),
                    "mode": params.get("mode", ""),
                    "results_count": context["page_obj"].paginator.count,
                    "sort": params.get("sort", ""),
                    "sort_direction": params.get("sort_direction", ""),
                    "search_scope": "public_repositories",
                    "filters_applied": repository_filters_applied(params, include_sort=False),
                },
                source_function="RepositorySearchView",
            )
        visible_repositories = visible_repository_queryset()
        filter_options = public_repository_filter_options()
        search_url = reverse("repos:search")
        context.update(
            repository_filter_context(
                request=self.request,
                base_queryset=visible_repositories,
                search_url=search_url,
                reset_url=search_url,
                awesome_lists=filter_options["awesome_lists"],
                languages=filter_options["languages"],
                topic_options=filter_options["topic_options"],
                generated_tag_options=filter_options["generated_tag_options"],
                stack_options=filter_options["stack_options"],
                package_manager_options=filter_options["package_manager_options"],
                filter_id_prefix="repo-filter",
            )
        )
        context["total_repositories"] = filter_options["total_repositories"]
        context["total_lists"] = filter_options["total_lists"]
        context["search_eyebrow"] = "GitHub projects from awesome lists"
        context["search_title"] = "Search awesome repositories"
        context["search_description"] = (
            "Search names, descriptions, topics, tags, and stacks, then tune results by "
            "ecosystem, freshness, health, and cross-list signal."
        )
        context["total_repositories_label"] = "Repos indexed"
        context["total_lists_label"] = "Awesome lists tracked"
        return context


class UserStarredRepositorySearchView(LoginRequiredMixin, ListView):
    template_name = "repos/search.html"
    context_object_name = "repositories"
    paginate_by = 30
    login_url = "account_login"

    def get_profile(self):
        if not hasattr(self, "profile"):
            self.profile, _created = Profile.objects.get_or_create(user=self.request.user)
        return self.profile

    def base_starred_repository_queryset(self):
        # Personal starred search intentionally includes every imported star, including
        # repositories hidden from public catalog search as awesome-list candidates.
        profile = self.get_profile()
        return Repository.objects.filter(starred_by_profiles__profile=profile)

    def starred_repository_queryset(self):
        profile = self.get_profile()
        return self.base_starred_repository_queryset().annotate(
            user_starred_at=Max(
                "starred_by_profiles__starred_at",
                filter=Q(starred_by_profiles__profile=profile),
            )
        )

    def get_queryset(self):
        return with_repository_like_state(
            repository_search_queryset(
                self.request.GET,
                queryset=self.starred_repository_queryset(),
                include_snapshot_metrics=False,
                extra_sort_map={"starred": ("user_starred_at", "desc")},
            ),
            self.request.user,
        ).prefetch_related("awesome_items__awesome_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["repositories"] = attach_repository_snapshot_counts(context["repositories"])
        context["object_list"] = context["repositories"]
        context["page_obj"].object_list = context["repositories"]
        profile = self.get_profile()
        starred_repositories = self.base_starred_repository_queryset()
        search_url = reverse("repos:starred")
        awesome_lists = (
            AwesomeList.objects.filter(
                is_active=True,
                items__repository__starred_by_profiles__profile=profile,
            )
            .annotate(
                repo_count=Count(
                    "items__repository",
                    filter=Q(items__repository__starred_by_profiles__profile=profile),
                    distinct=True,
                )
            )
            .order_by("name")
            .distinct()
        )
        context.update(
            repository_filter_context(
                request=self.request,
                base_queryset=starred_repositories,
                search_url=search_url,
                reset_url=search_url,
                awesome_lists=awesome_lists,
                profile=profile,
                filter_id_prefix="repo-filter",
                sort_labels={"starred": "Recently starred"},
            )
        )
        context["total_repositories"] = profile.starred_repository_links.count()
        context["total_lists"] = awesome_lists.count()
        context["search_eyebrow"] = "Your GitHub stars"
        context["search_title"] = "Search your starred repositories."
        context["search_description"] = (
            "Filter the public repositories imported from your GitHub stars by the same "
            "metadata used across Awesome."
        )
        context["total_repositories_label"] = "Starred repos"
        context["total_lists_label"] = "Matching awesome lists"
        context["is_personal_starred_search"] = True
        return context


class LikedRepositoryListView(LoginRequiredMixin, ListView):
    template_name = "repos/liked.html"
    context_object_name = "repositories"
    paginate_by = 30
    login_url = "account_login"

    def liked_repository_queryset(self):
        # Personal liked search intentionally includes explicit saves even when a
        # repository is hidden from public catalog search.
        return Repository.objects.filter(likes__user=self.request.user).distinct()

    def get_queryset(self):
        sort = (self.request.GET.get("sort") or "").strip()
        liked_queryset = repository_search_queryset(
            self.request.GET,
            queryset=self.liked_repository_queryset(),
        )
        queryset = with_repository_like_state(
            liked_queryset,
            self.request.user,
        ).prefetch_related("awesome_items__awesome_list")
        if sort in {"", "liked"}:
            return queryset.order_by("-likes__created_at", "full_name")
        return queryset

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        context["params"] = params
        context["querystring"] = params.urlencode()
        context["liked_repository_count"] = self.liked_repository_queryset().count()
        return context


class AwesomeListListView(ListView):
    model = AwesomeList
    template_name = "repos/lists.html"
    context_object_name = "awesome_lists"
    paginate_by = 30

    def get_queryset(self):
        return awesome_list_search_queryset(self.request.GET)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        totals = awesome_list_directory_totals()
        sort_labels = {
            "stars": "Most starred",
            "repos": "README repo count",
            "indexed": "Indexed repos",
            "commits": "Commit count",
            "recent": "Recently pushed",
            "oldest": "Oldest first commit",
            "scanned": "Recently scanned",
            "name": "Name",
        }
        requested_sort = params.get("sort")
        selected_sort = requested_sort if requested_sort in sort_labels else "stars"
        selected_sort_label = sort_labels.get(selected_sort, sort_labels["stars"])
        active_filters = []
        search_query = (params.get("q") or "").strip()
        if search_query:
            active_filters.append({"label": "Search", "value": search_query})
        if requested_sort and selected_sort != "stars":
            active_filters.append({"label": "Sort", "value": selected_sort_label})
        min_age_years = params.get("min_age_years")
        if min_age_years and minimum_age_cutoff(params):
            active_filters.append({"label": "History", "value": f"{min_age_years}+ years old"})
        context["params"] = params
        context["querystring"] = params.urlencode()
        context["active_list_filters"] = active_filters
        context["active_list_filter_count"] = len(active_filters)
        context["selected_list_sort_label"] = selected_sort_label
        context["total_lists"] = totals["total_lists"]
        context["total_indexed_links"] = totals["total_indexed_links"]
        context["total_readme_repositories"] = totals["total_readme_repositories"]
        context["total_list_stars"] = totals["total_list_stars"]
        context["latest_scan"] = totals["latest_scan"]
        return context


class AwesomeListRequestView(FormView):
    template_name = "repos/request_list.html"
    form_class = AwesomeListRequestForm
    success_url = reverse_lazy("repos:request_list")
    rate_limit_count = AWESOME_LIST_REQUEST_RATE_LIMIT
    rate_limit_window_seconds = AWESOME_LIST_REQUEST_RATE_LIMIT_WINDOW_SECONDS

    def post(self, request, *args, **kwargs):
        cache_key = awesome_list_request_rate_limit_key(request)
        cache.add(cache_key, 0, timeout=self.rate_limit_window_seconds)
        if cache.incr(cache_key) > self.rate_limit_count:
            return HttpResponse("Too many awesome-list requests. Try again later.", status=429)
        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        try:
            with transaction.atomic():
                form.save()
        except IntegrityError:
            form.add_error("source_url", "That awesome-list request has already been submitted.")
            return self.form_invalid(form)
        messages.success(
            self.request,
            "Thanks, your awesome-list request has been submitted.",
        )
        return super().form_valid(form)

    def get_success_url(self):
        next_url = self.request.POST.get("next", "")
        if next_url and url_has_allowed_host_and_scheme(
            next_url,
            allowed_hosts={self.request.get_host()},
            require_https=self.request.is_secure(),
        ):
            return next_url

        return super().get_success_url()


class RepositoryDetailView(DetailView):
    model = Repository
    template_name = "repos/detail.html"
    context_object_name = "repository"

    def get_object(self, queryset=None):
        full_name = f"{self.kwargs['owner']}/{self.kwargs['name']}"
        queryset = Repository.objects.prefetch_related("awesome_items__awesome_list")
        queryset = with_repository_like_state(queryset, self.request.user)
        return get_object_or_404(queryset, full_name=full_name)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        performance = repository_performance_summary(self.object)
        context["performance"] = performance
        if performance["has_history"]:
            context["repository_history_chart_data"] = repository_history_chart_data(self.object)
        context["similar_repositories"] = similar_repositories_for_repository(self.object)
        context["ai_development_signal_summary"] = _ai_development_signal_summary(
            self.object.ai_development_signals
        )
        context["badge_embed"] = repository_badge_embed_context(self.request, self.object)
        context["newsletter_issues"] = self.object.newsletter_issues.filter(
            published_at__isnull=False,
        )[:5]
        if self.request.user.is_superuser:
            subscription = (
                NewsletterSubscription.objects.filter(
                    user=self.request.user,
                    repository=self.object,
                    is_active=True,
                )
                .order_by("-created_at")
                .first()
            )
            context["newsletter_subscription"] = subscription
            context["newsletter_form"] = NewsletterSubscriptionForm(
                initial={
                    "email": subscription.email if subscription else self.request.user.email,
                    "cadence": subscription.cadence if subscription else NewsletterCadence.WEEKLY,
                }
            )
        return context


class RepositoryNewsletterIssueListView(ListView):
    template_name = "repos/newsletter_issue_list.html"
    context_object_name = "issues"
    paginate_by = 20

    def dispatch(self, request, *args, **kwargs):
        self.repository = get_object_or_404(
            Repository,
            full_name=f"{kwargs['owner']}/{kwargs['name']}",
        )
        return super().dispatch(request, *args, **kwargs)

    def get_queryset(self):
        return self.repository.newsletter_issues.filter(published_at__isnull=False)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["repository"] = self.repository
        context["newsletter_list_meta_description"] = (
            f"{self.repository.full_name} repository newsletter archive with generated "
            "weekly and monthly change updates plus RSS feeds from tracked commits."
        )
        return context


class RepositoryNewsletterIssueDetailView(DetailView):
    model = RepositoryNewsletterIssue
    template_name = "repos/newsletter_issue_detail.html"
    context_object_name = "issue"

    def get_queryset(self):
        return RepositoryNewsletterIssue.objects.filter(
            repository__full_name=f"{self.kwargs['owner']}/{self.kwargs['name']}",
            cadence=self.kwargs["cadence"],
            published_at__isnull=False,
        ).select_related("repository")

    def get_object(self, queryset=None):
        queryset = self.get_queryset() if queryset is None else queryset
        return get_object_or_404(queryset, slug=self.kwargs["slug"])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        issue = self.object
        context["newsletter_issue_meta_description"] = (
            f"{issue.repository.full_name} {issue.get_cadence_display().lower()} update: "
            f"{issue.title} covering {issue.commit_count} commit"
            f"{'' if issue.commit_count == 1 else 's'} from "
            f"{issue.period_start:%Y-%m-%d} to {issue.period_end:%Y-%m-%d}."
        )
        return context


class RepositoryNewsletterFeed(Feed):
    def get_object(self, request, owner: str, name: str, cadence: str):
        if cadence not in NewsletterCadence.values:
            raise Http404("Unknown newsletter cadence.")
        repository = get_object_or_404(Repository, full_name=f"{owner}/{name}")
        return repository, cadence

    def title(self, obj):
        repository, cadence = obj
        return f"{repository.full_name} {cadence} newsletter"

    def link(self, obj):
        repository, _cadence = obj
        return reverse(
            "repos:newsletter_issue_list",
            kwargs={"owner": repository.owner, "name": repository.name},
        )

    def description(self, obj):
        repository, cadence = obj
        return f"Generated {cadence} change updates for {repository.full_name}."

    def items(self, obj):
        repository, cadence = obj
        return repository.newsletter_issues.filter(
            cadence=cadence,
            published_at__isnull=False,
        )[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.content_html

    def item_link(self, item):
        return item.get_absolute_url()

    def item_pubdate(self, item):
        return item.published_at


def newsletter_unsubscribe(request, token: str):
    subscription = get_object_or_404(
        NewsletterSubscription.objects.select_related("repository"),
        unsubscribe_token=token,
    )
    if request.method == "POST":
        unsubscribe_newsletter(subscription)
        messages.success(request, "You have been unsubscribed from this repository newsletter.")
        return redirect(subscription.repository.get_absolute_url())
    return render(
        request,
        "repos/newsletter_unsubscribe.html",
        {"subscription": subscription},
    )


class AwesomeListDetailView(DetailView):
    model = AwesomeList
    template_name = "repos/list_detail.html"
    context_object_name = "awesome_list"

    def get_queryset(self):
        return AwesomeList.objects.filter(is_active=True).annotate(
            indexed_repo_count=visible_awesome_list_item_count()
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        repos = with_repository_like_state(
            awesome_list_repository_queryset(self.object, self.request.GET),
            self.request.user,
        )
        all_list_repos = visible_repository_queryset().filter(
            awesome_items__awesome_list=self.object
        )
        self.object.indexed_repo_count = all_list_repos.count()
        context.update(
            repository_filter_context(
                request=self.request,
                base_queryset=all_list_repos,
                search_url=self.object.get_absolute_url(),
                reset_url=self.object.get_absolute_url(),
                awesome_list=self.object,
                show_list_filter=False,
                search_field_class="md:col-span-2 lg:col-span-1",
                filter_id_prefix="list-repo",
            )
        )
        context["repo_stats"] = all_list_repos.aggregate(
            total_stars=Sum("stars"),
            total_forks=Sum("forks"),
            active_count=Count("id", filter=Q(is_archived=False)),
            archived_count=Count("id", filter=Q(is_archived=True)),
            latest_repo_push=Max("github_pushed_at"),
        )
        context["language_counts"] = (
            all_list_repos.exclude(language="")
            .values("language")
            .annotate(count=Count("id"))
            .order_by("-count", "language")[:12]
        )
        context["awesome_list_history_chart_data"] = awesome_list_history_chart_data(self.object)
        context["page_obj"] = Paginator(repos, 50).get_page(self.request.GET.get("page"))
        context["hide_side_ad_rails"] = True
        return context
