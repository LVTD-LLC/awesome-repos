from django.urls import path
from django.views.generic import RedirectView

from apps.repos import views

app_name = "repos"
urlpatterns = [
    path("", views.RepositorySearchView.as_view(), name="search"),
    path(
        "repos/",
        RedirectView.as_view(pattern_name="repos:search", permanent=True),
        name="legacy_search",
    ),
    path(
        "repos/<str:owner>/<str:name>/rescan/",
        views.queue_repository_rescan,
        name="repo_rescan",
    ),
    path("repos/<str:owner>/<str:name>/", views.RepositoryDetailView.as_view(), name="repo_detail"),
    path("lists/", views.AwesomeListListView.as_view(), name="list"),
    path(
        "lists/<slug:slug>/rescan/",
        views.queue_awesome_list_rescan,
        name="list_rescan",
    ),
    path(
        "lists/<slug:slug>/discover-missing/",
        views.queue_awesome_list_missing_repo_discovery,
        name="list_discover_missing",
    ),
    path("lists/<slug:slug>/", views.AwesomeListDetailView.as_view(), name="list_detail"),
]
