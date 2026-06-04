import pytest
from django.test import override_settings
from django.urls import reverse

from apps.repos.models import AwesomeList, Repository

pytestmark = pytest.mark.django_db


def response_text(response):
    return response.content.decode()


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
    assert "<loc>https://awesome.example/lists/awesome-django/</loc>" in content
