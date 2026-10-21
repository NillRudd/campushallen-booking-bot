import httpx
import pytest

from campushallen.services.provider_errors import (
    ERROR_CATEGORIES,
    ProviderRejection,
    provider_rejection,
)
from campushallen.web.presentation import reason


@pytest.mark.parametrize("code,category", ERROR_CATEGORIES.items())
def test_known_provider_enum_is_the_only_retained_response_value(code, category):
    response = httpx.Response(
        403,
        json={
            "errorCode": code,
            "message": "private-secret-body",
            "rule": {"customerId": "private-secret-body", "limit": "private-secret-body"},
            "fieldErrors": [{"field": "password", "message": "private-secret-body"}],
        },
        headers={"Set-Cookie": "private-secret-body"},
    )
    result = provider_rejection(response)
    assert result.category == category
    assert result.code == code
    assert "private-secret-body" not in result.message
    assert len(result.message) < 200
    assert code in reason(result.message)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(403, json={"errorCode": "PRIVATE_SECRET", "message": "limit 4"}),
        httpx.Response(401, json={"message": "private-secret-body"}),
        httpx.Response(403, json={"errorCode": ["TOO_MANY_CONCURRENT_BOOKINGS"]}),
        httpx.Response(403, json={"code": "TOO_MANY_CONCURRENT_BOOKINGS"}),
        httpx.Response(403, json=[{"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}]),
        httpx.Response(403, text="<html>private-secret-body</html>"),
        httpx.Response(403, content=b"{", headers={"content-type": "application/json"}),
        httpx.Response(403, content=b"[" * 2000, headers={"content-type": "application/json"}),
        httpx.Response(
            403,
            json={"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS", "message": "x" * 16_384},
        ),
        httpx.Response(500, json={"errorCode": "TOO_MANY_CONCURRENT_BOOKINGS"}),
    ],
)
def test_generic_status_prose_unknown_code_html_or_unbounded_json_never_classify(response):
    assert provider_rejection(response) == ProviderRejection()


def test_unsafe_historical_message_is_not_displayed_as_a_provider_reason():
    assert "private-secret-body" not in reason(
        "Provider rejection: BOOKING_LIMIT_REACHED; errorCode=private-secret-body."
    )
    assert "private-secret-body" not in reason(
        "Authenticated bookings read: total=4; regular=private-secret-body; waitlist=0."
    )


def test_json_suffix_media_type_uses_proven_errorcode_field():
    r = httpx.Response(
        403,
        content=b'{"errorCode":"TOO_EARLY_TO_BOOK"}',
        headers={"content-type": "application/problem+json; charset=utf-8"},
    )
    assert provider_rejection(r).category == "NOT_BOOKABLE_YET"
