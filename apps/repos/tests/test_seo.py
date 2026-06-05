from datetime import date

import pytest
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from apps.repos.models import (
    AwesomeList,
    NewsletterCadence,
    Repository,
    RepositoryNewsletterIssue,
)

pytestmark = pytest.mark.django_db


def response_text(response):
    return response.content.decode()


@override_settings(SITE_URL="https://testserver")
def test_public_search_page_has_complete_seo_metadata(client):
    response = client.get(reverse("repos:search"))

    assert response.status_code == 200
    content = response_text(response)
    assert "<title>Search Awesome Repositories · Awesome</title>" in content
    assert (
        '<meta name="description" content="Search names, descriptions, topics, tags, and '
        'stacks, then tune results by ecosystem, freshness, health, and cross-list signal." />'
        in content
    )
    assert '<meta name="robots" content="index, follow" />' in content
    assert '<link rel="canonical" href="https://testserver/" />' in content
    assert '<meta property="og:title" content="Search Awesome Repositories · Awesome" />' in content
    assert '<meta name="twitter:card" content="summary_large_image" />' in content
    assert '<meta name="keywords"' not in content


@override_settings(SITE_URL="https://testserver")
def test_repository_detail_has_page_specific_metadata_and_schema(client):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework for perfectionists with deadlines.",
        language="Python",
    )

    response = client.get(repository.get_absolute_url())

    assert response.status_code == 200
    content = response_text(response)
    assert "<title>django/django · Awesome</title>" in content
    assert (
        '<meta name="description" content="The Web framework for perfectionists with deadlines." />'
        in content
    )
    assert (
        '<link rel="canonical" href="https://testserver/repos/django/django/" />' in content
    )
    assert '<meta property="og:type" content="article" />' in content
    assert '"@type": "SoftwareSourceCode"' in content
    assert '"codeRepository": "https://github.com/django/django"' in content
    assert '"url": "https://testserver/repos/django/django/"' in content


@override_settings(SITE_URL="https://testserver")
def test_awesome_list_detail_has_page_specific_metadata_and_schema(client):
    awesome_list = AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        description="Curated Django packages and resources.",
    )

    response = client.get(awesome_list.get_absolute_url())

    assert response.status_code == 200
    content = response_text(response)
    assert "<title>Awesome Django · Awesome</title>" in content
    assert (
        '<meta name="description" content="Curated Django packages and resources." />'
        in content
    )
    assert '<link rel="canonical" href="https://testserver/lists/awesome-django/" />' in content
    assert '"@type": "CollectionPage"' in content
    assert '"url": "https://testserver/lists/awesome-django/"' in content


@override_settings(SITE_URL="https://testserver")
def test_newsletter_issue_list_has_repository_specific_seo_description(client):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework for perfectionists with deadlines.",
    )

    response = client.get(
        reverse(
            "repos:newsletter_issue_list",
            kwargs={"owner": repository.owner, "name": repository.name},
        )
    )

    assert response.status_code == 200
    content = response_text(response)
    assert "<title>django/django newsletters · Awesome</title>" in content
    assert (
        '<meta name="description" content="django/django repository newsletter archive '
        'with generated weekly and monthly change updates plus RSS feeds from tracked commits." />'
        in content
    )
    assert (
        '<link rel="canonical" href="https://testserver/repos/django/django/newsletters/" />'
        in content
    )


@override_settings(SITE_URL="https://testserver")
def test_newsletter_issue_detail_has_unique_seo_description(client):
    repository = Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework for perfectionists with deadlines.",
    )
    issue = RepositoryNewsletterIssue.objects.create(
        repository=repository,
        cadence=NewsletterCadence.WEEKLY,
        period_start=date(2026, 5, 25),
        period_end=date(2026, 5, 31),
        slug="2026-05-25",
        title="Django weekly update",
        content_markdown="## Changes\n- Added tracking.",
        content_html="<h2>Changes</h2><ul><li>Added tracking.</li></ul>",
        commit_count=3,
        published_at=timezone.now(),
    )

    response = client.get(issue.get_absolute_url())

    assert response.status_code == 200
    content = response_text(response)
    assert "<title>Django weekly update · Awesome</title>" in content
    assert (
        '<meta name="description" content="django/django weekly update: Django weekly update '
        'covering 3 commits from 2026-05-25 to 2026-05-31." />'
        in content
    )
    assert (
        '<link rel="canonical" '
        'href="https://testserver/repos/django/django/newsletters/weekly/2026-05-25/" />'
        in content
    )


@override_settings(SITE_URL="https://awesome.example")
def test_robots_txt_allows_crawling_and_advertises_sitemap(client):
    response = client.get("/robots.txt")

    assert response.status_code == 200
    assert response["Content-Type"].startswith("text/plain")
    assert response_text(response) == (
        "User-agent: *\n"
        "Allow: /\n"
        "Sitemap: https://awesome.example/sitemap.xml\n"
    )


@override_settings(SITE_URL="https://awesome.example")
def test_sitemap_includes_public_static_repository_and_list_pages(client):
    Repository.objects.create(
        full_name="django/django",
        owner="django",
        name="django",
        url="https://github.com/django/django",
        description="The Web framework for perfectionists with deadlines.",
    )
    Repository.objects.create(
        full_name="django/channels",
        owner="django",
        name="channels",
        url="https://github.com/django/channels",
        description="Archived Django Channels repository.",
        is_archived=True,
    )
    Repository.objects.create(
        full_name="django/disabled",
        owner="django",
        name="disabled",
        url="https://github.com/django/disabled",
        description="Disabled Django repository.",
        is_disabled=True,
    )
    AwesomeList.objects.create(
        name="Awesome Django",
        slug="awesome-django",
        source_url="https://github.com/wsvincent/awesome-django",
        description="Curated Django packages and resources.",
    )

    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    content = response_text(response)
    assert "<loc>https://awesome.example/</loc>" in content
    assert "<loc>https://awesome.example/lists/</loc>" in content
    assert "<loc>https://awesome.example/repos/django/django/</loc>" in content
    assert "<loc>https://awesome.example/repos/django/channels/</loc>" not in content
    assert "<loc>https://awesome.example/repos/django/disabled/</loc>" not in content
    assert "<loc>https://awesome.example/lists/awesome-django/</loc>" in content
