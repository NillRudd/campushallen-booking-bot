"""Admin approval is explicit, tenant-independent, audited, and defaults off."""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from test_web import login, own_account, post
from test_web import web as web

from campushallen.database.models import (
    AuditEvent,
    BookingRule,
    CampushallenAccount,
    Outcome,
    User,
    utcnow,
)


def prepare(client, app, *, admin=True):
    login(client)
    account_id = own_account(client, app)
    with app.state.sessions() as db:
        db.get(User, app.state.alice_id).is_admin = admin
        a = db.get(CampushallenAccount, account_id)
        assert not a.live_booking_enabled and not a.live_booking_ongoing
        a.auth_state = app.state.gateway.vault.seal(
            {"customer_id": account_id * 100, "access_token": "synthetic"}
        )
        from campushallen.services.credentials import remember

        remember(db, app.state.credential_vault, a, "synthetic-private-password")
        rule = BookingRule(
            campushallen_account_id=a.id, name="Beta rule", activity="Ball", weekday=1
        )
        db.add(rule)
        db.flush()
        slot = SimpleNamespace(
            id=4342668,
            name="Ball",
            starts_at=utcnow() + timedelta(days=7),
            bookable_earliest=utcnow() + timedelta(days=1),
        )
        app.state.engine.discover = lambda account_id: [
            SimpleNamespace(rule_id=rule.id, slot=slot, outcome=Outcome.DRY_RUN)
        ]
        db.commit()
    return account_id


def test_admin_requires_confirmation_and_can_stop_without_deleting_claims(web):
    client, app = web
    account_id = prepare(client, app)
    route = f"/admin/connections/{account_id}/live"
    assert client.get(route).status_code == 200
    assert post(client, route, {"action": "enable"}).status_code == 422
    assert (
        client.post(
            route,
            data={"action": "enable", "confirmation": "ENABLE", "identity_confirmation": "CLEAR"},
        ).status_code
        == 403
    )
    assert (
        post(
            client,
            route,
            {"action": "enable", "confirmation": "ENABLE", "identity_confirmation": "CLEAR"},
        ).status_code
        == 303
    )
    for path in ("/", f"/accounts/{account_id}", "/rules"):
        assert "Aktiv" in client.get(path).text
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
            post(
                client,
                route,
                {"action": "enable", "confirmation": "ENABLE", "identity_confirmation": "CLEAR"},
            ).status_code
            == 403
        )
    for path in ("/", f"/accounts/{account_id}", "/rules"):
        assert "Pausad" in client.get(path).text


@pytest.mark.parametrize("disabled,controlled", [(True, False), (False, True)])
def test_admin_blocks_disabled_auth_but_can_enable_ongoing_without_a_test_booking(
    web, disabled, controlled
):
    client, app = web
    account_id = prepare(client, app)
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, account_id)
        a.enabled = not disabled
        a.live_booking_activity_id = 4342668 if controlled else None
        db.commit()
    assert post(
        client,
        f"/admin/connections/{account_id}/live",
        {"action": "enable", "confirmation": "ENABLE", "identity_confirmation": "CLEAR"},
    ).status_code == (409 if disabled else 303)


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
            {"action": "enable", "confirmation": "ENABLE", "identity_confirmation": "CLEAR"},
        ).status_code
        == 303
    )


def test_live_activation_requires_external_identity_and_preference_review(web):
    client, app = web
    account_id = prepare(client, app)
    route = f"/admin/connections/{account_id}/live"
    assert post(client, route, {"action": "enable", "confirmation": "ENABLE"}).status_code == 422
    assert "identity_confirmation" in client.get(route).text


@pytest.mark.parametrize("problem", ["credential", "ambiguous", "legacy", "unresolved_claim"])
def test_matching_warnings_do_not_block_live_but_duplicate_identity_does(web, problem):
    from sqlalchemy import delete

    from campushallen.database.models import BookingClaim, CampushallenCredential
    from campushallen.services.identities import register_external

    client, app = web
    account_id = prepare(client, app)
    with app.state.sessions() as db:
        if problem == "credential":
            db.execute(
                delete(CampushallenCredential).where(
                    CampushallenCredential.campushallen_account_id == account_id
                )
            )
        elif problem == "legacy":
            register_external(
                db, app.state.gateway.vault, account_id * 100, "friend_legacy", active=True
            )
        elif problem == "ambiguous":
            app.state.engine.discover = lambda account_id: []
        elif problem == "unresolved_claim":
            db.add(BookingClaim(campushallen_account_id=account_id, activity_id=4342668))
        db.commit()
    route = f"/admin/connections/{account_id}/live"
    page = client.get(route)
    assert 'role="status"' in page.text
    assert "<button disabled" not in page.text
    assert post(
        client,
        route,
        {"action": "enable", "confirmation": "ENABLE", "identity_confirmation": "CLEAR"},
    ).status_code == (409 if problem == "legacy" else 303)
    with app.state.sessions() as db:
        assert db.get(CampushallenAccount, account_id).live_booking_enabled == (problem != "legacy")
