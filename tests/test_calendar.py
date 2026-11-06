"""`calendar` against a fake Exchange account: shapes, filters, errors, read-only."""
from __future__ import annotations

import datetime
import importlib
import json
from types import SimpleNamespace as NS

import pytest
from exchangelib import errors as ews
from starlette.testclient import TestClient

from motor.app import create_app
from motor.config import Config
from motor.providers.motor import motor

cal = importlib.import_module("motor.providers.calendar")

MSK = cal.TZ
UTC = datetime.UTC
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def box(email, name=""):
    return NS(email_address=email, name=name)


def item(subject, start, end, *, attendees=(), optional=(), all_day=False, cancelled=False, **extra):
    fields = dict(
        id="AAMk" + subject, uid="uid-" + subject, recurrence_id=None, subject=subject, start=start, end=end,
        is_all_day=all_day, location="Переговорка «Нева»", organizer=box("boss@avito.ru", "Босс"),
        required_attendees=[NS(mailbox=box(e, n)) for e, n in attendees] or None,
        optional_attendees=[NS(mailbox=box(e, n)) for e, n in optional] or None,
        is_cancelled=cancelled, my_response_type="Accept",
    )
    fields.update(extra)
    return NS(**fields)


class FakeAccount:
    """`account.calendar.view(start, end).only(*fields)` → items."""

    def __init__(self, items):
        self.items = items
        self.views: list[tuple[datetime.datetime, datetime.datetime]] = []
        self.fields: tuple = ()
        self.fail: Exception | None = None
        self.calendar = self

    def view(self, start, end):
        self.views.append((start, end))
        if self.fail:
            raise self.fail
        return self

    def only(self, *fields):
        self.fields = fields
        return list(self.items)


TODAY = datetime.date(2026, 11, 6)


def at(hour, minute=0, day=TODAY, tz=MSK):
    return datetime.datetime.combine(day, datetime.time(hour, minute), tz)


ITEMS = [
    # Exchange answers in UTC; 07:00 UTC is 10:00 in Moscow.
    item("Стендап", at(7, tz=UTC), at(7, 15, tz=UTC),
         attendees=[("kir@avito.ru", "Кир"), ("ivanov@avito.ru", "Иванов Пётр")],
         optional=[("petrova@avito.ru", "Петрова")]),
    item("Отпуск", TODAY - datetime.timedelta(days=1), TODAY + datetime.timedelta(days=1), all_day=True),
    item("Отменили", at(12), at(13), cancelled=True),
    item("1-1 с Сидоровым", at(16), at(16, 30), attendees=[("sidorov@avito.ru", "Сидоров")],
         recurrence_id=at(13, tz=UTC)),
]


@pytest.fixture
def account(monkeypatch):
    fake = FakeAccount(list(ITEMS))
    connects = []

    def connect(email, username, password):
        connects.append((email, username, password))
        return fake

    monkeypatch.setattr(cal, "_connect", connect)
    monkeypatch.setattr(cal, "_current", None)
    monkeypatch.setattr(cal, "_now", lambda: at(9))
    fake.connects = connects
    return fake


@pytest.fixture
def app(tmp_path, account):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    app = create_app(config, [motor, cal.calendar])
    for field, value in (("email", "Kir@avito.ru"), ("username", "kir"), ("password", "pw-1")):
        app.state.store.set_secret("calendar", field, value)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        client.headers["Authorization"] = f"Bearer {app.state.store.add_consumer('мак')}"
        yield client


def test_day_shape(client, app, account):
    body = client.get("/calendar/day?date=2026-11-06").json()
    assert (body["date"], body["to"], body["count"]) == ("2026-11-06", "2026-11-06", 3)
    assert [e["subject"] for e in body["events"]] == ["Отпуск", "Стендап", "1-1 с Сидоровым"]
    standup = body["events"][1]
    assert standup == {
        "subject": "Стендап", "start": "2026-11-06T10:00+03:00", "end": "2026-11-06T10:15+03:00",
        "all_day": False, "location": "Переговорка «Нева»",
        "organizer": {"email": "boss@avito.ru", "name": "Босс"},
        "attendees": [{"email": "ivanov@avito.ru", "name": "Иванов Пётр"},
                      {"email": "petrova@avito.ru", "name": "Петрова", "optional": True}],
        "attendees_total": 2, "response": "accept", "uid": "uid-Стендап", "recurrence_id": "",
        "id": "AAMkСтендап",
    }
    vacation = body["events"][0]
    assert (vacation["start"], vacation["end"], vacation["all_day"]) == ("2026-11-05", "2026-11-07", True)
    assert body["events"][2]["recurrence_id"] == "2026-11-06T16:00+03:00"
    # Whole Moscow days, and only the fields we show.
    assert account.views == [(at(0), at(0, day=TODAY + datetime.timedelta(days=1)))]
    assert "body" not in account.fields and "required_attendees" in account.fields
    assert account.connects == [("Kir@avito.ru", "kir", "pw-1")]
    assert app.state.store.provider_state("calendar").status == "ok"


def test_range_attendee_and_limit(client, account):
    body = client.get("/calendar/day?date=2026-11-10&to=2026-11-06&attendee=Иванов").json()
    assert (body["date"], body["to"]) == ("2026-11-06", "2026-11-10")
    assert [e["subject"] for e in body["events"]] == ["Стендап"]
    assert account.views[-1] == (at(0), at(0, day=datetime.date(2026, 11, 11)))
    assert client.get("/calendar/day?attendee=sidorov").json()["count"] == 1
    assert client.get("/calendar/day?attendee=boss").json()["count"] == 3  # the organizer counts
    assert client.get("/calendar/day?limit=1").json()["count"] == 1


def test_upcoming_drops_what_ended(client, monkeypatch):
    monkeypatch.setattr(cal, "_now", lambda: at(12))
    body = client.get("/calendar/upcoming?days=1").json()
    assert body["now"] == "2026-11-06T12:00+03:00"
    assert [e["subject"] for e in body["events"]] == ["Отпуск", "1-1 с Сидоровым"]


def test_status(client):
    body = client.get("/calendar/status").json()
    assert body == {"email": "Kir@avito.ru", "server": "exchange.avito.ru", "timezone": "Europe/Moscow",
                    "today": "2026-11-06", "today_count": 3}


def test_many_attendees_are_capped(client, account):
    crowd = [(f"p{i}@avito.ru", f"Человек {i}") for i in range(200)]
    account.items = [item("Общее", at(11), at(12), attendees=crowd)]
    event = client.get("/calendar/day").json()["events"][0]
    assert (len(event["attendees"]), event["attendees_total"]) == (30, 200)


def test_bad_credentials_are_401(client, app, account):
    account.fail = ews.UnauthorizedError("Invalid credentials")
    response = client.get("/calendar/day")
    assert response.status_code == 401
    assert response.json()["error"] == "provider_token"
    assert "motor.gang.su" in response.json()["message"]
    assert app.state.store.provider_state("calendar").status == "expired"


def test_unreachable_exchange_is_503(client, app, account):
    account.fail = ews.ErrorTimeoutExpired("Reraised from ConnectTimeout")
    response = client.get("/calendar/upcoming")
    assert response.status_code == 503
    assert "direct" in response.json()["message"]
    assert app.state.store.provider_state("calendar").status == "down"


def test_missing_secret_is_401(client, app):
    app.state.store.set_secret("calendar", "password", "")
    response = client.get("/calendar/day")
    assert response.status_code == 401
    assert "motor.gang.su" in response.json()["message"]


def test_new_password_reconnects(client, app, account):
    client.get("/calendar/day")
    app.state.store.set_secret("calendar", "password", "pw-2")
    client.get("/calendar/day")
    assert [c[2] for c in account.connects] == ["pw-1", "pw-2"]


def test_bad_input_is_400_and_not_unhealthy(client, app):
    client.get("/calendar/day")
    assert client.get("/calendar/day?date=завтра").status_code == 400
    assert client.get("/calendar/day?date=2026-01-01&to=2026-12-31").status_code == 400
    assert client.get("/calendar/upcoming?days=0").status_code == 400
    assert client.get("/calendar/day?limit=0").status_code == 400
    assert app.state.store.provider_state("calendar").status == "ok"


def test_writes_are_403(client, account):
    response = client.post("/calendar/day", json={})
    assert response.status_code == 403
    assert not account.views


def test_mcp_lists_three_read_only_tools(client):
    response = client.post("/calendar", headers=MCP_HEADERS,
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert sorted(tools) == ["day", "status", "upcoming"]
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools.values())
    call = client.post("/calendar", headers=MCP_HEADERS, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "day", "arguments": {"attendee": "Сидоров"}},
    })
    assert json.loads(call.json()["result"]["content"][0]["text"])["count"] == 1
