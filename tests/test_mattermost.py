"""`mattermost` against a fake Mattermost (httpx.MockTransport): shapes, errors, read-only."""
from __future__ import annotations

import importlib
import json

import httpx
import pytest
from starlette.testclient import TestClient

from motor.app import create_app
from motor.config import Config
from motor.providers.motor import motor

# The module, not the provider object of the same name that motor.providers re-exports.
mm = importlib.import_module("motor.providers.mattermost")

MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}
ME = {"id": "u" * 26, "username": "kir", "first_name": "Кир", "last_name": "А"}
BOB = {"id": "b" * 26, "username": "bob"}
TEAM = {"id": "t" * 26, "name": "avito", "display_name": "Avito"}
CHANNEL = {"id": "c" * 26, "name": "avitoid-dev", "display_name": "AvitoID dev", "type": "O",
           "total_msg_count": 12, "last_post_at": 1_762_400_000_000}
POSTS = {
    "p1": {"id": "p1", "user_id": BOB["id"], "channel_id": CHANNEL["id"], "message": "первый",
           "create_at": 1_762_300_000_000},
    "p2": {"id": "p2", "user_id": ME["id"], "channel_id": CHANNEL["id"], "message": "ответ",
           "root_id": "p1", "create_at": 1_762_300_060_000,
           "metadata": {"files": [{"name": "схема.png"}]}},
}


class FakeMattermost:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []
        self.status = 200
        self.fail: Exception | None = None
        self.auth = ""

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v4")
        self.calls.append((request.method, path))
        self.auth = request.headers["authorization"]
        if self.fail:
            raise self.fail
        if self.status != 200:
            return httpx.Response(self.status, json={"message": "nope"})
        routes = {
            "/users/me": ME,
            "/users/me/teams": [TEAM],
            f"/users/{BOB['id']}": BOB,
            f"/users/{ME['id']}": ME,
            f"/channels/{CHANNEL['id']}": CHANNEL,
            f"/users/me/teams/{TEAM['id']}/channels": [CHANNEL],
            f"/users/me/teams/{TEAM['id']}/channels/members": [
                {"channel_id": CHANNEL["id"], "msg_count": 10, "mention_count": 1,
                 "notify_props": {"mark_unread": "all"}}],
            f"/users/{ME['id']}/posts/flagged": {"order": ["p1", "p2"], "posts": POSTS},
            f"/teams/{TEAM['id']}/channels/name/avitoid-dev": CHANNEL,
            f"/channels/{CHANNEL['id']}/posts": {"order": ["p2", "p1"], "posts": POSTS},
            "/posts/p2": POSTS["p2"],
            "/posts/p1/thread": {"order": ["p1", "p2"], "posts": POSTS},
            f"/teams/{TEAM['id']}/posts/search": {"order": ["p1"], "posts": {"p1": POSTS["p1"]}},
            "/users/search": [BOB],
        }
        if path in routes:
            return httpx.Response(200, json=routes[path])
        return httpx.Response(404, json={"message": "not found"})


@pytest.fixture
def fake(monkeypatch):
    server = FakeMattermost()
    monkeypatch.setattr(mm, "transport", httpx.MockTransport(server))
    monkeypatch.setattr(mm, "_current", None)
    return server


@pytest.fixture
def app(tmp_path, fake):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    app = create_app(config, [motor, mm.mattermost])
    app.state.store.set_secret("mattermost", "token", "pat-1")
    return app


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        client.headers["Authorization"] = f"Bearer {app.state.store.add_consumer('мак')}"
        yield client


def test_saved_posts_shape(client, app):
    response = client.get("/mattermost/saved_posts?limit=10")
    assert response.status_code == 200
    body = response.json()
    assert body["count"] == 2
    newest = body["posts"][0]
    assert newest == {
        "post_id": "p2", "user": "kir", "created": "2025-11-04T23:47:40Z", "message": "ответ",
        "permalink": "https://mt.avito.ru/_redirect/pl/p2", "root_id": "p1",
        "channel": "AvitoID dev", "channel_id": CHANNEL["id"], "files": ["схема.png"],
    }
    assert app.state.store.provider_state("mattermost").status == "ok"


def test_read_actions(client, fake):
    assert client.get("/mattermost/status").json()["me"]["username"] == "kir"
    assert client.get("/mattermost/channels?query=avito").json()["channels"][0]["type"] == "public"
    thread = client.get("/mattermost/thread?post=https://mt.avito.ru/avito/pl/p2").json()
    assert [p["is_target"] for p in thread["posts"]] == [False, True]
    history = client.get("/mattermost/channel_posts?channel=~avitoid-dev&since=2025-11-01").json()
    assert [p["post_id"] for p in history["posts"]] == ["p1", "p2"]
    unread = client.get("/mattermost/unreads").json()["channels"][0]
    assert (unread["unread"], unread["mentions"], unread["muted"]) == (2, 1, False)
    assert client.get("/mattermost/users?query=bob").json()["users"][0]["username"] == "bob"
    assert client.get("/mattermost/search?query=первый&from_user=bob").json()["count"] == 1
    assert client.get("/mattermost/mentions?days=3").json()["count"] == 1
    # Names are looked up once per client, not once per post.
    assert fake.calls.count(("GET", f"/users/{BOB['id']}")) == 1


def test_every_write_is_403(client, fake):
    for path in ("/mattermost/post", "/mattermost/saved_posts"):
        response = client.post(path, json={"channel": "x", "message": "привет"})
        assert response.status_code == 403
        assert response.json()["error"] == "write_forbidden"
    assert not [call for call in fake.calls if call[0] == "POST"]


def test_mattermost_401_is_expired_token(client, app, fake):
    fake.status = 401
    response = client.get("/mattermost/saved_posts")
    assert response.status_code == 401
    assert response.json()["error"] == "provider_token"
    assert "motor.gang.su" in response.json()["message"]
    assert app.state.store.provider_state("mattermost").status == "expired"


def test_network_failure_is_503(client, app, fake):
    fake.fail = httpx.ConnectError("no route")
    response = client.get("/mattermost/unreads")
    assert response.status_code == 503
    assert app.state.store.provider_state("mattermost").status == "down"


def test_bad_input_does_not_mark_provider_unhealthy(client, app):
    client.get("/mattermost/status")
    assert client.get("/mattermost/channel_posts?channel=нет-такого").status_code == 404
    assert client.get("/mattermost/search?query=x&after=вчера").status_code == 400
    assert client.get("/mattermost/saved_posts?limit=0").status_code == 400
    assert app.state.store.provider_state("mattermost").status == "ok"


def test_new_token_on_the_page_takes_effect(client, app, fake):
    client.get("/mattermost/status")
    assert fake.auth == "Bearer pat-1"
    app.state.store.set_secret("mattermost", "token", "pat-2")
    client.get("/mattermost/status")
    assert fake.auth == "Bearer pat-2"


def test_mcp_lists_nine_read_only_tools(client):
    response = client.post("/mattermost", headers=MCP_HEADERS,
                           json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {tool["name"]: tool for tool in response.json()["result"]["tools"]}
    assert sorted(tools) == ["channel_posts", "channels", "mentions", "saved_posts", "search",
                             "status", "thread", "unreads", "users"]
    assert all(tool["annotations"]["readOnlyHint"] for tool in tools.values())
    call = client.post("/mattermost", headers=MCP_HEADERS, json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "saved_posts", "arguments": {"limit": 1}},
    })
    assert json.loads(call.json()["result"]["content"][0]["text"])["count"] == 1
