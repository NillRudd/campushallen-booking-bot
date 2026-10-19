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
