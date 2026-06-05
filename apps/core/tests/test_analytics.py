import json
from urllib.parse import quote

import pytest
from django.test import override_settings

from apps.core.analytics import queue_track_event
from apps.core.tasks import track_event, try_create_posthog_alias


@override_settings(POSTHOG_API_KEY="")
def test_queue_track_event_noops_without_posthog_key(monkeypatch):
    def fail_async_task(*args, **kwargs):
        raise AssertionError("analytics task should not be queued")

    monkeypatch.setattr("apps.core.analytics.async_task", fail_async_task)

    assert queue_track_event(event_name="repository_liked", profile_id=1) is None


@override_settings(POSTHOG_API_KEY="phc_test")
def test_queue_track_event_queues_worker_task(monkeypatch):
    queued = {}

    def fake_async_task(func_path, **kwargs):
        queued["func_path"] = func_path
        queued["kwargs"] = kwargs
        return "task-id"

    monkeypatch.setattr("apps.core.analytics.async_task", fake_async_task)

    task_id = queue_track_event(
        event_name="repository_liked",
        profile_id=1,
        properties={"repository_full_name": "django/django"},
        source_function="test",
    )

    assert task_id == "task-id"
    assert queued == {
        "func_path": "apps.core.tasks.track_event",
        "kwargs": {
            "profile_id": 1,
            "distinct_id": None,
            "event_name": "repository_liked",
            "properties": {"repository_full_name": "django/django"},
            "source_function": "test",
            "group": "Track Event",
        },
    }


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_track_event_identifies_profile_without_email(profile, monkeypatch):
    captured = {}

    def fake_capture(distinct_id, *, event, properties):
        captured["distinct_id"] = distinct_id
        captured["event"] = event
        captured["properties"] = properties

    monkeypatch.setattr("apps.core.tasks.posthog.capture", fake_capture)

    result = track_event(
        profile_id=profile.id,
        event_name="signup_completed",
        properties={
            "method": "github",
            "email": profile.user.email,
            "$set": {
                "email": profile.user.email,
                "company_email": "team@example.com",
            },
            "items": [
                {
                    "name": "seat",
                    "email": profile.user.email,
                }
            ],
        },
    )

    assert result == "Tracked event signup_completed"
    assert captured["distinct_id"] == str(profile.id)
    assert captured["event"] == "signup_completed"
    assert captured["properties"]["profile_id"] == profile.id
    assert captured["properties"]["method"] == "github"
    assert "email" not in captured["properties"]
    assert captured["properties"]["items"] == [{"name": "seat"}]
    assert captured["properties"]["$set"] == {
        "profile_id": profile.id,
        "current_state": profile.state,
    }
    assert profile.user.email not in str(captured)


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_track_event_does_not_set_person_properties_by_default(profile, monkeypatch):
    captured = {}
    logs = []

    def fake_capture(distinct_id, *, event, properties):
        captured["distinct_id"] = distinct_id
        captured["event"] = event
        captured["properties"] = properties

    monkeypatch.setattr("apps.core.tasks.posthog.capture", fake_capture)
    monkeypatch.setattr(
        "apps.core.tasks.logger.info",
        lambda message, **kwargs: logs.append((message, kwargs)),
    )

    result = track_event(
        profile_id=profile.id,
        event_name="repository_liked",
        properties={"repository_full_name": "django/django"},
    )

    assert result == "Tracked event repository_liked"
    assert captured["distinct_id"] == str(profile.id)
    assert captured["event"] == "repository_liked"
    assert captured["properties"]["profile_id"] == profile.id
    assert captured["properties"]["repository_full_name"] == "django/django"
    assert "$set" not in captured["properties"]
    assert logs == [
        (
            "[TrackEvent] Tracked event",
            {
                "profile_id": profile.id,
                "distinct_id": str(profile.id),
                "event_name": "repository_liked",
                "properties": captured["properties"],
                "source_function": None,
            },
        )
    ]


@pytest.mark.django_db
@override_settings(POSTHOG_API_KEY="phc_test")
def test_try_create_posthog_alias_does_not_alias_email(profile, monkeypatch):
    aliases = []
    cookie_name = "ph_phc_test_posthog"
    cookie_value = quote(json.dumps({"distinct_id": "anon-id"}))

    def fake_alias(previous_id, distinct_id):
        aliases.append((previous_id, distinct_id))

    monkeypatch.setattr("apps.core.tasks.posthog.alias", fake_alias)

    result = try_create_posthog_alias(profile.id, {cookie_name: cookie_value})

    assert result == f"Set PostHog alias for profile {profile.id}."
    assert aliases == [("anon-id", str(profile.id))]
    assert profile.user.email not in str(aliases)
