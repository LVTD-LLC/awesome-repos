from django.core.paginator import Paginator
from django.db.models import Count
from django.shortcuts import get_object_or_404
from django.views.generic import DetailView, ListView

from apps.repos.models import AwesomeList, Repository
from apps.repos.services import repository_performance_summary, repository_search_queryset


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
        context["params"] = self.request.GET.copy()
        context["total_repositories"] = Repository.objects.count()
        context["total_lists"] = AwesomeList.objects.count()
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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        repos = Repository.objects.filter(awesome_items__awesome_list=self.object).order_by(
            "-stars"
        )
        context["page_obj"] = Paginator(repos, 50).get_page(self.request.GET.get("page"))
        return context
