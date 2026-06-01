from allauth.mfa import app_settings as mfa_app_settings
from allauth.socialaccount.models import SocialApp
from django.conf import settings
from django.core.cache import cache

from apps.core.choices import ProfileStates
from apps.core.models import SponsorAdPurchase
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


def active_sponsor_ad(request):
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
