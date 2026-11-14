"""`tracker`, `people`, `inbox`, `triage` against a fake hub: routes, bodies, one token, errors."""
from __future__ import annotations

import importlib
import json

import httpx
import pytest
from starlette.testclient import TestClient

from motor.app import create_app
from motor.config import Config
from motor.providers.motor import motor

hub = importlib.import_module("motor.providers.hub")
tr = importlib.import_module("motor.providers.tracker")
pp = importlib.import_module("motor.providers.people")
ib = importlib.import_module("motor.providers.inbox")
tg = importlib.import_module("motor.providers.triage")

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


class FakeHub:
    def __init__(self):
        self.calls: list[tuple[str, str, dict, object]] = []
        self.auth = ""
        self.answer: httpx.Response | None = None
        self.fail: Exception | None = None

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, dict(request.url.params), body))
        self.auth = request.headers.get("authorization", "")
        self.raw = request.url.raw_path.decode().split("?")[0]
        if self.fail:
            raise self.fail
        if self.answer is not None:
            return self.answer
        return httpx.Response(200, json={"ok": True, "path": request.url.path})

    @property
    def last(self):
        return self.calls[-1]


@pytest.fixture
def fake(monkeypatch):
    server = FakeHub()
    monkeypatch.setattr(hub, "transport", httpx.MockTransport(server))
    monkeypatch.setattr(hub, "_client", None)
    return server


@pytest.fixture
def app(tmp_path, fake):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    app = create_app(config, [motor, tr.tracker, pp.people, ib.inbox, tg.triage])
    app.state.store.set_secret("tracker", "token", "sb-1")
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        client.headers["Authorization"] = f"Bearer {app.state.store.add_consumer('мак')}"
        yield client


def test_reads_go_to_the_hub_with_its_token(client, fake):
    assert client.get("/tracker/list_tasks?owner=me&overdue=true").json()["ok"] is True
    assert fake.last == ("GET", "/api/tracker/tasks", {"include_done": "false", "overdue": "true", "owner": "me"},
                         None)
    assert fake.auth == "Bearer sb-1"
    client.get("/tracker/get_me")
    assert fake.last[1] == "/api/me"
    client.get("/people/resolve_person?query=Ваня")
    assert fake.last[1:3] == ("/api/people/resolve", {"q": "Ваня"})
    client.get("/inbox/list_captures?state=failed")
    assert fake.last[1:3] == ("/api/inbox/captures", {"state": "failed"})
    client.get("/triage/debug_route?dest=people/x/tasks")
    assert fake.last[1:3] == ("/api/triage/debug/route", {"dest": "people/x/tasks"})
    client.get("/tracker/get_agenda?key=uid-1|")
    assert fake.last[1:3] == ("/api/calendar/agenda", {"key": "uid-1|"})


def test_writes_carry_the_original_bodies(client, fake):
    client.post("/tracker/add_task", json={"text": "Позвонить", "project": "capacity"})
    method, path, _, body = fake.last
    assert (method, path) == ("POST", "/api/tracker/tasks")
    assert body["text"] == "Позвонить" and body["owner"] == "me" and body["project"] == "capacity"
    client.post("/tracker/update_task", json={"id": "ns-001", "clear_due": True})
    assert fake.last[0:2] == ("PATCH", "/api/tracker/tasks/ns-001") and fake.last[3]["has_due"] is True
    client.post("/tracker/set_project_status", json={"slug": "a", "status": "done"})
    assert fake.last[0:2] == ("PATCH", "/api/projects/a/status")
    client.post("/tracker/remove_project_alias", json={"slug": "a", "alias": "b"})
    assert fake.last[0:2] == ("DELETE", "/api/projects/aliases") and fake.last[3] == {"slug": "a", "alias": "b"}
    client.post("/tracker/add_agenda_item", json={"key": "k", "text": "обсудить"})
    assert fake.last[0:2] == ("POST", "/api/calendar/agenda/items")
    client.post("/triage/decide", json={"item_id": 7, "action": "accept", "edits": {"due": "2026-11-20"}})
    assert fake.last[3] == {"id": 7, "action": "accept", "edits": {"due": "2026-11-20"}}
    client.post("/triage/create_items", json={"items": [{"text": "x"}]})
    assert fake.last[3] == {"items": [{"text": "x"}]}
    client.post("/inbox/retry_capture", json={"capture_id": 12})
    assert fake.last[0:2] == ("POST", "/api/inbox/captures/12/retry")
    client.post("/people/create_person", json={"slug": "ivan-petrov", "name": "Иван"})
    assert fake.last[3] == {"slug": "ivan-petrov", "name": "Иван"}


def test_ids_cannot_step_out_of_their_path(client, fake):
    client.post("/tracker/delete_task", json={"id": "../../me"})
    assert fake.raw == "/api/tracker/tasks/..%2F..%2Fme"


def test_one_token_for_all_four(client, app, fake):
    for path in ("/tracker/list_someday", "/people/list_people", "/inbox/status", "/triage/triage_status"):
        assert client.get(path).status_code == 200
    assert {call[1] for call in fake.calls} >= {"/api/people/", "/api/inbox/status", "/api/triage/status"}
    app.state.store.set_secret("tracker", "token", "")
    response = client.get("/people/list_people")
    assert response.status_code == 401
    assert "Трекер" in response.json()["message"]
    assert app.state.store.provider_state("people").status == "expired"


def test_hub_refusal_text_comes_through(client, app, fake):
    client.get("/tracker/list_someday")
    fake.answer = httpx.Response(400, json={"error": "an existing source is never replaced"})
    response = client.post("/tracker/update_task", json={"id": "ns-1", "source_url": "https://x"})
    assert response.status_code == 400
    assert "never replaced" in response.json()["message"]
    fake.answer = httpx.Response(404, json={"error": "no such project"})
    assert client.get("/tracker/get_project?slug=nope").status_code == 404
    assert app.state.store.provider_state("tracker").status == "ok"


def test_hub_down_and_bad_token(client, app, fake):
    fake.fail = httpx.ConnectError("refused")
    assert client.get("/triage/list_items").status_code == 503
    assert app.state.store.provider_state("triage").status == "down"
    fake.fail = None
    fake.answer = httpx.Response(401, json={"error": "unauthorized"})
    assert client.get("/inbox/status").status_code == 401
    assert app.state.store.provider_state("inbox").status == "expired"


def test_update_person_needs_something_to_change(client, fake):
    assert client.post("/people/update_person", json={"slug": "x"}).status_code == 400
    assert not fake.calls


def test_mcp_tool_counts_and_write_flags(client):
    expected = {"tracker": 28, "people": 8, "inbox": 10, "triage": 6}
    for name, count in expected.items():
        response = client.post(f"/{name}", headers=MCP_HEADERS,
                               json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
        assert len(tools) == count, name
    tracker_tools = {tool["name"]: tool for tool in client.post("/tracker", headers=MCP_HEADERS, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]}
    assert tracker_tools["list_tasks"]["annotations"]["readOnlyHint"] is True
    assert tracker_tools["add_task"]["annotations"]["readOnlyHint"] is False
