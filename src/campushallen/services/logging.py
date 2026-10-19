"""Small structured worker logs with an explicit list of non-sensitive fields."""

import json
import logging
from datetime import datetime, timezone


class WorkerLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for name in ("account_id", "rule_id", "result"):
            if hasattr(record, name):
                data[name] = getattr(record, name)
        return json.dumps(data)


class InviteAccessFilter(logging.Filter):
    """Uvicorn logs a path in its third argument; invitation bearer tokens must be redacted."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3:
            args = list(record.args)
            from urllib.parse import unquote

            if isinstance(args[2], str) and unquote(args[2]).lstrip("/").startswith("invite/"):
                args[2] = "/invite/[redacted]"
                record.args = tuple(args)
        return True
