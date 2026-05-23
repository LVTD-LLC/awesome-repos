from apps.core.model_utils import get_api_key_prefix
from apps.core.models import Profile
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)


def get_profile_for_api_key(key: str) -> Profile | None:
    logger.info("[API Key Auth] API key request")
    api_key_prefix = get_api_key_prefix(key)
    if not api_key_prefix:
        logger.warning("[API Key Auth] Invalid API key format")
        return None

    try:
        profile = Profile.objects.select_related("user").get(api_key_prefix=api_key_prefix)
    except Profile.DoesNotExist:
        logger.warning("[API Key Auth] Invalid API key prefix")
        return None

    if not profile.check_api_key(key):
        logger.warning("[API Key Auth] Invalid API key secret")
        return None
    return profile
