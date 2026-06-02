import re

import pytest
from django.template.loader import render_to_string


@pytest.mark.parametrize("template_name", ["base_landing.html", "base_app.html"])
def test_side_ad_sponsor_checkout_form_includes_csrf_token(template_name):
    content = render_to_string(template_name, {"csrf_token": "csrf-test-token"})

    assert re.search(
        r'<form method="post" action="/sponsor/checkout/"[^>]*>\s*'
        r'<input type="hidden" name="csrfmiddlewaretoken" value="csrf-test-token">',
        content,
    ), "Sponsor checkout form should render the forwarded CSRF token."
