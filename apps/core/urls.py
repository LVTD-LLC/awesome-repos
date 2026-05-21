from django.urls import path

from apps.core import views

urlpatterns = [
    
    # App pages
    path("home", views.HomeView.as_view(), name="home"),
    path("settings", views.UserSettingsView.as_view(), name="settings"),
    path("admin-panel", views.AdminPanelView.as_view(), name="admin_panel"),
    # Utils
    path("settings/api-key/rotate/", views.rotate_api_key, name="rotate_api_key"),
    path("resend-confirmation/", views.resend_confirmation_email, name="resend_confirmation"),
    path("delete-account/", views.delete_account, name="delete_account"),
    
]
