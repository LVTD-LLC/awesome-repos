import json
from urllib.parse import unquote

import posthog
from django.conf import settings

from apps.core.models import Profile
from awesome_repos.utils import get_awesome_repos_logger

logger = get_awesome_repos_logger(__name__)

SENSITIVE_ANALYTICS_PROPERTY_KEYS = {"cookies", "email", "posthog_cookie"}


def _scrub_analytics_value(value):
    if isinstance(value, dict):
        return _scrub_analytics_properties(value)
    if isinstance(value, list):
        return [_scrub_analytics_value(item) for item in value]
    return value


def _scrub_analytics_properties(properties: dict) -> dict:
    scrubbed = {}
    for key, value in properties.items():
        normalized_key = str(key).lower()
        if normalized_key in SENSITIVE_ANALYTICS_PROPERTY_KEYS or normalized_key.endswith("_email"):
            continue
        scrubbed[key] = _scrub_analytics_value(value)
    return scrubbed


def try_create_posthog_alias(profile_id: int, cookies: dict, source_function: str = None) -> str:
    if not settings.POSTHOG_API_KEY:
        return "PostHog API key not found."

    base_log_data = {
        "profile_id": profile_id,
        "source_function": source_function,
    }

    try:
        Profile.objects.only("id").get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error("[Try Create Posthog Alias] Profile not found.", **base_log_data)
        return f"Profile with id {profile_id} not found."

    posthog_cookie = cookies.get(f"ph_{settings.POSTHOG_API_KEY}_posthog")
    if not posthog_cookie:
        logger.warning("[Try Create Posthog Alias] No PostHog cookie found.", **base_log_data)
        return f"No PostHog cookie found for profile {profile_id}."

    logger.info("[Try Create Posthog Alias] Setting PostHog alias", **base_log_data)

    try:
        cookie_dict = json.loads(unquote(posthog_cookie))
    except json.JSONDecodeError:
        logger.warning("[Try Create Posthog Alias] Invalid PostHog cookie.", **base_log_data)
        return f"Invalid PostHog cookie for profile {profile_id}."

    frontend_distinct_id = cookie_dict.get("distinct_id")

    if frontend_distinct_id:
        posthog.alias(frontend_distinct_id, str(profile_id))
    else:
        logger.warning("[Try Create Posthog Alias] Missing distinct id.", **base_log_data)
        return f"No PostHog distinct id found for profile {profile_id}."

    logger.info("[Try Create Posthog Alias] Set PostHog alias", **base_log_data)
    return f"Set PostHog alias for profile {profile_id}."


def track_event(
    profile_id: int | None,
    event_name: str,
    properties: dict | None = None,
    source_function: str = None,
    distinct_id: str | None = None,
) -> str:
    if not settings.POSTHOG_API_KEY:
        return "PostHog API key not found."

    properties = _scrub_analytics_properties(properties or {})
    base_log_data = {
        "profile_id": profile_id,
        "distinct_id": distinct_id,
        "event_name": event_name,
        "properties": properties,
        "source_function": source_function,
    }

    event_properties = {**properties}
    if profile_id is not None:
        try:
            profile = Profile.objects.get(id=profile_id)
        except Profile.DoesNotExist:
            logger.error("[TrackEvent] Profile not found.", **base_log_data)
            return f"Profile with id {profile_id} not found."

        distinct_id = distinct_id or str(profile.id)
        event_properties = {
            "profile_id": profile.id,
            "current_state": profile.state,
            "is_authenticated": True,
            **event_properties,
        }
        set_properties = event_properties.get("$set")
        if set_properties is not None and isinstance(set_properties, dict):
            event_properties["$set"] = {
                "profile_id": profile.id,
                "current_state": profile.state,
                **set_properties,
            }

    capture_log_data = {
        **base_log_data,
        "distinct_id": distinct_id,
        "properties": event_properties,
    }

    if not distinct_id:
        logger.warning("[TrackEvent] Missing distinct id.", **capture_log_data)
        return f"No distinct id provided for event {event_name}."

    posthog.capture(
        distinct_id,
        event=event_name,
        properties=event_properties,
    )

    logger.info("[TrackEvent] Tracked event", **capture_log_data)

    return f"Tracked event {event_name}"


def track_state_change(
    profile_id: int,
    from_state: str,
    to_state: str,
    metadata: dict = None,
    source_function: str = None,
) -> None:
    from apps.core.models import Profile, ProfileStateTransition

    base_log_data = {
        "profile_id": profile_id,
        "from_state": from_state,
        "to_state": to_state,
        "metadata": metadata,
        "source_function": source_function,
    }

    try:
        profile = Profile.objects.get(id=profile_id)
    except Profile.DoesNotExist:
        logger.error("[TrackStateChange] Profile not found.", **base_log_data)
        return f"Profile with id {profile_id} not found."

    if from_state != to_state:
        logger.info("[TrackStateChange] Tracking state change", **base_log_data)
        ProfileStateTransition.objects.create(
            profile=profile,
            from_state=from_state,
            to_state=to_state,
            backup_profile_id=profile_id,
            metadata=metadata,
        )
        profile.state = to_state
        profile.save(update_fields=["state"])

    return f"Tracked state change from {from_state} to {to_state} for profile {profile_id}"
