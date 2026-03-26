"""
logger.py — Structured JSON logging + file handler.
All platform modules import get_logger() from here.
"""

import sys
import json
import logging
import datetime
from pathlib import Path

from config import LOG_LEVEL, LOG_FORMAT, LOG_FILE


class JsonFormatter(logging.Formatter):
    """Emit each log record as a single JSON line (Grafana/Loki-friendly)."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts":      datetime.datetime.utcnow().isoformat() + "Z",
            "level":   record.levelname,
            "module":  record.name,
            "msg":     record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            payload.update(record.extra)
        return json.dumps(payload, default=str)


def _build_handler(stream=sys.stdout) -> logging.Handler:
    handler = logging.StreamHandler(stream)
    if LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s — %(message)s")
        )
    return handler


def _build_file_handler() -> logging.FileHandler:
    Path(LOG_FILE).parent.mkdir(parents=True, exist_ok=True)
    fh = logging.FileHandler(LOG_FILE, encoding="utf-8")
    fh.setFormatter(JsonFormatter())
    return fh


# Bootstrap root logger once
_root = logging.getLogger("platform")
if not _root.handlers:
    _root.setLevel(getattr(logging, LOG_LEVEL.upper(), logging.INFO))
    _root.addHandler(_build_handler())
    _root.addHandler(_build_file_handler())
    _root.propagate = False


def get_logger(name: str) -> logging.Logger:
    return _root.getChild(name)
