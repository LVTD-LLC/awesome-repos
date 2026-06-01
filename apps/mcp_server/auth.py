from asgiref.sync import sync_to_async
from django.db import close_old_connections
from fastmcp.server.auth import AccessToken, TokenVerifier

from apps.core.api_keys import get_profile_for_api_key
from apps.core.models import Profile


def get_profile_for_mcp_api_key(token: str) -> Profile | None:
    close_old_connections()
    try:
        return get_profile_for_api_key(token)
    finally:
        close_old_connections()


class AwesomeReposAPIKeyVerifier(TokenVerifier):
    """FastMCP bearer-token verifier backed by Awesome API keys."""

    async def verify_token(self, token: str) -> AccessToken | None:
        profile = await sync_to_async(get_profile_for_mcp_api_key, thread_sensitive=True)(token)
        if profile is None:
            return None

        return AccessToken(
            token=token,
            client_id=str(profile.user_id),
            scopes=["awesome-repos:read"],
            claims={
                "profile_id": profile.id,
                "user_id": profile.user_id,
                "email": profile.user.email,
            },
        )
