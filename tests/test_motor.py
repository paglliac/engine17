"""The skeleton end to end: tokens, actions over HTTP and MCP, errors, the page."""
from __future__ import annotations

import json

import pytest
from starlette.testclient import TestClient

from motor.app import create_app
from motor.auth import hash_password
from motor.config import Config
from motor.provider import Provider, Secret
from motor.providers.motor import motor

PASSWORD = "correct horse battery staple"
HASH = hash_password(PASSWORD)
MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
    "MCP-Protocol-Version": "2025-06-18",
}


def make_providers():
    demo = Provider(
        "demo", "Демо", writes=False, instructions="Тестовый поставщик.",
        secrets=[Secret("token", "Токен демо")],
        check=lambda: None,
    )

    @demo.action
    def items(since: str | None = None, ids: list[str] | None = None, limit: int = 30) -> list[dict]:
        """Элементы демо."""
        return [{"since": since, "ids": ids, "limit": limit, "token": demo.secret()}]

    @demo.action
    def expired() -> dict:
        """Всегда протух."""
        raise demo.expired()

    @demo.action
    def down() -> dict:
        """Всегда лежит."""
        raise demo.down("VPN Авито отвалился — введи код на motor.gang.su")

    @demo.action
    async def broken() -> dict:
        """Падает неожиданно."""
        raise KeyError("boom")

    todo = Provider("todo", "Задачи", writes=True, instructions="Пишущий тестовый поставщик.")

    @todo.action
    def tasks() -> list:
        """Список."""
        return []

    @todo.action(write=True)
    def delete_task(task_id: str) -> dict:
        """Удалить задачу."""
        return {"deleted": task_id}

    return [motor, demo, todo]


@pytest.fixture
def app(tmp_path):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=HASH, session_secret=b"s" * 32)
    return create_app(config, make_providers())


@pytest.fixture
def client(app):
    with TestClient(app, base_url="https://motor.gang.su") as client:
        yield client


@pytest.fixture
def token(app):
    return app.state.store.add_consumer("мак")


def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ── entry ────────────────────────────────────────────────────────────────────


def test_health_is_open(client):
    assert client.get("/healthz").status_code == 200


def test_no_token_is_401(client):
    for path in ("/motor/status", "/demo/items"):
        response = client.get(path)
        assert response.status_code == 401
        assert response.json()["error"] == "motor_token"
        assert "motor.gang.su" in response.json()["message"]
    assert client.post("/motor", headers=MCP_HEADERS, json={}).status_code == 401


def test_issued_token_passes_and_is_seen(client, app, token):
    response = client.get("/motor/status", headers=auth(token))
    assert response.status_code == 200
    body = response.json()
    assert body["consumer"] == "мак"
    assert [p["name"] for p in body["providers"]] == ["demo", "todo"]
    assert app.state.store.consumers()[0].last_seen_at is not None


def test_wrong_and_revoked_tokens_are_401(client, app, token):
    assert client.get("/motor/status", headers=auth("motor_nope")).status_code == 401
    app.state.store.revoke(app.state.store.consumers()[0].id)
    assert client.get("/motor/status", headers=auth(token)).status_code == 401


def test_page_cookie_does_not_open_providers(client):
    client.post("/login", data={"password": PASSWORD})
    assert client.get("/motor/status").status_code == 401


# ── actions over HTTP ────────────────────────────────────────────────────────


def test_action_params_are_converted(client, app, token):
    app.state.store.set_secret("demo", "token", "t-1")
    response = client.get("/demo/items?limit=5&ids=a&ids=b&since=2026-11-01", headers=auth(token))
    assert response.status_code == 200
    assert response.json() == [{"since": "2026-11-01", "ids": ["a", "b"], "limit": 5, "token": "t-1"}]
    assert app.state.store.provider_state("demo").status == "ok"


def test_bad_params_are_400(client, token):
    assert client.get("/demo/items?limit=many", headers=auth(token)).status_code == 400
    response = client.get("/demo/items?nope=1", headers=auth(token))
    assert response.status_code == 400
    assert "nope" in response.json()["message"]


def test_missing_provider_token_is_401_with_hint(client, app, token):
    response = client.get("/demo/items", headers=auth(token))
    assert response.status_code == 401
    assert response.json() == {"error": "provider_token", "message": "токена Демо нет — вставь его на motor.gang.su"}
    assert app.state.store.provider_state("demo").status == "expired"


def test_provider_errors(client, app, token):
    response = client.get("/demo/expired", headers=auth(token))
    assert (response.status_code, response.json()["error"]) == (401, "provider_token")
    assert "протух" in response.json()["message"]

    response = client.get("/demo/down", headers=auth(token))
    assert response.status_code == 503
    assert response.json()["message"] == "VPN Авито отвалился — введи код на motor.gang.su"
    assert app.state.store.provider_state("demo").status == "down"

    response = client.get("/demo/broken", headers=auth(token))
    assert response.status_code == 502
    assert app.state.store.provider_state("demo").status == "error"

    assert client.get("/demo/nothing", headers=auth(token)).status_code == 404
    assert client.get("/nothing/at/all", headers=auth(token)).status_code == 404


def test_write_rule(client, token):
    # writes=False: any POST is refused, whatever the action
    response = client.post("/demo/items", headers=auth(token), json={})
    assert (response.status_code, response.json()["error"]) == (403, "write_forbidden")
    assert client.post("/demo/anything", headers=auth(token)).status_code == 403
    # writes=True: write actions are POST with a JSON body
    response = client.post("/todo/delete_task", headers=auth(token), json={"task_id": "42"})
    assert response.json() == {"deleted": "42"}
    assert client.get("/todo/delete_task?task_id=42", headers=auth(token)).status_code == 405
    assert client.post("/todo/tasks", headers=auth(token)).status_code == 405


def test_write_action_on_read_only_provider_is_refused():
    provider = Provider("ro", "RO", writes=False, instructions="")
    with pytest.raises(ValueError):
        provider.action(write=True)(lambda: None)


# ── MCP ──────────────────────────────────────────────────────────────────────


def rpc(client, token, path, method, params=None, id=1):
    body = {"jsonrpc": "2.0", "id": id, "method": method}
    if params is not None:
        body["params"] = params
    response = client.post(path, headers={**MCP_HEADERS, **auth(token)}, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def test_mcp_tools_mirror_actions(client, token):
    init = rpc(client, token, "/demo", "initialize", {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"},
    })
    assert init["result"]["serverInfo"]["name"] == "demo"
    assert init["result"]["instructions"] == "Тестовый поставщик."

    tools = {t["name"]: t for t in rpc(client, token, "/demo", "tools/list")["result"]["tools"]}
    assert set(tools) == {"items", "expired", "down", "broken"}
    assert tools["items"]["description"] == "Элементы демо."
    assert set(tools["items"]["inputSchema"]["properties"]) == {"since", "ids", "limit"}
    assert tools["items"]["annotations"]["readOnlyHint"] is True

    write_tools = {t["name"]: t for t in rpc(client, token, "/todo", "tools/list")["result"]["tools"]}
    assert write_tools["delete_task"]["annotations"]["destructiveHint"] is True


def test_mcp_call_and_errors(client, app, token):
    app.state.store.set_secret("demo", "token", "t-1")
    result = rpc(client, token, "/demo", "tools/call", {"name": "items", "arguments": {"limit": 3}})["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"])[0]["limit"] == 3

    result = rpc(client, token, "/demo", "tools/call", {"name": "down", "arguments": {}})["result"]
    assert result["isError"] is True
    assert result["content"][0]["text"] == "VPN Авито отвалился — введи код на motor.gang.su"

    status = rpc(client, token, "/motor", "tools/call", {"name": "status", "arguments": {}})["result"]
    assert json.loads(status["content"][0]["text"])["consumer"] == "мак"


# ── page ─────────────────────────────────────────────────────────────────────


def test_page_needs_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/login"
    assert "Неверный пароль" in client.post("/login", data={"password": "nope"}).text
    response = client.post("/login", data={"password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303 and "motor_session" in response.headers["set-cookie"]
    assert "Поставщики" in client.get("/").text


def test_page_without_password_says_how_to_set_it(tmp_path):
    config = Config(host="127.0.0.1", port=0, data_dir=tmp_path, password_hash=None, session_secret=b"s" * 32)
    with TestClient(create_app(config, make_providers()), base_url="https://motor.gang.su") as client:
        assert "MOTOR_PASSWORD_HASH" in client.get("/login").text
        assert client.post("/login", data={"password": ""}).status_code == 200
        assert client.get("/", follow_redirects=False).status_code == 303


def test_consumer_is_issued_once_then_revoked(client, app):
    client.post("/login", data={"password": PASSWORD})
    page = client.post("/page/consumers", data={"name": "recaps"}).text
    token = page.split("<div class=token>")[1].split("</div>")[0]
    assert token.startswith("motor_")
    assert token not in client.get("/").text
    assert client.get("/motor/status", headers=auth(token)).json()["consumer"] == "recaps"
    assert "уже есть" in client.post("/page/consumers", data={"name": "recaps"}).text

    consumer_id = app.state.store.consumers()[0].id
    client.post(f"/page/consumers/{consumer_id}/revoke")
    assert client.get("/motor/status", headers=auth(token)).status_code == 401


def test_secret_is_saved_checked_and_never_shown(client, app):
    client.post("/login", data={"password": PASSWORD})
    client.post("/page/providers/demo", data={"secret.token": "very-secret-value"})
    assert app.state.store.secret("demo", "token") == "very-secret-value"
    assert app.state.store.provider_state("demo").status == "ok"  # the check ran
    page = client.get("/").text
    assert "very-secret-value" not in page
    assert "задан" in page


def test_cross_site_post_is_refused(client, app):
    client.post("/login", data={"password": PASSWORD})
    response = client.post("/page/consumers", data={"name": "evil"}, headers={"Origin": "https://evil.example"})
    assert response.status_code == 403
    assert app.state.store.consumers() == []


def test_login_is_throttled(client):
    for _ in range(5):
        client.post("/login", data={"password": "nope"})
    assert "Слишком много попыток" in client.post("/login", data={"password": PASSWORD}).text
