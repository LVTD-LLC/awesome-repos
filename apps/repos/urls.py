from django.urls import path

from apps.repos import views

app_name = "repos"
urlpatterns = [
    path("repos/", views.RepositorySearchView.as_view(), name="search"),
    path("repos/<str:owner>/<str:name>/", views.RepositoryDetailView.as_view(), name="repo_detail"),
    path("lists/<slug:slug>/", views.AwesomeListDetailView.as_view(), name="list_detail"),
]
