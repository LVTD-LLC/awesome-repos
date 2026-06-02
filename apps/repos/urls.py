from django.urls import path
from django.views.generic import RedirectView

from apps.repos import views

app_name = "repos"
urlpatterns = [
    path("", views.RepositorySearchView.as_view(), name="search"),
    path("starred/", views.UserStarredRepositorySearchView.as_view(), name="starred"),
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
    path(
        "repos/<str:owner>/<str:name>/newsletter/",
        views.upsert_repository_newsletter_subscription,
        name="repo_newsletter_subscribe",
    ),
    path(
        "repos/<str:owner>/<str:name>/newsletter/disable/",
        views.disable_repository_newsletter,
        name="repo_newsletter_disable",
    ),
    path(
        "repos/<str:owner>/<str:name>/newsletters/",
        views.RepositoryNewsletterIssueListView.as_view(),
        name="newsletter_issue_list",
    ),
    path(
        "repos/<str:owner>/<str:name>/newsletters/<str:cadence>/feed.xml",
        views.RepositoryNewsletterFeed(),
        name="newsletter_feed",
    ),
    path(
        "repos/<str:owner>/<str:name>/newsletters/<str:cadence>/<slug:slug>/",
        views.RepositoryNewsletterIssueDetailView.as_view(),
        name="newsletter_issue_detail",
    ),
    path(
        "repos/<str:owner>/<str:name>/like/",
        views.toggle_repository_like,
        name="repo_like_toggle",
    ),
    path("repos/<str:owner>/<str:name>/", views.RepositoryDetailView.as_view(), name="repo_detail"),
    path("liked/", views.LikedRepositoryListView.as_view(), name="liked"),
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
    path("lists/request/", views.AwesomeListRequestView.as_view(), name="request_list"),
    path("lists/<slug:slug>/", views.AwesomeListDetailView.as_view(), name="list_detail"),
    path(
        "newsletters/unsubscribe/<str:token>/",
        views.newsletter_unsubscribe,
        name="newsletter_unsubscribe",
    ),
]
