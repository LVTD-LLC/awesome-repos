from django.core.paginator import Paginator
from django.db import connection
from django.db.models import Count, F, Max, Q, Sum
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, ListView

from apps.repos.models import AwesomeList, AwesomeListItem, Repository
from apps.repos.services import repository_performance_summary, repository_search_queryset

REPOSITORY_JSON_FILTER_FIELDS = {"topics", "generated_tags"}


def repository_json_value_counts(
    field_name: str,
    *,
    limit: int = 200,
) -> list[dict[str, int | str]]:
    if field_name not in REPOSITORY_JSON_FILTER_FIELDS:
        raise ValueError(f"Unsupported repository JSON filter field: {field_name}")

    table_name = connection.ops.quote_name(Repository._meta.db_table)
    column_name = connection.ops.quote_name(field_name)
    query = f"""
        SELECT item.value AS name, COUNT(*) AS count
        FROM {table_name} AS repository
        CROSS JOIN LATERAL jsonb_array_elements_text(repository.{column_name}) AS item(value)
        WHERE jsonb_typeof(repository.{column_name}) = 'array'
            AND item.value <> ''
        GROUP BY item.value
        ORDER BY count DESC, item.value ASC
        LIMIT %s
    """
    with connection.cursor() as cursor:
        cursor.execute(query, [limit])
        return [{"name": name, "count": count} for name, count in cursor.fetchall()]


def awesome_list_directory_totals() -> dict:
    list_table = connection.ops.quote_name(AwesomeList._meta.db_table)
    item_table = connection.ops.quote_name(AwesomeListItem._meta.db_table)
    query = f"""
        SELECT
            COUNT(*) AS total_lists,
            COALESCE(SUM(readme_repository_count), 0) AS total_readme_repositories,
            COALESCE(SUM(stars), 0) AS total_list_stars,
            MAX(last_scanned_at) AS latest_scan,
            (SELECT COUNT(*) FROM {item_table}) AS total_indexed_links
        FROM {list_table}
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
        context["awesome_lists"] = AwesomeList.objects.annotate(repo_count=Count("items")).order_by(
            "name"
        )
        context["languages"] = (
            Repository.objects.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        )
        context["topic_options"] = repository_json_value_counts("topics")
        context["generated_tag_options"] = repository_json_value_counts("generated_tags")
        context["params"] = self.request.GET.copy()
        context["total_repositories"] = Repository.objects.count()
        context["total_lists"] = AwesomeList.objects.count()
        return context


class AwesomeListListView(ListView):
    model = AwesomeList
    template_name = "repos/lists.html"
    context_object_name = "awesome_lists"
    paginate_by = 30

    def get_queryset(self):
        qs = AwesomeList.objects.annotate(indexed_repo_count=Count("items", distinct=True))
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
        return context


class AwesomeListDetailView(DetailView):
    model = AwesomeList
    template_name = "repos/list_detail.html"
    context_object_name = "awesome_list"

    def get_queryset(self):
        return AwesomeList.objects.annotate(indexed_repo_count=Count("items", distinct=True))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        repos = Repository.objects.filter(awesome_items__awesome_list=self.object).order_by(
            "-stars",
            "full_name",
        )
        context["repo_stats"] = repos.aggregate(
            total_stars=Sum("stars"),
            total_forks=Sum("forks"),
            active_count=Count("id", filter=Q(is_archived=False)),
            archived_count=Count("id", filter=Q(is_archived=True)),
            latest_repo_push=Max("github_pushed_at"),
        )
        context["language_counts"] = (
            repos.exclude(language="")
            .values("language")
            .annotate(count=Count("id"))
            .order_by("-count", "language")[:12]
        )
        context["page_obj"] = Paginator(repos, 50).get_page(self.request.GET.get("page"))
        return context
