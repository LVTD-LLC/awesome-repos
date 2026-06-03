from datetime import timedelta

from allauth.mfa import app_settings as mfa_app_settings
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.core.cache import cache
from django.utils import timezone

from apps.core.choices import ProfileStates
from apps.core.models import HighlightedRepoPurchase, SponsorAdPurchase
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


def current_state(request):
    if request.user.is_authenticated:
        return {"current_state": request.user.profile.current_state}
    return {"current_state": ProfileStates.STRANGER}


def mfa_recovery_codes_settings(request):
    return {"mfa_recovery_codes_show_once": mfa_app_settings.RECOVERY_CODES_SHOW_ONCE}


def posthog_api_key(request):
    return {"posthog_api_key": settings.POSTHOG_API_KEY}


def chatwoot_settings(request):
    return {
        "chatwoot_base_url": settings.CHATWOOT_BASE_URL.rstrip("/"),
        "chatwoot_website_token": settings.CHATWOOT_WEBSITE_TOKEN,
    }


def user_has_removed_ads(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return False

    try:
        return bool(request.user.profile.remove_ads)
    except Exception:
        return False


def ads_removed(request):
    return {"ads_removed": user_has_removed_ads(request)}


def active_sponsor_ad(request):
    if user_has_removed_ads(request):
        return {"awesome_sponsor_ad": None}

    cache_key = "awesome:active_sponsor_ad"
    cache_miss = object()
    no_active_ad = "__awesome_no_active_sponsor_ad__"
    sponsor_ad = cache.get(cache_key, cache_miss)
    if sponsor_ad == no_active_ad:
        return {"awesome_sponsor_ad": None}
    if sponsor_ad is cache_miss:
        sponsor_ad = (
            SponsorAdPurchase.objects.filter(status=SponsorAdPurchase.Status.ACTIVE)
            .exclude(startup_name="")
            .order_by("-updated_at")
            .first()
        )
        cached_value = sponsor_ad if sponsor_ad is not None else no_active_ad
        cache.set(cache_key, cached_value, 60)
    return {"awesome_sponsor_ad": sponsor_ad}


def active_highlighted_repo(request):
    if user_has_removed_ads(request):
        return {"awesome_highlighted_repo": None}

    cache_key = "awesome:active_highlighted_repo"
    cache_miss = object()
    no_active_highlight = "__awesome_no_active_highlighted_repo__"
    highlighted_repo = cache.get(cache_key, cache_miss)
    if highlighted_repo == no_active_highlight:
        return {"awesome_highlighted_repo": None}
    if highlighted_repo is cache_miss:
        highlighted_repo = (
            HighlightedRepoPurchase.objects.filter(
                status=HighlightedRepoPurchase.Status.ACTIVE,
                details_submitted_at__gt=timezone.now() - timedelta(days=7),
            )
            .exclude(repo_full_name="")
            .exclude(repo_url="")
            .order_by("-details_submitted_at", "-updated_at")
            .first()
        )
        cached_value = highlighted_repo if highlighted_repo is not None else no_active_highlight
        cache.set(cache_key, cached_value, 60)
    return {"awesome_highlighted_repo": highlighted_repo}


def available_social_providers(request):
    """
    Checks which social authentication providers are available.
    Returns a list of provider names from either SOCIALACCOUNT_PROVIDERS settings
    or SocialApp database entries, as django-allauth supports both configuration methods.
    """
    available_providers = set()

    configured_providers = getattr(settings, "SOCIALACCOUNT_PROVIDERS", {})

    available_providers.update(configured_providers.keys())

    try:
        social_apps = SocialApp.objects.all()
        for social_app in social_apps:
            available_providers.add(social_app.provider)
    except Exception as e:
        logger.warning("Error retrieving SocialApp entries", error=str(e))

    available_providers_list = sorted(list(available_providers))

    return {
        "available_social_providers": available_providers_list,
        "has_social_providers": len(available_providers_list) > 0,
    }
