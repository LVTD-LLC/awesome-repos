from django.urls import path

from apps.mcp_server.views import mcp_endpoint

urlpatterns = [
    path("", mcp_endpoint, name="mcp"),
]
