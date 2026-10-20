"""Admin approval is explicit, tenant-independent, audited, and defaults off."""

import pytest
from sqlalchemy import select
from test_web import login, own_account, post
from test_web import web as web

from campushallen.database.models import AuditEvent, CampushallenAccount, User


def prepare(client, app, *, admin=True):
    login(client)
    account_id = own_account(client, app)
    with app.state.sessions() as db:
        db.get(User, app.state.alice_id).is_admin = admin
        a = db.get(CampushallenAccount, account_id)
        assert not a.live_booking_enabled and not a.live_booking_ongoing
        a.auth_state = "opaque-test-ciphertext"
        db.commit()
    return account_id


def test_admin_requires_confirmation_and_can_stop_without_deleting_claims(web):
    client, app = web
    account_id = prepare(client, app)
    route = f"/admin/connections/{account_id}/live"
    assert client.get(route).status_code == 200
    assert post(client, route, {"action": "enable"}).status_code == 422
    assert (
        client.post(route, data={"action": "enable", "confirmation": "ENABLE"}).status_code == 403
    )
    assert post(client, route, {"action": "enable", "confirmation": "ENABLE"}).status_code == 303
    for path in ("/", f"/accounts/{account_id}", "/rules"):
        assert "Aktiv — bokningstjänsten måste köras i liveläge" in client.get(path).text
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, account_id)
        assert a.live_booking_enabled and a.live_booking_ongoing
        assert not db.get(CampushallenAccount, app.state.bob_account).live_booking_enabled
    assert post(client, route, {"action": "disable"}).status_code == 303
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, account_id)
        assert not a.live_booking_enabled and not a.live_booking_ongoing
        codes = list(db.scalars(select(AuditEvent.code)))
        assert "LIVE_BOOKING_ENABLED" in codes and "LIVE_BOOKING_DISABLED" in codes


def test_user_cannot_approve_own_or_other_connection(web):
    client, app = web
    account_id = prepare(client, app, admin=False)
    for identifier in (account_id, app.state.bob_account):
        route = f"/admin/connections/{identifier}/live"
        assert client.get(route).status_code == 403
        assert (
            post(client, route, {"action": "enable", "confirmation": "ENABLE"}).status_code == 403
        )
    for path in ("/", f"/accounts/{account_id}", "/rules"):
        assert "Testläge / väntar på aktivering" in client.get(path).text


@pytest.mark.parametrize("disabled,controlled", [(True, False), (False, True)])
def test_admin_cannot_bypass_disabled_auth_or_unverified_controlled_grant(
    web, disabled, controlled
):
    client, app = web
    account_id = prepare(client, app)
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, account_id)
        a.enabled = not disabled
        a.live_booking_activity_id = 4342668 if controlled else None
        db.commit()
    assert (
        post(
            client,
            f"/admin/connections/{account_id}/live",
            {"action": "enable", "confirmation": "ENABLE"},
        ).status_code
        == 409
    )


def test_controlled_grant_can_only_become_ongoing_after_corrected_verified_booking(web):
    from campushallen.database.models import BookingAttempt

    client, app = web
    account_id = prepare(client, app)
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, account_id)
        a.live_booking_activity_id = 4342668
        db.add(
            BookingAttempt(
                campushallen_account_id=account_id,
                activity_id=4342668,
                result="BOOKED",
                phase="VERIFIED_BOOKED",
                message="mock verified",
                is_preview=False,
            )
        )
        db.commit()
    assert (
        post(
            client,
            f"/admin/connections/{account_id}/live",
            {"action": "enable", "confirmation": "ENABLE"},
        ).status_code
        == 303
    )
