"""`todoist` against a fake Todoist API v1: inbox, paging, delete, errors."""
from __future__ import annotations

import importlib

import httpx
import pytest
from starlette.testclient import TestClient

from motor.app import create_app
from motor.config import Config
from motor.providers.motor import motor

td = importlib.import_module("motor.providers.todoist")

PROJECTS = [{"id": "p1", "name": "Работа"}, {"id": "p0", "name": "Inbox", "inbox_project": True}]


class FakeTodoist:
    def __init__(self):
        self.tasks = {"p0": [{"id": f"t{i}", "content": f"задача {i}", "project_id": "p0"} for i in range(5)],
                      "p1": [{"id": "w1", "content": "работа", "project_id": "p1", "url": "https://x/w1"}]}
        self.calls: list[tuple[str, str, dict]] = []
        self.auth = ""
        self.status = 200

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.calls.append((request.method, request.url.path, params))
        self.auth = request.headers["authorization"]
        if self.status != 200:
            return httpx.Response(self.status, text="nope")
        path = request.url.path.removeprefix("/api/v1")
        if path == "/projects":
            return httpx.Response(200, json={"results": PROJECTS, "next_cursor": None})
        if path == "/tasks":
            items = self.tasks.get(params["project_id"], [])
            start = int(params.get("cursor", 0))
            end = start + int(params["limit"])
            return httpx.Response(200, json={"results": items[start:end],
                                             "next_cursor": str(end) if end < len(items) else None})
        if request.method == "DELETE" and path.startswith("/tasks/"):
            tid = path.rsplit("/", 1)[1]
            for items in self.tasks.values():
                for t in items:
                    if t["id"] == tid:
                        items.remove(t)
                        return httpx.Response(204)
            return httpx.Response(404, text="Task not found")
        return httpx.Response(404, text="?")


@pytest.fixture
def fake(monkeypatch):
    server = FakeTodoist()
    monkeypatch.setattr(td, "transport", httpx.MockTransport(server))
    return server


@pytest.fixture
def app(tmp_path, fake):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    app = create_app(config, [motor, td.todoist])
    app.state.store.set_secret("todoist", "token", "td-1")
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        client.headers["Authorization"] = f"Bearer {app.state.store.add_consumer('мак')}"
        yield client


def test_inbox_by_default_whole_objects(client, fake):
    body = client.get("/todoist/tasks").json()
    assert (body["project_id"], body["count"]) == ("p0", 5)
    assert body["tasks"][0] == {"id": "t0", "content": "задача 0", "project_id": "p0",
                                "url": "https://app.todoist.com/app/task/t0"}
    assert fake.auth == "Bearer td-1"
    assert client.get("/todoist/tasks?project=p1").json()["tasks"][0]["url"] == "https://x/w1"


def test_paging_follows_the_cursor_up_to_limit(client, fake, monkeypatch):
    monkeypatch.setattr(td, "PAGE", 2)
    assert client.get("/todoist/tasks?limit=3").json()["count"] == 3
    assert client.get("/todoist/tasks?limit=50").json()["count"] == 5
    task_calls = [c for c in fake.calls if c[1] == "/api/v1/tasks"]
    assert [c[2].get("cursor") for c in task_calls[-3:]] == [None, "2", "4"]


def test_status_and_projects(client):
    assert client.get("/todoist/status").json() == {"projects": 2, "inbox": {"id": "p0", "name": "Inbox"}}
    projects = client.get("/todoist/projects").json()["projects"]
    assert [(p["id"], p["inbox"]) for p in projects] == [("p1", False), ("p0", True)]


def test_delete_is_a_write_and_gone_is_fine(client, fake):
    assert client.get("/todoist/delete_task?id=t1").status_code == 405
    assert not [c for c in fake.calls if c[0] == "DELETE"]
    assert client.post("/todoist/delete_task", json={"id": "t1"}).json() == {"ok": True, "id": "t1"}
    assert client.post("/todoist/delete_task", json={"id": "t1"}).status_code == 200  # gone already: fine
    assert [t["id"] for t in fake.tasks["p0"]] == ["t0", "t2", "t3", "t4"]


def test_errors(client, app, fake):
    fake.status = 401
    response = client.get("/todoist/tasks")
    assert response.status_code == 401 and "motor.gang.su" in response.json()["message"]
    assert app.state.store.provider_state("todoist").status == "expired"
    fake.status = 503
    assert client.get("/todoist/status").status_code == 503
    fake.status = 200
    assert client.get("/todoist/tasks?limit=0").status_code == 400
