"""Structured logging.

IntentLock logs structured key/value data rather than interpolated prose so
that telemetry is machine-parseable and cannot be corrupted by attacker-influenced
strings (log injection). `sanitize_value` is the guard for the latter.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import MutableMapping
from typing import Any

# Control characters and newlines in a value let an attacker forge additional
# log records. They are stripped from every string that reaches the log.
_FORBIDDEN = str.maketrans({"\n": "\\n", "\r": "\\r", "\t": "\\t", "\x00": ""})

_MAX_VALUE_LEN = 512


def sanitize_value(value: Any, _depth: int = 0) -> Any:
    """Recursively make `value` safe for structured logging.

    - newlines/CR/tab/NUL are escaped (prevents log injection)
    - strings are truncated to `_MAX_VALUE_LEN`
    - non-JSON-serialisable objects are replaced by their type name
    - container depth is bounded to avoid pathological nesting
    """
    if _depth > 6:
        return "<max-depth>"
    if isinstance(value, str):
        cleaned = value.translate(_FORBIDDEN)
        if len(cleaned) > _MAX_VALUE_LEN:
            return cleaned[:_MAX_VALUE_LEN] + "…"
        return cleaned
    if isinstance(value, dict):
        return {str(k)[:64]: sanitize_value(v, _depth + 1) for k, v in list(value.items())[:64]}
    if isinstance(value, (list, tuple, set)):
        items = list(value)[:64]
        return [sanitize_value(v, _depth + 1) for v in items]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return f"<{type(value).__name__}>"


class _StructuredFormatter(logging.Formatter):
    """Renders records as single-line JSON, with message as the `event` key."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "event": sanitize_value(record.getMessage()),
        }
        for key, value in getattr(record, "extra_fields", {}).items():
            payload[key] = sanitize_value(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info).translate(_FORBIDDEN)
        return json.dumps(payload, ensure_ascii=False)


class StructuredLogger(logging.LoggerAdapter):
    """Logger adapter that accepts arbitrary keyword fields.

    Usage::

        log.info("gesture_classified", gesture="LEFT", confidence=0.91)
    """

    def process(
        self, msg: object, kwargs: MutableMapping[str, Any]
    ) -> tuple[object, MutableMapping[str, Any]]:
        extra = dict(self.extra or {})
        fields = kwargs.pop("extra_fields", {})
        extra["extra_fields"] = fields
        kwargs["extra"] = extra
        return msg, kwargs

    def info(self, msg: object, *args: Any, **kwargs: Any) -> None:
        if args or "extra_fields" in kwargs:
            self.log(logging.INFO, msg, *args, **kwargs)
        else:
            self.log(logging.INFO, msg, **kwargs)

    def warning(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self.log(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self.log(logging.ERROR, msg, *args, **kwargs)

    def debug(self, msg: object, *args: Any, **kwargs: Any) -> None:
        self.log(logging.DEBUG, msg, *args, **kwargs)


_CONFIGURED = False


def configure_logging(level: str = "INFO", json_output: bool = True) -> None:
    """Install the IntentLock logging configuration on the root logger once."""
    global _CONFIGURED
    handler = logging.StreamHandler(sys.stderr)
    if json_output:
        handler.setFormatter(_StructuredFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root = logging.getLogger("intentlock")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.propagate = False
    _CONFIGURED = True


def get_logger(name: str, **context: Any) -> StructuredLogger:
    """Return a structured logger for `name` (typically `__name__`)."""
    if not _CONFIGURED:
        configure_logging()
    return StructuredLogger(logging.getLogger(name), dict(context))
