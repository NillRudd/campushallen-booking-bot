"""Bounded GET recovery only; this policy can never send/replay a mutation."""

import logging
import time
from collections.abc import Callable

import httpx

from .provider_errors import provider_rejection

LOGGER = logging.getLogger(__name__)
READ_DELAYS = (0.2, 0.5)
TEMPORARY_STATUSES = {408, 429, 500, 502, 503, 504}


def authentication_rejected(response: httpx.Response) -> bool:
    return response.status_code == 401 or (
        400 <= response.status_code < 500
        and provider_rejection(response).category == "AUTH_REJECTED"
    )


class ProviderReads:
    def __init__(self, account_id: int | None = None):
        self.account_id = account_id
        self.reauthenticate: Callable[[], None] | None = None
        self.on_auth_rejection: Callable[[int], None] | None = None

    def __call__(self, send: Callable[[], httpx.Response]) -> httpx.Response:
        # Authentication recovery spends one of the same three GET attempts.
        recovered = False
        for attempt in range(3):
            try:
                response = send()
            except httpx.TransportError:
                if attempt == 2:
                    raise
            else:
                if authentication_rejected(response):
                    if self.on_auth_rejection is not None:
                        self.on_auth_rejection(response.status_code)
                    if recovered or self.reauthenticate is None or attempt == 2:
                        return response
                    response.close()
                    recovered = True
                    self.reauthenticate()
                    continue
                if response.status_code not in TEMPORARY_STATUSES or attempt == 2:
                    return response
                response.close()
            LOGGER.info(
                "Temporary provider read failure; bounded retry",
                extra={
                    "account_id": self.account_id,
                    "phase": "read_retry",
                    "read_attempt": attempt + 2,
                },
            )
            time.sleep(READ_DELAYS[attempt])
        raise AssertionError("Unreachable GET retry state")
