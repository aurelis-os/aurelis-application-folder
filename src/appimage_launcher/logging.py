"""Structured logging for AppImage Launcher.

Outputs key=value format, one line per event, compatible with journald.
Levels controlled via -v/--verbose flags.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import MutableMapping
from typing import Any

# Add TRACE level (below DEBUG)
TRACE_LEVEL = 5
logging.addLevelName(TRACE_LEVEL, "TRACE")


class StructuredFormatter(logging.Formatter):
    """Formatter that outputs key=value pairs."""

    def format(self, record: logging.LogRecord) -> str:
        # Base fields
        parts = [
            f"ts={self.formatTime(record, '%Y-%m-%dT%H:%M:%S')}",
            f"level={record.levelname}",
            f"logger={record.name}",
            f"msg={record.getMessage()}",
        ]

        # Add extra fields from record.__dict__
        # Skip standard logging fields
        standard_fields = {
            "name",
            "msg",
            "args",
            "created",
            "filename",
            "funcName",
            "levelname",
            "levelno",
            "lineno",
            "module",
            "msecs",
            "message",
            "pathname",
            "process",
            "processName",
            "relativeCreated",
            "thread",
            "threadName",
            "exc_info",
            "exc_text",
            "stack_info",
            "asctime",
        }

        for key, value in record.__dict__.items():
            if key not in standard_fields and not key.startswith("_"):
                # Format value appropriately
                if isinstance(value, str):
                    # Escape spaces and special chars
                    if " " in value or "=" in value:
                        value = f'"{value}"'
                elif isinstance(value, (list, dict, tuple)):
                    value = str(value)
                parts.append(f"{key}={value}")

        return " ".join(parts)


def setup_logging(verbose: int = 0) -> None:
    """Configure logging based on verbosity level.

    Args:
        verbose: Verbosity level (-1=ERROR, 0=WARNING, 1=INFO, 2=DEBUG, 3+=TRACE)
    """
    if verbose < 0:
        level = logging.ERROR
    elif verbose == 0:
        level = logging.WARNING
    elif verbose == 1:
        level = logging.INFO
    elif verbose == 2:
        level = logging.DEBUG
    else:
        level = TRACE_LEVEL

    # Create handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(StructuredFormatter())

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.handlers = [handler]

    # Reduce noise from third-party libraries
    logging.getLogger("dbus_fast").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Get a logger instance for the given module name."""
    logger = logging.getLogger(name)
    # Wrap with LoggerAdapter to handle extra keyword arguments
    return LoggerAdapter(logger, {})


class LoggerAdapter(logging.LoggerAdapter[logging.Logger]):
    """Logger adapter that adds context to all log messages."""

    def __init__(self, logger: logging.Logger, extra: dict[str, Any] | None = None):
        super().__init__(logger, extra or {})

    def process(
        self, msg: str, kwargs: MutableMapping[str, Any]
    ) -> tuple[str, MutableMapping[str, Any]]:
        # Merge extra context
        if "extra" not in kwargs:
            kwargs["extra"] = {}
        kwargs["extra"].update(self.extra)

        # Move any non-standard keyword arguments into extra
        # Standard logging kwargs: exc_info, stack_info, stacklevel, extra
        standard_kwargs = {"exc_info", "stack_info", "stacklevel", "extra"}
        for key in list(kwargs.keys()):
            if key not in standard_kwargs:
                kwargs["extra"][key] = kwargs.pop(key)

        return msg, kwargs


def get_context_logger(name: str, **context: Any) -> LoggerAdapter:
    """Get a logger with persistent context fields."""
    logger = get_logger(name)
    return LoggerAdapter(logger, context)
