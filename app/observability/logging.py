from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

_correlation_id: ContextVar[str] = ContextVar("correlation_id", default="")

#: Context fields are emitted verbatim. Only identifiers, statuses, and counts may be
#: passed; never buyer names, addresses, emails, or raw webhook payloads.
_LOGGER_NAME = "ops_core"


def new_correlation_id() -> str:
    return uuid4().hex


def set_correlation_id(value: str) -> None:
    _correlation_id.set(value)


def get_correlation_id() -> str:
    return _correlation_id.get()


class JsonFormatter(logging.Formatter):
    """One JSON object per line so logs stay queryable and never interleave."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
        }
        correlation_id = getattr(record, "correlation_id", "") or get_correlation_id()
        if correlation_id:
            payload["correlation_id"] = correlation_id
        payload.update(getattr(record, "context", {}))
        if record.exc_info:
            payload["error"] = self.formatException(record.exc_info)
        return json.dumps(payload, sort_keys=True, default=str)


def configure_logging(level: int = logging.INFO, stream: Any | None = None) -> logging.Logger:
    """Idempotently install the JSON handler on the application logger."""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    return logger


class StructuredLogger:
    """Thin wrapper that forces every log call to be an event name plus safe fields."""

    def __init__(self, component: str) -> None:
        self._logger = logging.getLogger(f"{_LOGGER_NAME}.{component}")

    def _emit(self, level: int, event: str, exc_info: bool = False, **context: Any) -> None:
        self._logger.log(
            level,
            event,
            exc_info=exc_info,
            extra={"context": context, "correlation_id": get_correlation_id()},
        )

    def info(self, event: str, **context: Any) -> None:
        self._emit(logging.INFO, event, **context)

    def warning(self, event: str, **context: Any) -> None:
        self._emit(logging.WARNING, event, **context)

    def error(self, event: str, exc_info: bool = False, **context: Any) -> None:
        self._emit(logging.ERROR, event, exc_info=exc_info, **context)


def get_logger(component: str) -> StructuredLogger:
    return StructuredLogger(component)
