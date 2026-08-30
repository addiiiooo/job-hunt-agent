"""Structured logging. One JSON object per line, one line per event.

The request id is held in a ``ContextVar`` and injected by the formatter rather
than passed as an argument. That is what makes "every line carries a
request_id" true by construction instead of true by discipline — there is no
call site that can forget it.
"""

from __future__ import annotations

import json
import logging
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

_RESERVED = {
    "args", "asctime", "created", "exc_info", "exc_text", "filename", "funcName",
    "levelname", "levelno", "lineno", "module", "msecs", "message", "msg", "name",
    "pathname", "process", "processName", "relativeCreated", "stack_info",
    "thread", "threadName", "taskName",
}

LOGGER_NAME = "jobhunt"
logger = logging.getLogger(LOGGER_NAME)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "request_id": getattr(record, "request_id", None) or request_id_var.get(),
            "event": getattr(record, "event", record.getMessage()),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and key not in payload and not key.startswith("_"):
                payload[key] = _safe(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, separators=(",", ":"))


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_safe(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    return str(value)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


def set_request_id(request_id: str):
    return request_id_var.set(request_id)


def get_request_id() -> str:
    return request_id_var.get()


def log_event(event: str, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one structured line. ``event`` is the machine-readable name."""
    logger.log(level, event, extra={"event": event, **fields})
