from __future__ import annotations

import logging
from pathlib import Path


SENSITIVE_WORDS = ("cookie", "authorization", "token", "password", "session")


class SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        lowered = message.lower()
        if any(word in lowered for word in SENSITIVE_WORDS):
            record.msg = "[redacted sensitive log message]"
            record.args = ()
        return True


def configure_logging(level: str, log_file: Path) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_file, encoding="utf-8"),
        ],
    )
    logging.getLogger().addFilter(SecretFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)
