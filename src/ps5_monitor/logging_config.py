"""Structured logging with secret redaction."""

from __future__ import annotations

import json
import logging
import re
import sys

# Telegram bot tokens look like 123456789:AA... and appear in API URLs (/bot<token>/...).
_TOKEN = re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b")
_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Make sure an exact secret value (token, chat id) is masked anywhere it appears in logs."""
    if value and len(value) >= 4:
        _secrets.add(value)


def redact(text: str) -> str:
    text = _TOKEN.sub("[REDACTED]", text)
    for secret in _secrets:
        text = text.replace(secret, "[REDACTED]")
    return text


class _Redactor(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        for key, value in list(vars(record).items()):
            if key not in _STANDARD and isinstance(value, str):
                setattr(record, key, redact(value))
        return True


_STANDARD = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "msg": record.getMessage(),
        }
        for key, value in vars(record).items():
            if key not in _STANDARD and not key.startswith("_"):
                payload[key.removeprefix("ctx_")] = value
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(_Redactor())
    if fmt == "json":
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full request URLs at INFO, and Telegram URLs contain the bot token.
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
