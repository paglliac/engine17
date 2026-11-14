"""`family` against a fake Google Calendar API v3: shapes, create/update/move/delete, errors."""
from __future__ import annotations

import datetime
import importlib
import json

import httpx
import pytest
from starlette.testclient import TestClient

from motor.app import create_app
from motor.config import Config
from motor.providers.motor import motor

fam = importlib.import_module("motor.providers.family")

CAL = "family123@group.calendar.google.com"
KEY = json.dumps({"type": "service_account", "client_email": "motor@proj.iam.gserviceaccount.com"})


class FakeGoogle:
    def __init__(self):
        self.events = {
            "e1": {"id": "e1", "status": "confirmed", "summary": "Врач", "location": "Клиника",
                   "start": {"dateTime": "2026-11-20T15:00:00Z"}, "end": {"dateTime": "2026-11-20T16:00:00Z"},
                   "creator": {"email": "wife@gmail.com"}, "htmlLink": "https://cal/e1"},
            "e2": {"id": "e2", "status": "confirmed", "summary": "Дача",
                   "start": {"date": "2026-11-21"}, "end": {"date": "2026-11-23"}},
        }
        self.calls: list[tuple[str, str, dict, object]] = []
        self.status = 200
        self.error_reason = "forbidden"
        self.zone = "Europe/Moscow"

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        params = dict(request.url.params)
        path = request.url.path.removeprefix("/calendar/v3")
        self.calls.append((request.method, path, params, body))
        assert request.headers["authorization"] == "Bearer tok"
        if self.status != 200:
            return httpx.Response(self.status, json={"error": {"message": "nope",
                                                               "errors": [{"reason": self.error_reason}]}})
        base = f"/calendars/{CAL}"
        if path == base:
            return httpx.Response(200, json={"summary": "Семья", "timeZone": self.zone})
        if path == f"{base}/events" and request.method == "GET":
            items = sorted(self.events.values(), key=lambda e: json.dumps(e["start"]))
            return httpx.Response(200, json={"items": items})
        if path == f"{base}/events" and request.method == "POST":
            ev = {"id": "new1", "status": "confirmed", **body}
            self.events["new1"] = ev
            return httpx.Response(200, json=ev)
        if path.startswith(f"{base}/events/"):
            eid = path.rsplit("/", 1)[1]
            if eid not in self.events:
                return httpx.Response(410 if request.method == "DELETE" else 404, json={"error": {"message": "gone"}})
            if request.method == "GET":
                return httpx.Response(200, json=self.events[eid])
            if request.method == "PATCH":
                self.events[eid].update(body)
                return httpx.Response(200, json=self.events[eid])
            if request.method == "DELETE":
                del self.events[eid]
                return httpx.Response(204)
        return httpx.Response(404, json={"error": {"message": "?"}})


@pytest.fixture
def google(monkeypatch):
    fake = FakeGoogle()
    monkeypatch.setattr(fam, "transport", httpx.MockTransport(fake))
    monkeypatch.setattr(fam, "_token", lambda: "tok")
    monkeypatch.setattr(fam, "_zone", None)
    return fake


@pytest.fixture
def app(tmp_path, google):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    app = create_app(config, [motor, fam.family])
    app.state.store.set_secret("family", "key", KEY)
    app.state.store.set_secret("family", "calendar", CAL)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        client.headers["Authorization"] = f"Bearer {app.state.store.add_consumer('мак')}"
        yield client


def test_day_shape_and_window(client, google):
    body = client.get("/family/day?date=2026-11-20&to=2026-11-22").json()
    assert body["count"] == 2
    doctor = next(e for e in body["events"] if e["id"] == "e1")
    assert doctor == {"id": "e1", "title": "Врач", "start": "2026-11-20T18:00+03:00", "end": "2026-11-20T19:00+03:00",
                      "all_day": False, "location": "Клиника", "description": "", "created_by": "wife@gmail.com",
                      "url": "https://cal/e1"}
    dacha = next(e for e in body["events"] if e["id"] == "e2")
    assert (dacha["start"], dacha["end"], dacha["all_day"]) == ("2026-11-21", "2026-11-22", True)
    params = google.calls[-1][2]
    assert (params["timeMin"], params["timeMax"]) == ("2026-11-20T00:00:00+03:00", "2026-11-23T00:00:00+03:00")
    assert params["singleEvents"] == "true" and params["orderBy"] == "startTime"


def test_status(client):
    assert client.get("/family/status").json() == {"calendar": "Семья", "timezone": "Europe/Moscow",
                                                   "account": "motor@proj.iam.gserviceaccount.com"}


def test_create_timed_defaults_to_an_hour_moscow(client, google):
    body = client.post("/family/create_event", json={"title": "Родительское собрание", "start": "2026-11-25T19:00"}).json()
    sent = google.calls[-1][3]
    assert sent["start"] == {"dateTime": "2026-11-25T19:00:00+03:00", "timeZone": "Europe/Moscow"}
    assert sent["end"]["dateTime"] == "2026-11-25T20:00:00+03:00"
    assert google.calls[-1][2] == {"sendUpdates": "none"}
    assert body["event"]["id"] == "new1"


def test_create_all_day_end_inclusive(client, google):
    client.post("/family/create_event", json={"title": "Отпуск", "start": "2026-12-28", "end": "2027-01-03"})
    sent = google.calls[-1][3]
    assert (sent["start"], sent["end"]) == ({"date": "2026-12-28"}, {"date": "2027-01-04"})


def test_move_keeps_duration(client, google):
    client.post("/family/update_event", json={"id": "e1", "start": "2026-11-21T10:30"})
    patch = google.calls[-1][3]
    assert patch["start"]["dateTime"] == "2026-11-21T10:30:00+03:00"
    assert patch["end"]["dateTime"] == "2026-11-21T11:30:00+03:00"
    client.post("/family/update_event", json={"id": "e2", "start": "2026-11-28"})
    patch = google.calls[-1][3]
    assert (patch["start"], patch["end"]) == ({"date": "2026-11-28"}, {"date": "2026-11-30"})


def test_update_only_what_is_given(client, google):
    client.post("/family/update_event", json={"id": "e1", "title": "Стоматолог", "location": ""})
    patches = [c[3] for c in google.calls if c[0] == "PATCH"]
    assert patches == [{"summary": "Стоматолог", "location": ""}]
    assert client.post("/family/update_event", json={"id": "e1"}).status_code == 400
    assert client.post("/family/update_event", json={"id": "nope", "title": "x"}).status_code == 404


def test_delete_and_already_gone(client, google):
    assert client.post("/family/delete_event", json={"id": "e1"}).json() == {"ok": True, "id": "e1"}
    assert "e1" not in google.events
    assert client.post("/family/delete_event", json={"id": "e1"}).status_code == 200


def test_bad_times_are_400(client, google):
    for body in ({"title": "x", "start": "завтра"}, {"title": "x", "start": "2026-11-25T19:00", "end": "2026-11-25T18:00"},
                 {"title": "x", "start": "2026-11-25", "end": "2026-11-25T18:00"}, {"title": " ", "start": "2026-11-25"}):
        assert client.post("/family/create_event", json=body).status_code == 400, body
    assert not [call for call in google.calls if call[0] != "GET"]  # nothing written


def test_no_write_permission_is_explained(client, app, google):
    google.status = 403
    response = client.post("/family/create_event", json={"title": "x", "start": "2026-11-25"})
    assert response.status_code == 401
    assert "motor@proj.iam.gserviceaccount.com" in response.json()["message"]
    assert "Вносить изменения" in response.json()["message"]
    google.error_reason = "rateLimitExceeded"
    assert client.get("/family/day").status_code == 503


def test_calendar_not_shared_is_explained(client, app, google):
    app.state.store.set_secret("family", "calendar", "other@group.calendar.google.com")
    response = client.get("/family/status")
    assert response.status_code == 401 and "расшарен" in response.json()["message"]


def test_times_are_in_the_calendars_own_zone(client, google):
    google.zone = "Asia/Krasnoyarsk"
    doctor = next(e for e in client.get("/family/day?date=2026-11-20").json()["events"] if e["id"] == "e1")
    assert doctor["start"] == "2026-11-20T22:00+07:00"  # 15:00Z, as the family sees it
    assert google.calls[-1][2]["timeMin"] == "2026-11-20T00:00:00+07:00"
    client.post("/family/create_event", json={"title": "Ужин", "start": "2026-11-25T18:00"})
    assert google.calls[-1][3]["start"] == {"dateTime": "2026-11-25T18:00:00+07:00", "timeZone": "Asia/Krasnoyarsk"}
    assert sum(1 for c in google.calls if c[1].endswith(".com")) == 1  # the zone is read once, then remembered
