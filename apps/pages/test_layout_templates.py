import re

import pytest
from django.template.loader import render_to_string


@pytest.mark.parametrize("template_name", ["base_landing.html", "base_app.html"])
def test_side_ad_sponsor_checkout_form_includes_csrf_token(template_name):
    content = render_to_string(template_name, {"csrf_token": "csrf-test-token"})

    assert re.search(
        r'<form\b(?=[^>]*\bmethod="post")(?=[^>]*\baction="/sponsor/checkout/")[^>]*>'
        r"[\s\S]*?"
        r'<input\b(?=[^>]*\btype="hidden")'
        r'(?=[^>]*\bname="csrfmiddlewaretoken")(?=[^>]*\bvalue="csrf-test-token")[^>]*>',
        content,
    ), "Sponsor checkout form should render the forwarded CSRF token."
