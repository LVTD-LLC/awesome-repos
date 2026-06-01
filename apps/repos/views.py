from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Q, Sum
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from django.views.generic import DetailView, FormView, ListView
from django_q.tasks import async_task

from apps.core.models import Profile
from apps.repos.forms import AwesomeListRequestForm
from apps.repos.models import AwesomeList, Repository, RepositoryLike
from apps.repos.search_services import (
    awesome_list_search_queryset,
    visible_awesome_list_item_count,
)
from apps.repos.services import (
    awesome_list_directory_totals,
    awesome_list_repository_queryset,
    repository_history_chart_data,
    repository_json_value_counts,
    repository_performance_summary,
    repository_search_queryset,
    similar_repositories_for_repository,
    visible_repository_queryset,
    with_repository_like_state,
)

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
            group=REPOSITORY_REFRESH_TASK_GROUP,
        )
    )
    messages.success(request, f"Queued a rescan for {repository.full_name}.")
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


def awesome_list_request_client_ip(request) -> str:
    # X-Forwarded-For is only safe behind a trusted proxy that strips spoofed headers.
    return request.META.get("REMOTE_ADDR", "unknown")


def awesome_list_request_rate_limit_key(request) -> str:
    return f"awesome-list-request:{awesome_list_request_client_ip(request)}"


class RepositorySearchView(ListView):
    template_name = "repos/search.html"
    context_object_name = "repositories"
    paginate_by = 30

    def get_queryset(self):
        return with_repository_like_state(
            repository_search_queryset(self.request.GET),
            self.request.user,
        ).prefetch_related(
            "awesome_items__awesome_list",
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        visible_repositories = visible_repository_queryset()
        search_url = reverse("repos:search")
        context["awesome_lists"] = (
            AwesomeList.objects.filter(is_active=True)
            .annotate(repo_count=visible_awesome_list_item_count())
            .order_by("name")
        )
        context["languages"] = (
            visible_repositories.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        )
        context["topic_options"] = repository_json_value_counts("topics")
        context["generated_tag_options"] = repository_json_value_counts("generated_tags")
        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring"] = params.urlencode()
        context["total_repositories"] = visible_repositories.count()
        context["total_lists"] = AwesomeList.objects.filter(is_active=True).count()
        context["search_action_url"] = search_url
        context["search_reset_url"] = search_url
        context["search_eyebrow"] = "Awesome-list intelligence for GitHub"
        context["search_title"] = "Search every repository hiding inside awesome lists."
        context["search_description"] = (
            "Discover projects curated by awesome-list maintainers, then narrow them by "
            "stars, age, freshness, archive status, language, topics, generated tags, and "
            "source list."
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

    def starred_repository_queryset(self):
        # Personal starred search intentionally includes every imported star, including
        # repositories hidden from public catalog search as awesome-list candidates.
        return Repository.objects.filter(starred_by_profiles__profile=self.get_profile()).distinct()

    def get_queryset(self):
        return with_repository_like_state(
            repository_search_queryset(
                self.request.GET,
                queryset=self.starred_repository_queryset(),
            ),
            self.request.user,
        ).prefetch_related("awesome_items__awesome_list")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        profile = self.get_profile()
        starred_repositories = self.starred_repository_queryset()
        search_url = reverse("repos:starred")
        context["awesome_lists"] = (
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
        context["languages"] = (
            starred_repositories.exclude(language="")
            .values_list("language", flat=True)
            .distinct()
            .order_by("language")
        )
        context["topic_options"] = repository_json_value_counts("topics", profile=profile)
        context["generated_tag_options"] = repository_json_value_counts(
            "generated_tags",
            profile=profile,
        )
        params = self.request.GET.copy()
        params.pop("page", None)
        context["querystring"] = params.urlencode()
        context["total_repositories"] = starred_repositories.count()
        context["total_lists"] = context["awesome_lists"].count()
        context["search_action_url"] = search_url
        context["search_reset_url"] = search_url
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

    def get_queryset(self):
        sort = (self.request.GET.get("sort") or "").strip()
        liked_queryset = repository_search_queryset(self.request.GET).filter(
            likes__user=self.request.user
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
        context["liked_repository_count"] = RepositoryLike.objects.filter(
            user=self.request.user
        ).count()
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
        return context


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
        params = self.request.GET.copy()
        params.pop("page", None)
        filter_names = (
            "q",
            "language",
            "topic",
            "generated_tag",
            "min_stars",
            "updated_days",
            "min_age_years",
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
        context["hide_side_ad_rails"] = True
        return context
