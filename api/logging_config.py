"""Structured JSON logging with a per-request ID.

Why: plain-text logs are hard to search. JSON logs (one object per line) can be
filtered by field in any log tool. Each line includes ``request_id`` so all log
lines for one HTTP request can be grouped — the same ID is returned to the
client in the ``X-Request-ID`` header, which makes support/debugging easy.

How: the request-ID middleware stores the ID in a ``ContextVar`` (a variable
that is local to the current request even under async concurrency). The
``RequestIdFilter`` copies it onto every log record.
"""

from __future__ import annotations

import logging
import logging.config
from contextvars import ContextVar
from pathlib import Path

import yaml

request_id_ctx: ContextVar[str] = ContextVar("request_id", default="-")


class RequestIdFilter(logging.Filter):
    """Attach the current request ID to every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_ctx.get()
        return True


def setup_logging(config_path: str = "configs/logging.yaml") -> None:
    """Configure logging from YAML; fall back to basic logging if unavailable."""
    path = Path(config_path)
    try:
        with open(path, encoding="utf-8") as fh:
            logging.config.dictConfig(yaml.safe_load(fh))
    except Exception:  # noqa: BLE001 - logging must never crash the app
        logging.basicConfig(level=logging.INFO)
        logging.getLogger().addFilter(RequestIdFilter())


def get_logger(name: str = "api") -> logging.Logger:
    return logging.getLogger(name)
