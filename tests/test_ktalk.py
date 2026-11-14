"""`ktalk` against a fake ktalk (httpx.MockTransport): the cURL paste, listing, transcript, errors."""
from __future__ import annotations

import datetime
import importlib

import httpx
import pytest
from starlette.testclient import TestClient

from motor.app import create_app
from motor.config import Config
from motor.providers.motor import motor

kt = importlib.import_module("motor.providers.ktalk")

TODAY = datetime.date.today()
CURL = (
    "curl 'https://avito.ktalk.ru/api/recordings?skip=0&top=50' \\\n"
    "  -H 'accept: application/json' \\\n"
    "  -b 'sessionToken=sess-1; ngtoken=ng-1; kontur_ngtoken=kng-1; _ga=x' \\\n"
    "  -H 'x-platform: web'"
)


def rec(n, days_ago):
    return {"id": f"m{n}", "title": f"Встреча {n}", "createdDate": f"{TODAY - datetime.timedelta(days=days_ago)}T10:00:00Z"}


TRANSCRIPT = {
    "status": "Complete",
    "tracks": [
        {"speaker": {"userInfo": {"firstname": "Кир", "surname": "А"}},
         "chunks": [{"startTimeOffsetInMillis": 65_000, "text": "второе"}]},
        {"speaker": {"userInfo": {"firstname": "Боб", "surname": ""}},
         "chunks": [{"startTimeOffsetInMillis": 1_000, "text": "первое"}]},
    ],
}


class FakeKtalk:
    def __init__(self):
        self.records = [rec(1, 0), rec(2, 2), rec(3, 10)]
        self.calls: list[str] = []
        self.headers: httpx.Headers | None = None
        self.status = 200
        self.fail: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(str(request.url))
        self.headers = request.headers
        if self.fail:
            raise self.fail
        if self.status != 200:
            return httpx.Response(self.status, json={})
        path = request.url.path
        if path == "/api/recordings":
            skip, top = int(request.url.params["skip"]), int(request.url.params["top"])
            return httpx.Response(200, json={"recordings": self.records[skip:skip + top]})
        if path == "/api/recordings/m1/transcript":
            return httpx.Response(200, json=TRANSCRIPT)
        if path == "/api/recordings/m2/transcript":
            return httpx.Response(200, json={"status": "inProgress", "statusMessage": "..."})
        return httpx.Response(404, json={})


@pytest.fixture
def fake(monkeypatch):
    server = FakeKtalk()
    monkeypatch.setattr(kt, "transport", httpx.MockTransport(server))
    return server


@pytest.fixture
def app(tmp_path, fake):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    app = create_app(config, [motor, kt.ktalk])
    app.state.store.set_secret("ktalk", "curl", CURL)
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        client.headers["Authorization"] = f"Bearer {app.state.store.add_consumer('мак')}"
        yield client


def test_curl_paste_is_parsed_like_the_hub():
    assert kt.session_from_curl(CURL) == {"sessionToken": "sess-1", "ngtoken": "ng-1", "kontur_ngtoken": "kng-1"}
    assert kt.session_from_curl("Cookie: sessionToken=a; ngtoken=b") == {"sessionToken": "a", "ngtoken": "b"}
    assert kt.session_from_curl("curl 'x' -H 'Authorization: Session zzz'") == {"sessionToken": "zzz"}


def test_recordings_window_and_session(client, app, fake):
    body = client.get("/ktalk/recordings?days=7").json()
    assert [r["id"] for r in body["recordings"]] == ["m1", "m2"]
    assert body["recordings"][0]["url"] == "https://avito.ktalk.ru/recordings/m1"
    assert fake.headers["authorization"] == "Session sess-1"
    assert "kontur_ngtoken=kng-1" in fake.headers["cookie"] and "_ga" not in fake.headers["cookie"]
    assert client.get(f"/ktalk/recordings?since={TODAY - datetime.timedelta(days=30)}").json()["count"] == 3
    assert client.get("/ktalk/recordings?days=30&limit=1").json()["count"] == 1
    assert app.state.store.provider_state("ktalk").status == "ok"


def test_transcript_whole_and_in_order(client):
    body = client.get("/ktalk/transcript?id=https://avito.ktalk.ru/recordings/m1").json()
    assert body["ready"] is True
    assert body["text"] == "[00:01] Боб: первое\n[01:05] Кир А: второе"
    assert body["speakers"] == ["Кир А", "Боб"]


def test_transcript_not_ready_is_not_an_error(client):
    body = client.get("/ktalk/transcript?id=m2").json()
    assert (body["ready"], body["text"], body["status"]) == (False, "", "inProgress")


def test_errors(client, app, fake):
    assert client.get("/ktalk/transcript?id=nope").status_code == 404
    assert client.get("/ktalk/recordings?days=0").status_code == 400
    fake.status = 401
    response = client.get("/ktalk/status")
    assert response.status_code == 401 and "cURL" in response.json()["message"]
    assert app.state.store.provider_state("ktalk").status == "expired"
    fake.status, fake.fail = 200, httpx.ConnectTimeout("slow")
    response = client.get("/ktalk/status")
    assert response.status_code == 503 and "direct" in response.json()["message"]


def test_paste_without_session_is_401(client, app):
    app.state.store.set_secret("ktalk", "curl", "curl 'x' -H 'accept: */*'")
    response = client.get("/ktalk/recordings")
    assert response.status_code == 401 and "sessionToken" in response.json()["message"]


def test_read_only(client, fake):
    assert client.post("/ktalk/recordings", json={}).status_code == 403
    assert not fake.calls
