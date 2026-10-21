"""Connected users get ongoing live; rules and account pause remain user-controlled."""

import pytest
from test_admin_live import prepare
from test_credentials_reauth import authenticated_gateway as authenticated_gateway
from test_engine import booking as booking
from test_web import post
from test_web import web as web

from campushallen.database.models import BookingIdentity, CampushallenAccount, User
from campushallen.services.account_management import connect_account
from campushallen.services.identities import identity_for, reserve_identity
from campushallen.services.live_policy import activate_connected


@pytest.mark.parametrize("remember", [False, True])
def test_successful_connection_enables_ongoing_live_without_admin_or_rules(
    authenticated_gateway, remember
):
    b = authenticated_gateway
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.account_id)
        assert not db.get(User, a.user_id).is_admin
        connect_account(
            db, b.gateway, b.credentials, a, "private-test-password", save_password=remember
        )
        assert a.live_booking_enabled and a.live_booking_ongoing
        identity = db.get(BookingIdentity, identity_for(a, b.tokens))
        assert identity.account_id == a.id and identity.reviewed_by == a.user_id
    assert b.server.login_calls == 1


def test_reconnect_does_not_unpause_disabled_account(authenticated_gateway):
    b = authenticated_gateway
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.account_id)
        a.enabled = False
        db.commit()
        connect_account(
            db, b.gateway, b.credentials, a, "private-test-password", save_password=True
        )
        assert not a.enabled and not a.live_booking_enabled


def test_owner_can_resume_and_pause_without_admin_and_foreign_account_is_denied(web):
    client, app = web
    aid = prepare(client, app, admin=False)
    route = f"/accounts/{aid}/toggle"
    assert post(client, route, {"action": "resume"}).status_code == 303
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, aid)
        assert a.enabled and a.live_booking_enabled and a.live_booking_ongoing
    assert post(client, route, {"action": "pause"}).status_code == 303
    with app.state.sessions() as db:
        assert not db.get(CampushallenAccount, aid).enabled
    assert (
        post(client, f"/accounts/{app.state.bob_account}/toggle", {"action": "resume"}).status_code
        == 404
    )
    assert post(client, route, {"action": "resume"}).status_code == 303


def test_ongoing_activation_retains_history_and_unrelated_rule_warnings_do_not_block(web):
    client, app = web
    aid = prepare(client, app, admin=False)
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, aid)
        a.live_booking_activity_id = 4342668
        a.live_booking_claim_limit = 0
        db.commit()
        activate_connected(db, a, app.state.gateway.vault, actor_id=a.user_id)
        db.commit()
        assert a.live_booking_ongoing and a.live_booking_enabled
        assert a.live_booking_activity_id == 4342668 and a.live_booking_claim_limit == 0


def test_owner_cannot_reserve_foreign_identity(web):
    client, app = web
    aid = prepare(client, app, admin=False)
    with app.state.sessions() as db:
        a = db.get(CampushallenAccount, aid)
        foreign = db.get(CampushallenAccount, app.state.bob_account)
        foreign.auth_state = app.state.gateway.vault.seal({"customer_id": 99999})
        db.commit()
        with pytest.raises(ValueError, match="Only the account owner"):
            reserve_identity(
                db,
                foreign,
                app.state.gateway.vault,
                a.user_id,
                expected_digest=identity_for(foreign, app.state.gateway.vault),
            )


def test_connected_owner_can_book_other_targets_without_resetting_old_claims(booking):
    from campushallen.database.models import BookingClaim

    b = booking
    with b.sessions() as db:
        a = db.get(CampushallenAccount, b.accounts[0])
        a.auth_state = b.gateway.vault.seal({"customer_id": a.id * 100})
        a.live_booking_enabled = False
        a.live_booking_claim_limit = 0
        a.live_booking_activity_id = 999
        old = BookingClaim(campushallen_account_id=a.id, activity_id=999)
        db.add(old)
        db.commit()
        original = old.created_at
        activate_connected(db, a, b.gateway.vault, actor_id=a.user_id)
        db.commit()
    assert b.engine.run(b.plan, dry_run=False).result == "BOOKED"
    assert len(b.gateway.posts) == 1
    with b.sessions() as db:
        assert db.get(BookingClaim, (b.accounts[0], 999)).created_at == original
        assert db.get(BookingClaim, (b.accounts[0], 101))
