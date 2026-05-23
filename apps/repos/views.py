from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, F, Max, OuterRef, PositiveIntegerField, Q, Subquery, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView
from django_q.tasks import async_task

from apps.repos.forms import AwesomeListRequestForm
from apps.repos.models import AwesomeList, AwesomeListItem, Repository
from apps.repos.services import (
    repository_performance_summary,
    repository_search_queryset,
    similar_repositories_for_repository,
)
from apps.repos.tags import normalize_repository_tag

REPOSITORY_JSON_FILTER_FIELDS = {"topics", "generated_tags"}
MAX_UPDATED_DAYS_FILTER = 36500
AWESOME_LIST_SCAN_TASK_GROUP = "Scan awesome list"
MISSING_REPOSITORY_DISCOVERY_TASK_GROUP = "Manual awesome-list missing repo discovery"
REPOSITORY_REFRESH_TASK_GROUP = "Refresh repositories"
AWESOME_LIST_REQUEST_RATE_LIMIT = 5
AWESOME_LIST_REQUEST_RATE_LIMIT_WINDOW_SECONDS = 60 * 60


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
            include_readme=True,
            group=REPOSITORY_REFRESH_TASK_GROUP,
        )
    )
    messages.success(request, f"Queued a rescan for {repository.full_name}.")
    return redirect(repository.get_absolute_url())


def repository_json_value_counts(
    field_name: str,
    *,
    awesome_list: AwesomeList | None = None,
    limit: int = 200,
) -> list[dict[str, int | str]]:
    if field_name not in REPOSITORY_JSON_FILTER_FIELDS:
        raise ValueError(f"Unsupported repository JSON filter field: {field_name}")

    table_name = connection.ops.quote_name(Repository._meta.db_table)
    repository_pk = connection.ops.quote_name(Repository._meta.pk.column)
    column_name = connection.ops.quote_name(field_name)
    join_clause = ""
    params = []
    if awesome_list is not None:
        item_table = connection.ops.quote_name(AwesomeListItem._meta.db_table)
        item_repository_id = connection.ops.quote_name(
            AwesomeListItem._meta.get_field("repository").column
        )
        item_list_id = connection.ops.quote_name(
            AwesomeListItem._meta.get_field("awesome_list").column
        )
        join_clause = f"""
        INNER JOIN {item_table} AS list_item
            ON list_item.{item_repository_id} = repository.{repository_pk}
            AND list_item.{item_list_id} = %s
        """
        params.append(awesome_list.pk)

    query = f"""
        SELECT item.value AS name, COUNT(*) AS count
        FROM {table_name} AS repository
        {join_clause}
        CROSS JOIN LATERAL jsonb_array_elements_text(repository.{column_name}) AS item(value)
        WHERE jsonb_typeof(repository.{column_name}) = 'array'
            AND item.value <> ''
        GROUP BY item.value
        ORDER BY count DESC, item.value ASC
        LIMIT %s
    """
    params.append(limit)
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        return [{"name": name, "count": count} for name, count in cursor.fetchall()]


def awesome_list_directory_totals() -> dict:
    list_table = connection.ops.quote_name(AwesomeList._meta.db_table)
    item_table = connection.ops.quote_name(AwesomeListItem._meta.db_table)
    list_pk = connection.ops.quote_name(AwesomeList._meta.pk.column)
    list_active = connection.ops.quote_name("is_active")
    list_last_scanned_at = connection.ops.quote_name("last_scanned_at")
    list_readme_repository_count = connection.ops.quote_name("readme_repository_count")
    list_stars = connection.ops.quote_name("stars")
    item_list_id = connection.ops.quote_name(AwesomeListItem._meta.get_field("awesome_list").column)
    query = f"""
        SELECT
            COUNT(*) AS total_lists,
            COALESCE(SUM(awesome_list.{list_readme_repository_count}), 0)
                AS total_readme_repositories,
            COALESCE(SUM(awesome_list.{list_stars}), 0) AS total_list_stars,
            MAX(awesome_list.{list_last_scanned_at}) AS latest_scan,
            (
                SELECT COUNT(*)
                FROM {item_table} AS item
                INNER JOIN {list_table} AS item_list
                    ON item.{item_list_id} = item_list.{list_pk}
                WHERE item_list.{list_active}
            ) AS total_indexed_links
        FROM {list_table} AS awesome_list
        WHERE awesome_list.{list_active}
    """
    with connection.cursor() as cursor:
        cursor.execute(query)
        (
            total_lists,
            total_readme_repositories,
            total_list_stars,
            latest_scan,
            total_indexed_links,
        ) = cursor.fetchone()

    return {
        "total_lists": total_lists,
        "total_readme_repositories": total_readme_repositories,
        "total_list_stars": total_list_stars,
        "latest_scan": latest_scan,
        "total_indexed_links": total_indexed_links,
    }


def _positive_int_param(params, name: str) -> int | None:
    value = (params.get(name) or "").strip()
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed < 0:
        return None
    return parsed


def _apply_list_repository_keyword_filter(qs, params):
    q = (params.get("q") or "").strip()
    if q:
        return qs.filter(
            Q(full_name__icontains=q)
            | Q(description__icontains=q)
            | Q(language__icontains=q)
            | Q(license_name__icontains=q)
            | Q(topics__icontains=q)
            | Q(generated_tags__icontains=q)
        )
    return qs


def _apply_list_repository_taxonomy_filters(qs, params):
    language = (params.get("language") or "").strip()
    if language:
        qs = qs.filter(language__iexact=language)

    topic = normalize_repository_tag(params.get("topic") or "")
    if topic:
        qs = qs.filter(topics__contains=[topic])

    generated_tag = normalize_repository_tag(params.get("generated_tag") or "")
    if generated_tag:
        qs = qs.filter(generated_tags__contains=[generated_tag])
    return qs


def _apply_list_repository_state_filters(qs, params):
    min_stars = _positive_int_param(params, "min_stars")
    if min_stars is not None:
        qs = qs.filter(stars__gte=min_stars)

    updated_days = _positive_int_param(params, "updated_days")
    if updated_days and updated_days <= MAX_UPDATED_DAYS_FILTER:
        cutoff = timezone.now() - timedelta(days=updated_days)
        qs = qs.filter(github_pushed_at__gte=cutoff)

    archived = params.get("archived")
    if archived == "yes":
        qs = qs.filter(is_archived=True)
    elif archived == "no":
        qs = qs.filter(is_archived=False)

    ai_development = params.get("ai_development")
    if ai_development == "yes":
        qs = qs.filter(uses_ai_for_development=True)
    elif ai_development == "no":
        qs = qs.filter(uses_ai_for_development=False)
    return qs


def _order_list_repositories(qs, params):
    sort = params.get("sort") or "stars"
    sort_map = {
        "stars": "-stars",
        "forks": "-forks",
        "recent": F("github_pushed_at").desc(nulls_last=True),
        "created": F("github_created_at").desc(nulls_last=True),
        "commits": F("commit_count").desc(nulls_last=True),
        "awesome": F("awesome_count").desc(nulls_last=True),
        "least_awesome": F("awesome_count").asc(nulls_last=True),
        "name": "full_name",
    }
    return qs.order_by(sort_map.get(sort, "-stars"), "full_name")


def awesome_list_repository_queryset(awesome_list: AwesomeList, params):
    mention_count = (
        AwesomeListItem.objects.filter(repository=OuterRef("pk"))
        .values("repository")
        .annotate(total=Count("id"))
        .values("total")
    )
    qs = Repository.objects.filter(awesome_items__awesome_list=awesome_list).annotate(
        awesome_count=Subquery(mention_count, output_field=PositiveIntegerField())
    )
    qs = _apply_list_repository_keyword_filter(qs, params)
    qs = _apply_list_repository_taxonomy_filters(qs, params)
    qs = _apply_list_repository_state_filters(qs, params)
    return _order_list_repositories(qs, params)


def awesome_list_request_client_ip(request) -> str:
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "unknown")


def awesome_list_request_rate_limit_key(request) -> str:
    return f"awesome-list-request:{awesome_list_request_client_ip(request)}"


class RepositorySearchView(ListView):
    template_name = "repos/search.html"
    context_object_name = "repositories"
    paginate_by = 30

    def get_queryset(self):
        return repository_search_queryset(self.request.GET).prefetch_related(
            "awesome_items__awesome_list"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["awesome_lists"] = (
            AwesomeList.objects.filter(is_active=True)
            .annotate(repo_count=Count("items"))
            .order_by("name")
        )
        context["languages"] = (
            Repository.objects.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        )
        context["topic_options"] = repository_json_value_counts("topics")
        context["generated_tag_options"] = repository_json_value_counts("generated_tags")
        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring"] = params.urlencode()
        context["total_repositories"] = Repository.objects.count()
        context["total_lists"] = AwesomeList.objects.filter(is_active=True).count()
        return context


class AwesomeListListView(ListView):
    model = AwesomeList
    template_name = "repos/lists.html"
    context_object_name = "awesome_lists"
    paginate_by = 30

    def get_queryset(self):
        qs = AwesomeList.objects.filter(is_active=True).annotate(
            indexed_repo_count=Count("items", distinct=True)
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                Q(name__icontains=q)
                | Q(description__icontains=q)
                | Q(repo_full_name__icontains=q)
                | Q(topics__icontains=q)
            )

        sort = self.request.GET.get("sort") or "stars"
        sort_map = {
            "stars": "-stars",
            "repos": "-readme_repository_count",
            "indexed": "-indexed_repo_count",
            "commits": F("commits_count").desc(nulls_last=True),
            "recent": F("github_pushed_at").desc(nulls_last=True),
            "scanned": F("last_scanned_at").desc(nulls_last=True),
            "name": "name",
        }
        return qs.order_by(sort_map.get(sort, "-stars"), "name")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        params = self.request.GET.copy()
        params.pop("page", None)
        totals = awesome_list_directory_totals()
        context["params"] = params
        context["querystring"] = params.urlencode()
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


class RepositoryDetailView(DetailView):
    model = Repository
    template_name = "repos/detail.html"
    context_object_name = "repository"

    def get_object(self, queryset=None):
        full_name = f"{self.kwargs['owner']}/{self.kwargs['name']}"
        queryset = Repository.objects.prefetch_related("awesome_items__awesome_list")
        return get_object_or_404(queryset, full_name=full_name)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["performance"] = repository_performance_summary(self.object)
        context["similar_repositories"] = similar_repositories_for_repository(self.object)
        return context


class AwesomeListDetailView(DetailView):
    model = AwesomeList
    template_name = "repos/list_detail.html"
    context_object_name = "awesome_list"

    def get_queryset(self):
        return AwesomeList.objects.filter(is_active=True).annotate(
            indexed_repo_count=Count("items", distinct=True)
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        repos = awesome_list_repository_queryset(self.object, self.request.GET)
        all_list_repos = Repository.objects.filter(awesome_items__awesome_list=self.object)
        params = self.request.GET.copy()
        params.pop("page", None)
        filter_names = (
            "q",
            "language",
            "topic",
            "generated_tag",
            "min_stars",
            "updated_days",
            "archived",
            "ai_development",
        )
        context["filters_applied"] = any(params.get(name) for name in filter_names)
        context["querystring"] = params.urlencode()
        context["languages"] = (
            all_list_repos.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        )
        context["topic_options"] = repository_json_value_counts("topics", awesome_list=self.object)
        context["generated_tag_options"] = repository_json_value_counts(
            "generated_tags", awesome_list=self.object
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
        context["page_obj"] = Paginator(repos, 50).get_page(self.request.GET.get("page"))
        return context
