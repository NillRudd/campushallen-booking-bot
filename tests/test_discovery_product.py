import copy
import re
from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from test_web import login, own_account, post
from test_web import web as web

from campushallen.api import CampushallenHttpClient
from campushallen.database.models import (
    BookingAttempt,
    BookingOpportunity,
    BookingRule,
    CampushallenAccount,
)
from campushallen.database.session import make_engine
from campushallen.services.discovery import selection_key
from campushallen.services.scheduler import Scheduler

TZ = ZoneInfo("Europe/Stockholm")


@pytest.fixture
def schedule(web, monkeypatch):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    with app.state.sessions() as db:
        account = db.get(CampushallenAccount, account_id)
        account.auth_state = app.state.gateway.vault.seal(
            {"access_token": "synthetic-token", "customer_id": 100}
        )
        account.auth_status = "connected"
        db.commit()
    base = (datetime.now(TZ) + timedelta(days=7)).date()
    monday = base - timedelta(days=base.weekday())

    def activity(
        identifier, weekday, hour, name="Innebandy 85", location="Vallastaden", available=15
    ):
        start = datetime.combine(monday + timedelta(days=weekday), datetime.min.time(), TZ).replace(
            hour=hour, minute=45 if hour == 18 else 0
        )
        return {
            "id": identifier,
            "name": name + " " + location,
            "groupActivityProduct": {"id": 85 if name == "Innebandy 85" else 99, "name": name},
            "businessUnit": {"id": 1, "name": "Campushallen"},
            "locations": [{"name": location}],
            "duration": {
                "start": start.isoformat(),
                "end": (start + timedelta(minutes=85)).isoformat(),
            },
            "bookableEarliest": (start - timedelta(days=7)).isoformat(),
            "slots": {
                "total": 15,
                "totalBookable": 15,
                "leftToBook": available,
                "hasWaitingList": True,
                "inWaitingList": 2 if not available else 0,
            },
        }

    data = [
        activity(1001, 1, 18),
        activity(1002, 3, 18),
        activity(1003, 6, 12, available=0),
        activity(1004, 2, 17, "Yoga", "Cityhallen"),
    ]
    state = SimpleNamespace(data=data, calls=[], failure=None, activity=activity, first=monday)

    def respond(request):
        assert request.method == "GET", "Discovery/preview must never send a mutation"
        state.calls.append(request)
        if state.failure and (state.failure == 401 or "/apps/" in request.url.path):
            return httpx.Response(state.failure, json={"error": "synthetic"})
        if request.url.path.endswith("/bookings/groupactivities"):
            return httpx.Response(200, json=[{"groupActivity": {"id": 1002}}])
        if "/apps/" in request.url.path:
            if getattr(state, "malformed", False):
                return httpx.Response(200, json={"unexpected": "shape"})
            start = datetime.fromisoformat(
                request.url.params["period.start"].replace("Z", "+00:00")
            )
            end = datetime.fromisoformat(request.url.params["period.end"].replace("Z", "+00:00"))
            # Production accepts weekly reads but rejects a single 21-day period.
            if (end.astimezone(TZ) - start.astimezone(TZ)) >= timedelta(days=7):
                return httpx.Response(403, json={"error": "synthetic-period-limit"})
            return httpx.Response(
                200,
                json=[
                    item
                    for item in state.data
                    if datetime.fromisoformat(item["duration"]["start"]).tzinfo is None
                    or start <= datetime.fromisoformat(item["duration"]["start"]) <= end
                ],
            )
        return httpx.Response(200, json={"id": 100})

    original = CampushallenHttpClient.__init__

    def init(self, config, **kwargs):
        original(self, config, **kwargs)
        self.client.close()
        self.client = httpx.Client(
            base_url="https://api.test", transport=httpx.MockTransport(respond)
        )

    monkeypatch.setattr(CampushallenHttpClient, "__init__", init)
    state.client, state.app, state.account_id = client, app, account_id
    return state


def selection_form(s, identifiers):
    page = s.client.get("/activities", params={"start": s.first.isoformat(), "days": 14})
    match = re.search(r'name="selection_token" value="([^"]+)"', page.text)
    assert match
    return {
        "activity_ids": [str(i) for i in identifiers],
        "start": s.first.isoformat(),
        "days": "14",
        "selection_token": match.group(1),
    }


def select_sessions(s, identifiers):
    return post(
        s.client,
        "/activities/select",
        selection_form(s, identifiers),
        "/activities?start=" + s.first.isoformat(),
    )


def test_one_account_creation_and_email_ui(web):
    client, app = web
    login(client)
    page = client.get("/accounts")
    assert 'type="email"' in page.text and "E-postadress hos Campushallen" in page.text
    assert "Användarnamn hos Campushallen" not in page.text
    assert (
        post(
            client, "/accounts", {"display_name": "Training", "username": "missing-at"}
        ).status_code
        == 422
    )
    account_id = own_account(client, app)
    assert (
        post(
            client, "/accounts", {"display_name": "Other", "username": "second@example.com"}
        ).status_code
        == 409
    )
    page = client.get("/accounts")
    assert page.url.path == f"/accounts/{account_id}"
    assert "E-postadress hos Campushallen" in page.text and 'type="email"' in page.text
    assert "Lägg till konto" not in page.text
    with app.state.sessions() as db:
        db.add(
            CampushallenAccount(
                user_id=app.state.alice_id, display_name="Duplicate", username="other@example.com"
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()
        assert db.get(CampushallenAccount, app.state.bob_account)


def test_duplicate_migration_stops_before_ddl_and_keeps_all_data(tmp_path):
    url = f"sqlite:///{tmp_path}/duplicates.db"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "d8426c9ef017")
    engine = make_engine(url)
    with engine.begin() as db:
        db.execute(
            text(
                "INSERT INTO users (id,username,password_hash,is_active,created_at,updated_at) VALUES (1,'owner','unused',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            )
        )
        for identifier in (1, 2):
            db.execute(
                text(
                    "INSERT INTO campushallen_accounts (id,user_id,display_name,username,auth_status,enabled,created_at,updated_at) VALUES (:id,1,'Account',:email,'connected',1,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
                ),
                {"id": identifier, "email": f"user{identifier}@example.com"},
            )
    with pytest.raises(RuntimeError, match="No accounts have been deleted"):
        command.upgrade(config, "head")
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM campushallen_accounts")).scalar() == 2
        assert (
            db.execute(text("SELECT version_num FROM alembic_version")).scalar() == "d8426c9ef017"
        )
    engine.dispose()


@pytest.mark.parametrize("query", ["innebandy", "Innebandy", "INNEBANDY", "bandy"])
def test_partial_case_insensitive_search_without_rule(schedule, query):
    s = schedule
    response = s.client.get("/activities", params={"q": query, "start": s.first.isoformat()})
    assert response.status_code == 200
    assert "Innebandy 85" in response.text and "Vallastaden" in response.text
    assert "Yoga" not in response.text
    assert "1001" in response.text and "15 / 15" in response.text
    with s.app.state.sessions() as db:
        assert not db.scalars(
            select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
        ).all()


def test_search_facility_dates_and_short_lived_cache(schedule):
    s = schedule
    url = "/activities?start=" + s.first.isoformat()
    assert "Yoga" in s.client.get(url).text
    assert "Innebandy" not in s.client.get(url + "&facility=Cityhallen").text
    assert "Yoga" not in s.client.get(url + "&facility=Vallastaden").text
    assert len([r for r in s.calls if "/apps/" in r.url.path]) == 1
    assert "Inga pass hittades" in s.client.get(url + "&q=unknown").text
    assert "Inga pass hittades" in s.client.get(url + "&days=1").text
    assert s.client.get(url + "&days=22").status_code == 422
    s.app.state.discovery.ttl = 0
    s.client.get(url)
    assert len([r for r in s.calls if "/apps/" in r.url.path]) == 3
    assert all(
        "synthetic-token" not in str(value) for _, value in s.app.state.discovery.cache.values()
    )


def test_21_day_search_uses_cached_weekly_reads_with_api_period_limit(schedule):
    s = schedule
    today = datetime.now(TZ).date()
    # Include an occurrence in every chunk, including across Stockholm's DST change.
    for week in (1, 2):
        item = copy.deepcopy(s.data[0])
        item["id"] += week * 100
        for field in ("start", "end"):
            stamp = datetime.fromisoformat(item["duration"][field]).astimezone(TZ)
            item["duration"][field] = (stamp + timedelta(days=week * 7)).isoformat()
        s.data.append(item)
    url = f"/activities?start={today.isoformat()}&days=21&q=innebandy"
    response = s.client.get(url)
    assert response.status_code == 200
    assert "Innebandy 85" in response.text
    assert "Anslut eller aktivera" not in response.text
    for item in s.data:
        stamp = datetime.fromisoformat(item["duration"]["start"]).astimezone(TZ)
        if today <= stamp.date() < today + timedelta(days=21) and "Innebandy" in item["name"]:
            assert f'value="{item["id"]}"' in response.text
    calls = [r for r in s.calls if "/apps/" in r.url.path]
    assert len(calls) == 3
    for left, right in zip(calls, calls[1:]):
        end = datetime.fromisoformat(left.url.params["period.end"].replace("Z", "+00:00"))
        start = datetime.fromisoformat(right.url.params["period.start"].replace("Z", "+00:00"))
        assert start - end == timedelta(milliseconds=1)
    assert "Innebandy 85" in s.client.get(url + "&facility=Vallastaden").text
    assert len([r for r in s.calls if "/apps/" in r.url.path]) == 3


@pytest.mark.parametrize("failure", [401, 503])
def test_auth_and_api_failure_have_safe_useful_states(schedule, failure):
    s = schedule
    s.failure = failure
    response = s.client.get("/activities?start=" + s.first.isoformat())
    assert response.status_code == 200
    assert (
        ("anslut" in response.text.lower())
        if failure == 401
        else ("Schemat kunde inte hämtas" in response.text)
    )
    assert "synthetic-token" not in response.text


def test_multi_day_creation_stable_identity_and_duplicate_protection(schedule):
    s = schedule
    response = select_sessions(s, [1001, 1002, 1003])
    assert response.status_code == 200
    with s.app.state.sessions() as db:
        rules = db.scalars(
            select(BookingRule)
            .where(BookingRule.campushallen_account_id == s.account_id)
            .order_by(BookingRule.weekday)
        ).all()
        assert [(r.weekday, str(r.start_time)) for r in rules] == [
            (1, "18:45:00"),
            (3, "18:45:00"),
            (6, "12:00:00"),
        ]
        assert all(
            r.product_ids == [85]
            and r.facility_contains == ["Vallastaden"]
            and not r.allow_waitlist
            for r in rules
        )
        assert not hasattr(rules[0], "activity_id")
        db.add(
            BookingRule(
                campushallen_account_id=s.account_id,
                name="Another name",
                activity="Ball",
                weekday=1,
                selection_key=rules[0].selection_key,
            )
        )
        with pytest.raises(IntegrityError):
            db.commit()
    repeated = select_sessions(s, [1001, 1002, 1003])
    assert "Du har redan en bokningsregel för detta pass" in repeated.text
    with s.app.state.sessions() as db:
        assert (
            len(
                db.scalars(
                    select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
                ).all()
            )
            == 3
        )
        account = db.get(CampushallenAccount, s.account_id)
        occurrence = s.app.state.discovery.upcoming(account, s.first, 14)[0]
        assert selection_key(occurrence.rule_input()) == selection_key(
            replace(occurrence, activity_id=99999).rule_input()
        )


def test_weekly_duplicates_in_one_submission_are_one_rule(schedule):
    s = schedule
    later = copy.deepcopy(s.data[0])
    later["id"] = 2001
    for field in ("start", "end"):
        later["duration"][field] = (
            datetime.fromisoformat(later["duration"][field]) + timedelta(days=7)
        ).isoformat()
    s.data.append(later)
    assert select_sessions(s, [1001, 2001]).status_code == 200
    with s.app.state.sessions() as db:
        assert (
            len(
                db.scalars(
                    select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
                ).all()
            )
            == 1
        )


def test_preview_worker_target_equality_and_full_waitlist_booked_metadata(schedule):
    s = schedule
    select_sessions(s, [1001, 1002, 1003])
    with s.app.state.sessions() as db:
        rules = db.scalars(
            select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
        ).all()
    scheduler = Scheduler(s.app.state.engine)
    for rule in rules:
        response = post(s.client, f"/rules/{rule.id}/preview", token_path="/rules")
        assert response.status_code == 200 and "Matchande kommande pass" in response.text
        assert "Vallastaden" in response.text and "Bokningen öppnar" in response.text
        plans = s.app.state.engine.discover(s.account_id, rule_id=rule.id)
        assert len([plan for plan in plans if plan.slot]) == 1
        assert not scheduler.already_processed(plans[0]), (
            "A preview must not suppress worker processing"
        )
        if rule.weekday == 6:
            assert "0 / 15" in response.text and "Reservlista: 2" in response.text
        if rule.weekday == 3:
            assert "Redan bokad" in response.text
        with s.app.state.sessions() as db:
            snapshot = db.scalar(
                select(BookingOpportunity).where(BookingOpportunity.booking_rule_id == rule.id)
            )
            assert snapshot.activity_id == plans[0].slot.id
            assert snapshot.bookable_earliest == plans[0].slot.bookable_earliest
    assert all(request.method == "GET" for request in s.calls)
    with s.app.state.sessions() as db:
        assert all(
            row.is_preview
            for row in db.scalars(
                select(BookingAttempt).where(BookingAttempt.campushallen_account_id == s.account_id)
            )
        )


def test_preview_clears_stale_matches_and_gives_guidance(schedule):
    s = schedule
    select_sessions(s, [1001])
    with s.app.state.sessions() as db:
        rule_id = db.scalar(
            select(BookingRule.id).where(BookingRule.campushallen_account_id == s.account_id)
        )
    assert "Vallastaden" in post(s.client, f"/rules/{rule_id}/preview", token_path="/rules").text
    s.data.clear()
    response = post(s.client, f"/rules/{rule_id}/preview", token_path="/rules")
    assert "Kontrollera veckodag, tid och anläggning" in response.text
    with s.app.state.sessions() as db:
        assert not db.scalar(
            select(BookingOpportunity).where(BookingOpportunity.booking_rule_id == rule_id)
        )


def test_ownership_csrf_missing_connection_and_invalid_selection(schedule):
    s = schedule
    assert s.client.post("/activities/select", data={"activity_ids": "1001"}).status_code == 403
    assert (
        post(s.client, "/activities/select", {"activity_ids": "999999"}, "/activities").status_code
        == 409
    )
    assert (
        post(s.client, f"/rules/{s.app.state.bob_rule}/preview", token_path="/rules").status_code
        == 404
    )
    with s.app.state.sessions() as db:
        account = db.get(CampushallenAccount, s.account_id)
        account.enabled = False
        db.commit()
    count = len(s.calls)
    assert "aktivera" in s.client.get("/activities").text
    assert len(s.calls) == count


def test_discovery_without_connection_never_uses_another_tenant(web):
    client, app = web
    login(client)
    response = client.get("/activities", params={"account_id": app.state.bob_account})
    assert response.status_code == 200 and "Anslut ditt Campushallen-konto" in response.text
    assert "Bob private" not in response.text
    assert (
        post(client, "/activities/select", {"activity_ids": "1001"}, "/activities").status_code
        == 409
    )


@pytest.mark.parametrize("change", ["ambiguous", "facility", "time", "weekday", "opening"])
def test_realistic_rule_rejects_ambiguous_or_mismatched_activity(schedule, change):
    s = schedule
    select_sessions(s, [1001])
    with s.app.state.sessions() as db:
        rule = db.scalar(
            select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
        )
        rule_id = rule.id
        if change == "facility":
            rule.facility_contains = ["Wrong hall"]
        elif change == "time":
            rule.start_time = rule.preferred_start_time.replace(hour=19)
        elif change == "weekday":
            rule.weekday = 0
        db.commit()
    if change == "ambiguous":
        duplicate = copy.deepcopy(s.data[0])
        duplicate["id"] = 12345
        s.data.append(duplicate)
    elif change == "opening":
        s.data[0].pop("bookableEarliest")
    response = post(s.client, f"/rules/{rule_id}/preview", token_path="/rules")
    assert (
        response.status_code == 200 and "Kontrollera veckodag, tid och anläggning" in response.text
    )
    assert "Flera möjliga pass" in response.text if change == "ambiguous" else True
    assert all(request.method == "GET" for request in s.calls)
    with s.app.state.sessions() as db:
        assert not db.scalar(
            select(BookingOpportunity).where(BookingOpportunity.booking_rule_id == rule_id)
        )


def test_normalization_stockholm_dst_and_rejects_naive_api_timestamps(schedule):
    s = schedule
    with s.app.state.sessions() as db:
        account = db.get(CampushallenAccount, s.account_id)
        occurrences = s.app.state.discovery.upcoming(account, s.first, 14)
    tuesday = next(item for item in occurrences if item.activity_id == 1001)
    assert tuesday.start.weekday() == 1 and (tuesday.start.hour, tuesday.start.minute) == (18, 45)
    assert tuesday.start.tzinfo == TZ
    assert tuesday.bookable_earliest == datetime.fromisoformat(s.data[0]["bookableEarliest"])
    assert tuesday.rule_input().start_time.hour == 18
    s.data[0]["duration"]["start"] = "2026-10-27T18:45:00"
    s.app.state.discovery.ttl = 0
    with s.app.state.sessions() as db:
        values = s.app.state.discovery.upcoming(
            db.get(CampushallenAccount, s.account_id), s.first, 14
        )
    assert 1001 not in {item.activity_id for item in values}


def test_existing_manual_rule_is_offered_instead_of_duplicate(schedule):
    s = schedule
    with s.app.state.sessions() as db:
        db.add(
            BookingRule(
                campushallen_account_id=s.account_id,
                name="Existing manual rule",
                activity="Innebandy",
                weekday=1,
                preferred_start_time=datetime.min.time().replace(hour=18, minute=45),
                earliest_allowed_time=datetime.min.time().replace(hour=17),
                latest_allowed_time=datetime.min.time().replace(hour=20),
                facility_contains=["Campushallen"],
            )
        )
        db.commit()
    response = select_sessions(s, [1001])
    assert (
        "Du har redan en bokningsregel" in response.text and "Existing manual rule" in response.text
    )
    with s.app.state.sessions() as db:
        assert (
            len(
                db.scalars(
                    select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
                ).all()
            )
            == 1
        )


def test_unique_account_migration_preserves_children_and_can_downgrade(tmp_path):
    from sqlalchemy import MetaData, Table
    from sqlalchemy.orm import Session

    from campushallen.database.models import CampushallenCredential, User, utcnow
    from campushallen.services.rules import RuleInput

    url = f"sqlite:///{tmp_path}/preserved.db"
    config = Config("alembic.ini")
    config.attributes["database_url"] = url
    command.upgrade(config, "d8426c9ef017")
    engine = make_engine(url)
    metadata = MetaData()
    accounts = Table("campushallen_accounts", metadata, autoload_with=engine)
    with Session(engine) as db:
        user = User(username="preserved", password_hash="synthetic")
        db.add(user)
        db.flush()
        account_id = db.execute(
            accounts.insert().values(
                user_id=user.id,
                username="user@example.com",
                display_name="One",
                auth_state="synthetic-ciphertext",
                enabled=True,
                auth_status="disconnected",
                created_at=utcnow(),
                updated_at=utcnow(),
            )
        ).inserted_primary_key[0]
        db.add(
            CampushallenCredential(
                campushallen_account_id=account_id,
                encrypted_password="synthetic-encrypted-password",
            )
        )
        db.commit()
    rules = Table("booking_rules", metadata, autoload_with=engine)
    attempts = Table("booking_attempts", metadata, autoload_with=engine)
    with engine.begin() as db:
        values = RuleInput(name="Preserved", activity="Innebandy", weekday=1).model_dump()
        db.execute(
            rules.insert().values(
                id=1, campushallen_account_id=1, created_at=utcnow(), updated_at=utcnow(), **values
            )
        )
        db.execute(
            attempts.insert().values(
                campushallen_account_id=1,
                booking_rule_id=1,
                result="DRY_RUN",
                message="Synthetic",
                attempted_at=utcnow(),
                created_at=utcnow(),
            )
        )
    command.upgrade(config, "head")
    command.check(config)
    with engine.connect() as db:
        assert (
            db.execute(text("SELECT auth_state FROM campushallen_accounts")).scalar()
            == "synthetic-ciphertext"
        )
        assert (
            db.execute(text("SELECT encrypted_password FROM campushallen_credentials")).scalar()
            == "synthetic-encrypted-password"
        )
        assert db.execute(text("SELECT count(*) FROM booking_rules")).scalar() == 1
        assert db.execute(text("SELECT is_preview FROM booking_attempts")).scalar() is None
    command.downgrade(config, "d8426c9ef017")
    with engine.connect() as db:
        assert db.execute(text("SELECT count(*) FROM booking_attempts")).scalar() == 1
        assert db.execute(text("SELECT count(*) FROM campushallen_credentials")).scalar() == 1
    command.upgrade(config, "head")
    engine.dispose()


def test_api_utc_times_across_stockholm_clock_change(schedule, monkeypatch):
    s = schedule

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 20, 12, tzinfo=TZ).astimezone(tz)

    monkeypatch.setattr("campushallen.services.discovery.datetime", FrozenDateTime)
    item = copy.deepcopy(s.data[0])
    item["duration"] = {"start": "2026-10-27T17:45:00Z", "end": "2026-10-27T19:10:00Z"}
    item["bookableEarliest"] = "2026-10-20T16:45:00Z"
    s.data[:] = [item]
    with s.app.state.sessions() as db:
        values = s.app.state.discovery.upcoming(
            db.get(CampushallenAccount, s.account_id), datetime(2026, 10, 20).date(), 14
        )
    assert len(values) == 1
    assert values[0].start.isoformat() == "2026-10-27T18:45:00+01:00"
    assert values[0].bookable_earliest.isoformat() == "2026-10-20T18:45:00+02:00"


def test_malformed_schedule_response_is_api_error_not_date_error(schedule):
    s = schedule
    s.malformed = True
    response = s.client.get("/activities")
    assert response.status_code == 200
    assert "Schemat kunde inte hämtas" in response.text
    assert "unexpected" not in response.text


def test_email_correction_validates_form_before_discarding_current_state(schedule, monkeypatch):
    s = schedule
    with s.app.state.sessions() as db:
        original = db.get(CampushallenAccount, s.account_id).auth_state
    invalid = post(
        s.client,
        f"/accounts/{s.account_id}/connect",
        {"username": "new+alias@example.com", "password": ""},
        f"/accounts/{s.account_id}",
    )
    assert invalid.status_code == 422
    with s.app.state.sessions() as db:
        assert db.get(CampushallenAccount, s.account_id).auth_state == original
        assert db.get(CampushallenAccount, s.account_id).username == "alice@example.com"

    def connect(account, password):
        assert account.username == "new+alias@example.com"
        assert password == "synthetic-password"
        account.auth_state = s.app.state.gateway.vault.seal(
            {"access_token": "synthetic-new-token", "customer_id": 100}
        )
        account.auth_status = "connected"
        account.requires_reconnect = False

    monkeypatch.setattr(s.app.state.gateway, "connect", connect)
    success = post(
        s.client,
        f"/accounts/{s.account_id}/connect",
        {"username": "new+alias@example.com", "password": "synthetic-password"},
        f"/accounts/{s.account_id}",
    )
    assert success.status_code == 303
    with s.app.state.sessions() as db:
        assert db.get(CampushallenAccount, s.account_id).username == "new+alias@example.com"


@pytest.mark.parametrize("change", ["time", "facility", "product", "tamper"])
def test_changed_session_or_tampered_selection_cannot_create_unintended_rule(schedule, change):
    s = schedule
    form = selection_form(s, [1001])
    if change == "time":
        value = datetime.fromisoformat(s.data[0]["duration"]["start"]) + timedelta(hours=1)
        s.data[0]["duration"]["start"] = value.isoformat()
    elif change == "facility":
        s.data[0]["locations"] = [{"name": "Other facility"}]
    elif change == "product":
        s.data[0]["groupActivityProduct"] = {"id": 99, "name": "Yoga"}
    else:
        form["selection_token"] += "invalid"
    s.app.state.discovery.ttl = 0
    response = post(s.client, "/activities/select", form, "/activities")
    assert response.status_code == 409
    with s.app.state.sessions() as db:
        assert not db.scalar(
            select(BookingRule).where(BookingRule.campushallen_account_id == s.account_id)
        )
