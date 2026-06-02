import logging
from collections.abc import Callable
from logging import LogRecord
from typing import Any

from sentry_sdk.integrations.logging import LoggingIntegration

_IGNORED_LOGGERS = {"ask_hn_digest"}
_IGNORED_TRANSACTION_PATHS = {"/api/healthcheck"}
_IGNORED_TRANSACTION_PREFIXES = ("/static/", "/media/")


class CustomLoggingIntegration(LoggingIntegration):
    def _handle_record(self, record: LogRecord) -> None:
        # This match upper logger names, e.g. "celery" will match "celery.worker"
        # or "celery.worker.job"
        if record.name in _IGNORED_LOGGERS or record.name.split(".")[0] in _IGNORED_LOGGERS:
            return
        super()._handle_record(record)


def before_send(event, hint):
    if "exc_info" in hint:
        _exc_type, exc_value, _tb = hint["exc_info"]

        if isinstance(exc_value, SystemExit):  # group all SystemExits together
            event["fingerprint"] = ["system-exit"]
    return event


def logging_level_from_env(value: str, default: int) -> int:
    stripped = value.strip()
    if stripped.isdigit():
        return int(stripped)

    level = logging.getLevelName(stripped.upper())
    if isinstance(level, int):
        return level
    return default


def build_traces_sampler(
    *,
    http_sample_rate: float,
    background_sample_rate: float,
) -> Callable[[dict[str, Any]], float]:
    def traces_sampler(sampling_context: dict[str, Any]) -> float:
        transaction_context = sampling_context.get("transaction_context") or {}
        transaction_name = transaction_context.get("name") or ""
        transaction_op = transaction_context.get("op") or ""

        if transaction_name in _IGNORED_TRANSACTION_PATHS:
            return 0.0
        if any(transaction_name.startswith(prefix) for prefix in _IGNORED_TRANSACTION_PREFIXES):
            return 0.0

        parent_sampled = sampling_context.get("parent_sampled")
        if parent_sampled is not None:
            return 1.0 if parent_sampled else 0.0

        if transaction_op.startswith("http") or transaction_name.startswith("/"):
            return http_sample_rate

        return background_sample_rate

    return traces_sampler
