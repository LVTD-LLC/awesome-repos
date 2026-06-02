from django.core.cache import cache

PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_KEY = "repos:public-filter-options:v1"
PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_TIMEOUT_SECONDS = 10 * 60


def clear_public_repository_filter_options_cache() -> None:
    cache.delete(PUBLIC_REPOSITORY_FILTER_OPTIONS_CACHE_KEY)
