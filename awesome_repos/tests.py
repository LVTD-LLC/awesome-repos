import logging

from awesome_repos.sentry_utils import build_traces_sampler, logging_level_from_env


def test_logging_level_from_env_accepts_known_names():
    assert logging_level_from_env("warning", logging.INFO) == logging.WARNING
    assert logging_level_from_env(" ERROR ", logging.INFO) == logging.ERROR


def test_logging_level_from_env_accepts_numeric_strings():
    assert logging_level_from_env("30", logging.INFO) == 30


def test_logging_level_from_env_falls_back_for_unknown_names():
    assert logging_level_from_env("not-a-level", logging.INFO) == logging.INFO


def test_sentry_traces_sampler_respects_parent_sampling_decision():
    sampler = build_traces_sampler(http_sample_rate=0.5, background_sample_rate=0.1)

    assert sampler({"parent_sampled": True}) == 1.0
    assert sampler({"parent_sampled": False}) == 0.0


def test_sentry_traces_sampler_drops_ignored_paths_before_parent_sampling():
    sampler = build_traces_sampler(http_sample_rate=0.5, background_sample_rate=0.1)

    assert (
        sampler(
            {
                "parent_sampled": True,
                "transaction_context": {"name": "/api/healthcheck", "op": "http.server"},
            }
        )
        == 0.0
    )


def test_sentry_traces_sampler_drops_healthcheck_and_static_paths():
    sampler = build_traces_sampler(http_sample_rate=0.5, background_sample_rate=0.1)

    assert (
        sampler({"transaction_context": {"name": "/api/healthcheck", "op": "http.server"}}) == 0.0
    )
    assert sampler({"transaction_context": {"name": "/static/app.css", "op": "http.server"}}) == 0.0
    assert (
        sampler({"transaction_context": {"name": "/media/photo.jpg", "op": "http.server"}}) == 0.0
    )


def test_sentry_traces_sampler_uses_route_specific_rates():
    sampler = build_traces_sampler(http_sample_rate=0.5, background_sample_rate=0.1)

    assert sampler({"transaction_context": {"name": "/", "op": "http.server"}}) == 0.5
    assert (
        sampler({"transaction_context": {"name": "apps.repos.tasks.refresh", "op": "queue.task"}})
        == 0.1
    )
