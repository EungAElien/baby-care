from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Final

_ALLOWED_EXTRA_FIELDS: Final[tuple[str, ...]] = (
    "request_id",
    "http_method",
    "route",
    "status_code",
    "result_code",
    "duration_ms",
    "service_version",
    "contract_version",
)
_ALLOWED_EVENTS: Final[frozenset[str]] = frozenset({"request_completed"})


class SafeJsonFormatter(logging.Formatter):
    """Emit only an explicit metadata allowlist.

    Request headers, query strings, bodies, exception messages, tokens, raw text, audio,
    and signed URLs never enter the formatter's output fields.
    """

    def format(self, record: logging.LogRecord) -> str:
        event = record.msg if isinstance(record.msg, str) else "application_event"
        if event not in _ALLOWED_EVENTS:
            event = "application_event"
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": event,
        }
        for field_name in _ALLOWED_EXTRA_FIELDS:
            if hasattr(record, field_name):
                payload[field_name] = getattr(record, field_name)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(SafeJsonFormatter())

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    logging.getLogger("uvicorn.access").disabled = True
