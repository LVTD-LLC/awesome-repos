from django.http import HttpRequest
from ninja.security import APIKeyHeader, HttpBearer

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


class APIKeyHeaderAuth(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str) -> Profile | None:
        return get_profile_for_api_key(key)


class BearerAPIKeyAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Profile | None:
        return get_profile_for_api_key(token)


class SessionAuth:
    """Authentication via Django session"""

    def authenticate(self, request: HttpRequest) -> Profile | None:
        if hasattr(request, "user") and request.user.is_authenticated:
            logger.info(
                "[Django Ninja Auth] API Request with authenticated user",
                user_id=request.user.id,
            )
            try:
                return request.user.profile
            except Profile.DoesNotExist:
                logger.warning("[Django Ninja Auth] No profile for user", user_id=request.user.id)
                return None
        return None

    def __call__(self, request: HttpRequest):
        return self.authenticate(request)


def _require_superuser(profile: Profile | None) -> Profile | None:
    if profile and profile.user.is_superuser:
        return profile

    if profile:
        logger.warning(
            "[API Key Auth] Non-superuser attempted admin access",
            profile_id=profile.user.id,
        )
    return None


class SuperuserAPIKeyHeaderAuth(APIKeyHeader):
    param_name = "X-API-Key"

    def authenticate(self, request: HttpRequest, key: str) -> Profile | None:
        return _require_superuser(get_profile_for_api_key(key))


class SuperuserBearerAPIKeyAuth(HttpBearer):
    def authenticate(self, request: HttpRequest, token: str) -> Profile | None:
        return _require_superuser(get_profile_for_api_key(token))


api_key_auth = [APIKeyHeaderAuth(), BearerAPIKeyAuth()]
session_auth = SessionAuth()
superuser_api_auth = [SuperuserAPIKeyHeaderAuth(), SuperuserBearerAPIKeyAuth()]
