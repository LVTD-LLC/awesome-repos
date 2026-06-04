from __future__ import annotations

from typing import Any

from django.conf import settings
from django_q.tasks import async_task


def queue_track_event(
    *,
    event_name: str,
    properties: dict[str, Any] | None = None,
    profile_id: int | None = None,
    distinct_id: str | None = None,
    source_function: str | None = None,
) -> str | None:
    if not settings.POSTHOG_API_KEY:
        return None

    return async_task(
        "apps.core.tasks.track_event",
        profile_id=profile_id,
        distinct_id=distinct_id,
        event_name=event_name,
        properties=properties or {},
        source_function=source_function,
        group="Track Event",
    )
