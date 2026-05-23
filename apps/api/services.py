from apps.core.models import Profile
from apps.repos.search_services import (  # noqa: F401
    DEFAULT_API_PAGE_SIZE,
    MAX_API_PAGE_SIZE,
    get_awesome_list_detail_payload,
    get_awesome_list_repository_options_payload,
    get_repository_detail_payload,
    normalized_query_params,
    paginate_queryset,
    search_awesome_list_repositories_payload,
    search_awesome_lists_payload,
    search_repositories_payload,
    serialize_awesome_list_summary,
    serialize_repository_summary,
)


def serialize_user_info(profile: Profile) -> dict:
    """Return safe user/profile details for API consumers."""
    user = profile.user
    return {
        "id": user.id,
        "email": user.email,
        "username": user.username,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "full_name": user.get_full_name(),
        "date_joined": user.date_joined,
        "profile": {
            "id": profile.id,
            "state": profile.state,
            "has_active_subscription": False,
        },
    }
