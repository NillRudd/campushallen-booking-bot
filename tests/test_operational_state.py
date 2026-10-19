import asyncio
from datetime import timedelta

from sqlalchemy import select
from test_credentials_reauth import authenticated_gateway as authenticated_gateway
from test_credentials_reauth import expire
from test_engine import booking as booking
from test_web import login, own_account, post, rule_form
from test_web import web as web

from campushallen.database.models import (
    BookingAttempt,
    BookingOpportunity,
    BookingRule,
    CampushallenAccount,
    WorkerState,
    utcnow,
)
from campushallen.services.engine import BookingEngine
from campushallen.services.notifications import NotificationKind
from campushallen.services.operations import Heartbeat, snapshot, worker_health
from campushallen.services.scheduler import Scheduler


def test_heartbeat_healthy_stale_and_stopped(booking):
    b = booking
    with b.sessions() as db:
        assert worker_health(db, 60)[0] == "Worker disabled / not running"
    heartbeat = Heartbeat(b.sessions, dry_run=True)
    heartbeat.beat()
    with b.sessions() as db:
        status, state = worker_health(db, 60)
        assert status == "Worker healthy" and state.dry_run
        state.heartbeat_at = utcnow() - timedelta(minutes=10)
        db.commit()
        assert worker_health(db, 60)[0] == "Worker hasn't reported recently"
    heartbeat.beat(stopped=True)
    with b.sessions() as db:
        assert worker_health(db, 60)[0] == "Worker disabled / not running"


def test_scheduler_snapshots_opening_capacity_and_last_discovery(booking):
    b = booking
    asyncio.run(Scheduler(b.engine).once())
    with b.sessions() as db:
        slot = db.scalar(
            select(BookingOpportunity).where(
                BookingOpportunity.campushallen_account_id == b.accounts[0]
            )
        )
        assert slot.activity_id == b.slot.id
        assert abs((slot.bookable_earliest - b.slot.bookable_earliest).total_seconds()) < 0.001
        assert slot.left_to_book == b.slot.left_to_book and slot.has_waiting_list
        assert not slot.already_booked and slot.worker_status == "dry_run"
        assert db.get(CampushallenAccount, b.accounts[0]).last_discovery_at
        assert db.get(CampushallenAccount, b.accounts[0]).last_success_at
        assert db.scalar(select(WorkerState)).stopped_at
    assert not b.gateway.posts


def test_future_task_has_durable_next_action_and_can_be_cancelled(booking):
    from dataclasses import replace

    b = booking
    opening = utcnow().replace(microsecond=0) + timedelta(minutes=5)
    b.gateway.slots = [replace(b.slot, bookable_earliest=opening)]

    async def run():
        scheduler = Scheduler(b.engine)
        await scheduler.refresh(wait=True)
        with b.sessions() as db:
            rows = db.scalars(select(BookingOpportunity)).all()
            assert len(rows) == 2 and all(row.next_action_at == opening for row in rows)
            assert all(row.worker_status == "waiting" for row in rows)
        for task in scheduler.tasks.values():
            task.cancel()
        await asyncio.gather(*scheduler.tasks.values(), return_exceptions=True)

    asyncio.run(run())
    assert not b.gateway.posts


def test_worker_uses_real_auth_adapter_reauth_and_account_failure_isolation(
    authenticated_gateway, monkeypatch
):
    b = authenticated_gateway
    expire(b)
    # All non-auth API calls remain mocked; no booking mutation is allowed.
    monkeypatch.setattr(
        "campushallen.api.CampushallenHttpClient.group_activities", lambda *args, **kwargs: []
    )
    monkeypatch.setattr(
        "campushallen.api.CampushallenHttpClient.customer_group_activity_bookings",
        lambda *args, **kwargs: [],
    )
    with b.sessions() as db:
        account = db.get(CampushallenAccount, b.account_id)
        db.add(
            BookingRule(
                campushallen_account_id=account.id,
                name="Target",
                activity="Ball",
                weekday=utcnow().weekday(),
            )
        )
        other = CampushallenAccount(
            user_id=account.user_id,
            username="other",
            display_name="Other",
            auth_state=b.tokens.seal({"access_token": "valid-token", "customer_id": 100}),
        )
        db.add(other)
        db.commit()
        other_id = other.id
        db.add(
            BookingRule(
                campushallen_account_id=other.id,
                name="Target",
                activity="Ball",
                weekday=utcnow().weekday(),
            )
        )
        db.commit()
    engine = BookingEngine(b.sessions, b.gateway)
    asyncio.run(Scheduler(engine).once())
    assert b.server.login_calls == 1
    with b.sessions() as db:
        assert db.get(CampushallenAccount, b.account_id).auth_error_code == "REAUTH_SUCCESS"
    # Now one account fails authentication while the other still discovers.
    expire(b)
    b.server.login_status = 401
    asyncio.run(Scheduler(engine).once())
    with b.sessions() as db:
        assert db.get(CampushallenAccount, b.account_id).requires_reconnect
        assert db.get(CampushallenAccount, other_id).last_discovery_at
        results = db.execute(
            select(BookingAttempt.campushallen_account_id, BookingAttempt.result)
        ).all()
        assert (b.account_id, "AUTH_FAILED") in results and (
            other_id,
            "TARGET_NOT_FOUND",
        ) in results


def test_notifications_are_safe_and_provider_failure_cannot_undo_booking(booking):
    b = booking
    events = []

    class Recorder:
        def notify(self, event):
            events.append(event)
            raise RuntimeError("provider unavailable")

    b.engine.notifier = Recorder()
    assert b.engine.run(b.plan, dry_run=False).result == "BOOKED"
    assert len(events) == 1 and events[0].kind == NotificationKind.BOOKING_CONFIRMED
    assert set(vars(events[0])) == {"kind", "user_id", "account_id", "attempt_id"}


def test_dashboard_history_filters_and_activity_rule_prefill(web):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    assert post(client, "/rules", rule_form(account_id)).status_code == 303
    from types import SimpleNamespace
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Europe/Stockholm")
    start = (utcnow() + timedelta(days=8)).astimezone(tz).replace(hour=12, minute=0)
    with app.state.sessions() as db:
        rule = db.scalar(
            select(BookingRule).where(BookingRule.campushallen_account_id == account_id)
        )
        account = db.get(CampushallenAccount, account_id)
        account.requires_reconnect = True
        slot = SimpleNamespace(
            id=101,
            name="Daytime ball",
            product_name="Ball 85",
            location_names=("Vallastaden",),
            business_unit_name="Campushallen",
            starts_at=start,
            bookable_earliest=start - timedelta(days=7),
            left_to_book=0,
            has_waiting_list=True,
        )
        snapshot(db, rule, slot, already_booked=True)
        db.add(
            BookingAttempt(
                campushallen_account_id=account_id,
                booking_rule_id=rule.id,
                activity_name="Daytime ball",
                activity_start=start,
                result="ALREADY_BOOKED",
                message="Independent check",
            )
        )
        db.commit()
        opportunity_id = db.scalar(select(BookingOpportunity.id))
    response = client.get("/")
    for label in (
        "Reconnection required",
        "Auto reconnect",
        "Daytime ball",
        "Booking opens",
        "Already booked",
        "Full",
        "Waitlist available",
    ):
        assert label in response.text
    assert client.get("/onboarding").status_code == 200
    response = client.get(
        f"/history?account_id={account_id}&result=ALREADY_BOOKED&date={start.date()}"
    )
    assert (
        response.status_code == 200
        and "Daytime ball" in response.text
        and "Ball Tuesday" in response.text
    )
    assert "Confirmed by independent" in response.text
    assert "Daytime ball" not in client.get("/history?result=BOOKED").text
    assert client.get("/history?account_id=&result=&date=").status_code == 200
    assert client.get("/history?date=bad-date").status_code == 422
    assert client.get(f"/history?account_id={app.state.bob_account}").status_code == 404
    assert client.get("/history?result=UNKNOWN").status_code == 422
    response = client.get(f"/rules/from-activity/{opportunity_id}")
    assert response.status_code == 200 and 'value="12:00"' in response.text
    assert 'name="activity_id"' not in response.text
    assert 'name="product_ids" value=""' in response.text
    assert client.get("/rules/from-activity/999999").status_code == 404
