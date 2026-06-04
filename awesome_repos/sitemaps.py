from types import SimpleNamespace
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib import sitemaps
from django.urls import reverse

from apps.repos.models import AwesomeList, Repository


class ConfiguredDomainSitemap(sitemaps.Sitemap):
    protocol = "https"

    def get_urls(self, page=1, site=None, protocol=None):
        parsed_site_url = urlsplit(settings.SITE_URL)
        configured_site = SimpleNamespace(domain=parsed_site_url.netloc)
        configured_protocol = parsed_site_url.scheme or protocol or self.protocol
        return super().get_urls(
            page=page,
            site=configured_site,
            protocol=configured_protocol,
        )


class StaticViewSitemap(ConfiguredDomainSitemap):
    """Generate a sitemap for public static and index pages."""

    priority = 0.9
    changefreq = "daily"

    def items(self):
        return [
            "repos:search",
            "repos:list",
            "repos:request_list",
            "uses",
            "privacy_policy",
            "terms_of_service",
        ]

    def location(self, item):
        return reverse(item)


class RepositorySitemap(ConfiguredDomainSitemap):
    changefreq = "weekly"
    priority = 0.7

    def items(self):
        active_list_source_repositories = (
            AwesomeList.objects.filter(is_active=True)
            .exclude(repo_full_name="")
            .values("repo_full_name")
        )
        return (
            Repository.objects.exclude(is_awesome_list_candidate=True)
            .exclude(full_name__in=active_list_source_repositories)
            .order_by("id")
        )

    def lastmod(self, item):
        return item.github_pushed_at or item.last_synced_at or item.updated_at


class AwesomeListSitemap(ConfiguredDomainSitemap):
    changefreq = "weekly"
    priority = 0.8

    def items(self):
        return AwesomeList.objects.filter(is_active=True).order_by("id")

    def lastmod(self, item):
        return item.last_scanned_at or item.github_pushed_at or item.updated_at


sitemaps = {
    "static": StaticViewSitemap,
    "repositories": RepositorySitemap,
    "awesome_lists": AwesomeListSitemap,
}
