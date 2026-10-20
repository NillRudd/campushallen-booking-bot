"""Website localization at the HTTP boundary, without changing domain data."""

from datetime import UTC, datetime

import pytest
from babel.messages.pofile import read_po
from bs4 import BeautifulSoup
from pydantic import ValidationError
from sqlalchemy import select
from test_web import login, own_account, post, rule_form
from test_web import web as web

from campushallen.database.models import (
    BookingAttempt,
    BookingRule,
    CampushallenAccount,
    Outcome,
    User,
)
from campushallen.services.audit import Event
from campushallen.services.invitations import create_invite
from campushallen.settings import Settings
from campushallen.web.i18n import CATALOGS, local_datetime, translations, weekdays
from campushallen.web.presentation import (
    EVENT_LABELS,
    OUTCOME_LABELS,
    connection_status,
    friendly_result,
    safe_error,
)


def visible(response):
    return BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)


def test_default_locale_and_no_browser_language_negotiation(web):
    client, app = web
    assert Settings(_env_file=None).default_locale == "sv_SE"
    assert app.state.settings.default_locale == "sv_SE"
    response = client.get("/login", headers={"Accept-Language": "en-US"})
    assert '<html lang="sv">' in response.text
    assert response.headers["Content-Language"] == "sv-SE"
    assert "Logga in" in visible(response)
    assert "Användarnamn" in visible(response)
    assert "Log in" not in visible(response)
    with pytest.raises(ValidationError):
        Settings(default_locale="en_US", _env_file=None)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("/", "Din vecka på Campushallen"),
        ("/onboarding", "Välj vilka pass du vill boka"),
        ("/accounts", "Mitt Campushallen-konto"),
        ("/rules", "Bokningsregler"),
        ("/rules/new", "Önskad starttid"),
        ("/history", "Bokningshistorik"),
        ("/settings", "Säkerhet och sessioner"),
    ],
)
def test_major_pages_are_swedish(web, path, expected):
    client, app = web
    login(client)
    own_account(client, app)
    response = client.get(path)
    assert response.status_code == 200
    assert expected in visible(response)
    assert "Översikt" in visible(response)
    assert "Bob private" not in visible(response)
    assert "Overview" not in visible(response)


def test_account_presentation_and_notices(web):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    page = client.get(f"/accounts/{account_id}?notice=forgotten")
    for phrase in ("Automatisk återanslutning", "Inte ansluten", "Ta bort", "har tagits bort"):
        assert phrase in visible(page)
    assert "token" not in visible(page).lower()
    assert "worker" not in visible(page).lower()
    assert "DISCONNECT" not in visible(page)  # Checkbox value stays machine-readable.
    assert (
        not BeautifulSoup(page.text, "html.parser")
        .select_one('[name="remember"]')
        .has_attr("checked")
    )


@pytest.mark.parametrize(
    ("status", "label"),
    [
        ("connected", "Ansluten"),
        ("disconnected", "Inte ansluten"),
        ("authentication_error", "Inloggningen misslyckades"),
        ("reauth_failed", "Inloggningen misslyckades"),
        ("reconnect_required", "Återanslutning krävs"),
        ("token_expired", "Återansluter"),
    ],
)
def test_authentication_states_are_presentation_only(status, label):
    account = CampushallenAccount(
        user_id=1,
        username="external",
        display_name="External",
        enabled=True,
        auth_status=status,
        auth_state="encrypted" if status == "connected" else None,
    )
    assert label in connection_status(account)
    assert account.auth_status == status
    account.enabled = False
    assert connection_status(account) == "Inaktiv"


def test_swedish_rule_crud_preserves_machine_fields(web):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    assert post(client, "/rules", rule_form(account_id)).status_code == 303
    with app.state.sessions() as db:
        rule = db.scalar(
            select(BookingRule).where(BookingRule.campushallen_account_id == account_id)
        )
        rule_id = rule.id
        assert rule.weekday == 1 and rule.booking_type == "group_activity"
    page = client.get(f"/rules/{rule_id}/edit")
    text = visible(page)
    assert "Redigera bokningsregel" in text and "Tisdag" in text
    assert "Avancerade inställningar" in text
    assert (
        post(
            client, f"/rules/{rule_id}", {**rule_form(account_id), "name": "Kvällspass"}
        ).status_code
        == 303
    )
    assert "Kvällspass" in visible(client.get("/rules"))
    assert post(client, f"/rules/{rule_id}/delete").status_code == 303
    assert "Kvällspass" not in visible(client.get("/rules"))


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"weekday": "8"}, "Välj en veckodag"),
        ({"latest_allowed_time": "16:00"}, "tidigaste starttiden"),
        ({"product_ids": "not-a-number"}, "numeriska ID:n"),
        ({"preferred_start_time": "nonsense"}, "giltiga lokala tider"),
        ({"preferred_start_time": "18:45+02:00"}, "lokal tid utan tidszon"),
    ],
)
def test_validation_has_swedish_guidance_without_pydantic_details(web, change, expected):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    response = post(client, "/rules", {**rule_form(account_id), **change})
    assert response.status_code == 422
    assert expected in visible(response)
    for jargon in ("ValueError", "validation error", "Input should", "pydantic"):
        assert jargon not in visible(response)


def test_invites_registration_and_password_validation(web):
    client, app = web
    with app.state.sessions() as db:
        _, token = create_invite(db, label="Vän")
        db.commit()
    path = f"/invite/{token}"
    assert "Du har blivit inbjuden" in visible(client.get(path))
    response = post(
        client, path, {"username": "friend", "password": "short", "confirmation": "short"}, path
    )
    assert response.status_code == 422 and "minst 12" in visible(response)
    response = post(
        client,
        path,
        {"username": "friend", "password": "long-enough-password", "confirmation": "different"},
        path,
    )
    assert "stämmer inte överens" in visible(response)
    response = post(
        client,
        path,
        {
            "username": "alice",
            "password": "long-enough-password",
            "confirmation": "long-enough-password",
        },
        path,
    )
    assert "Användarnamnet är redan upptaget" in visible(response)
    assert "secret" not in safe_error("secret unexpected exception")


def test_swedish_history_keeps_english_outcome_and_reason_in_database(web):
    client, app = web
    login(client)
    account_id = own_account(client, app)
    with app.state.sessions() as db:
        db.add(
            BookingAttempt(
                campushallen_account_id=account_id,
                result="DRY_RUN",
                message="Matching activity discovered.",
                activity_name="Innebandy 85",
            )
        )
        db.commit()
    page = client.get("/history?result=DRY_RUN")
    assert "Testkörning – ingen bokning gjordes" in visible(page)
    assert "Ett matchande pass hittades" in visible(page)
    assert "DRY_RUN" not in visible(page)
    assert 'value="DRY_RUN"' in page.text
    with app.state.sessions() as db:
        attempt = db.scalar(
            select(BookingAttempt).where(BookingAttempt.campushallen_account_id == account_id)
        )
        assert attempt.result == "DRY_RUN" and attempt.message == "Matching activity discovered."
    assert set(OUTCOME_LABELS) == {outcome.value for outcome in Outcome}
    assert friendly_result("BOOKED") == "Bokad"
    assert friendly_result("ALREADY_BOOKED") == "Redan bokad"
    assert all(friendly_result(outcome.value) != outcome.value for outcome in Outcome)
    assert Outcome.DRY_RUN.value == "DRY_RUN"


def test_admin_is_swedish_and_codes_are_secondary(web):
    client, app = web
    login(client)
    with app.state.sessions() as db:
        db.get(User, app.state.alice_id).is_admin = True
        db.commit()
    text = visible(client.get("/admin"))
    assert "Administration" in text and "Skapa inbjudan" in text
    assert "Bokningsmotorn körs inte" in text
    assert "Inloggning lyckades" in text and "Kod: LOGIN_SUCCESS" in text
    assert set(EVENT_LABELS) == {event.value for event in Event}
    response = post(client, "/admin/invites", {"count": "1", "expires_days": "7"})
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.select_one("[data-copy]").get_text() == "Kopiera länk"
    assert soup.select_one("[readonly]")["aria-label"] == "Länk till inbjudan 1"


def test_locale_dates_use_stockholm_without_changing_utc_input():
    value = datetime(2026, 10, 20, 16, 45, tzinfo=UTC)
    display = local_datetime(value, "Europe/Stockholm", "sv_SE")
    assert "tisdag" in display and "20 okt. 2026" in display and "18:45" in display
    winter = local_datetime(datetime(2026, 12, 1, 17, 45, tzinfo=UTC), "Europe/Stockholm", "sv_SE")
    assert "dec." in winter and "18:45" in winter
    assert value.hour == 16 and value.tzinfo == UTC
    assert weekdays("sv_SE") == [
        "Måndag",
        "Tisdag",
        "Onsdag",
        "Torsdag",
        "Fredag",
        "Lördag",
        "Söndag",
    ]


def test_catalog_complete_compiled_and_plural_forms_match():
    with (CATALOGS / "sv_SE/LC_MESSAGES/messages.po").open() as source:
        catalog = read_po(source)
    compiled = translations("sv_SE")
    assert not catalog.fuzzy
    for message in catalog:
        if not message.id:
            continue
        assert message.string and "fuzzy" not in message.flags, message.id
        if isinstance(message.id, tuple):
            assert all(message.string)
            for number, expected in zip((1, 2), message.string, strict=True):
                assert compiled.ngettext(*message.id, number) == expected
        else:
            assert compiled.gettext(message.id) == message.string
    assert (
        compiled.ngettext("%(num)s available spot", "%(num)s available spots", 1)
        == "%(num)s ledig plats"
    )
    assert (
        compiled.ngettext("%(num)s available spot", "%(num)s available spots", 2)
        == "%(num)s lediga platser"
    )


def test_error_boundaries_are_swedish_and_keep_security_controls(web):
    client, app = web
    bad_login = post(client, "/login", {"username": "alice", "password": "wrong"}, "/login")
    assert bad_login.status_code == 400 and "Felaktigt användarnamn" in visible(bad_login)
    csrf_error = client.post("/login", data={"username": "alice"})
    assert csrf_error.status_code == 403 and "Ladda om sidan" in visible(csrf_error)
    login(client)
    foreign = client.get(f"/accounts/{app.state.bob_account}")
    assert foreign.status_code == 404 and "Kontot hittades inte" in visible(foreign)
    assert "Bob private" not in visible(foreign)
    unknown = client.get("/missing-page")
    assert unknown.status_code == 404 and "Sidan hittades inte" in visible(unknown)
    malformed = client.get("/history?page=invalid")
    assert malformed.status_code == 422 and "Kontrollera formuläret" in visible(malformed)
    oversized = client.post("/login", content=b"x" * 262145)
    assert oversized.status_code == 413 and "Formuläret är för stort" in oversized.text
