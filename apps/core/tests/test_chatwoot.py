from django.template.loader import render_to_string
from django.test import override_settings

from apps.core.context_processors import chatwoot_settings


@override_settings(
    CHATWOOT_BASE_URL="https://chat.example.com/",
    CHATWOOT_WEBSITE_TOKEN="websitetoken",
)
def test_chatwoot_context_processor_exposes_widget_settings():
    assert chatwoot_settings(None) == {
        "chatwoot_base_url": "https://chat.example.com",
        "chatwoot_website_token": "websitetoken",
    }


def test_chatwoot_widget_renders_when_configured():
    content = render_to_string(
        "components/chatwoot_widget.html",
        {
            "chatwoot_base_url": "https://chat.example.com",
            "chatwoot_website_token": "websitetoken",
        },
    )

    assert 'var BASE_URL = "https://chat.example.com";' in content
    assert 'websiteToken: "websitetoken"' in content
    assert "window.chatwootSDK.run" in content


def test_chatwoot_widget_does_not_render_without_token():
    content = render_to_string(
        "components/chatwoot_widget.html",
        {
            "chatwoot_base_url": "https://chat.example.com",
            "chatwoot_website_token": "",
        },
    )

    assert "window.chatwootSDK.run" not in content
