from django.urls import path

from apps.core import views

urlpatterns = [
    # App pages
    path("home", views.HomeView.as_view(), name="home"),
    path("settings", views.UserSettingsView.as_view(), name="settings"),
    path("admin-panel", views.AdminPanelView.as_view(), name="admin_panel"),
    # Utils
    path(
        "settings/github/starred/import/",
        views.import_starred_repositories,
        name="import_starred_repositories",
    ),
    path(
        "settings/github/starred/disable/",
        views.disable_starred_repository_import,
        name="disable_starred_repository_import",
    ),
    path("resend-confirmation/", views.resend_confirmation_email, name="resend_confirmation"),
    path("delete-account/", views.delete_account, name="delete_account"),
    path("sponsor/checkout/", views.sponsor_checkout, name="sponsor_checkout"),
    path("sponsor/success/", views.sponsor_success, name="sponsor_success"),
    path("sponsor/stripe/webhook/", views.stripe_webhook, name="stripe_webhook"),
]
