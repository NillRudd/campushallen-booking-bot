"""Provider-neutral, non-sensitive events. No external delivery in this phase."""

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class NotificationKind(StrEnum):
    BOOKING_CONFIRMED = "BOOKING_CONFIRMED"
    BOOKING_FAILED = "BOOKING_FAILED"
    REAUTH_REQUIRED = "REAUTH_REQUIRED"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"


@dataclass(frozen=True)
class Notification:
    kind: NotificationKind
    user_id: int
    account_id: int
    attempt_id: int | None = None


class NotificationService(Protocol):
    def notify(self, event: Notification) -> None: ...


class NoopNotifier:
    def notify(self, event: Notification) -> None:
        pass


class LogNotifier:
    def notify(self, event: Notification) -> None:
        logging.getLogger(__name__).info(
            "Notification event", extra={"result": event.kind.value, "account_id": event.account_id}
        )


def deliver(notifier: NotificationService, event: Notification) -> None:
    try:
        notifier.notify(event)
    except Exception:
        logging.getLogger(__name__).error("Notification delivery failed")
