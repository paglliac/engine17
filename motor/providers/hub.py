"""The hub (SecondBrain) as a supplier: one HTTP client for `tracker`, `people`, `inbox`, `triage`.

The motor goes to the hub's HTTP API (`/api/...`) with the hub's API token (`SB_API_TOKEN`),
entered once on the page — in the `tracker` field; the other three read it from there.
The actions themselves are ported from the hub's own MCP wrappers
(`~/SecondBrain-new/server/mcp/{tracker,people,inbox,triage,sb_calendar}.py`): same names,
parameters, docstrings and bodies; the hub owns all logic, the motor only forwards.

Errors: the hub's 4xx text goes back as is (it often says which tool to use instead).
"""
from __future__ import annotations

import os

import httpx

from ..errors import PAGE, BadRequest, NotFound, TokenExpired
from ..provider import Provider

URL = os.environ.get("MOTOR_HUB_URL", "https://gang.su").rstrip("/")
# Below the 60 s Claude Code waits for an MCP answer: better our «не ответил», than its timeout.
TIMEOUT = 55

# For tests: an httpx transport to use instead of the network.
transport: httpx.BaseTransport | None = None

TOKEN_OWNER = "tracker"  # the provider whose page field holds the hub token
_client: httpx.Client | None = None


def _http() -> httpx.Client:
    global _client
    if _client is None:
        _client = httpx.Client(base_url=URL, timeout=TIMEOUT, transport=transport)
    return _client


def token(provider: Provider) -> str:
    """The hub token from the `tracker` field — one for all four hub providers."""
    value = provider._store().secret(TOKEN_OWNER, "token")
    if not value:
        raise TokenExpired(f"токена хаба нет — вставь SB_API_TOKEN на {PAGE} в поле «Трекер»")
    return value


def call(provider: Provider, method: str, path: str, *, params: dict | None = None, body=None):
    """`method /api<path>` on the hub → its JSON; failures → motor errors."""
    headers = {"Authorization": f"Bearer {token(provider)}"}
    if params:
        params = {k: v for k, v in params.items() if v is not None}
    try:
        resp = _http().request(method, f"/api{path}", params=params, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise provider.down(f"хаб не ответил за {TIMEOUT} с — попробуй позже") from exc
    except httpx.TransportError as exc:
        raise provider.down(f"хаб недоступен ({type(exc).__name__}) — попробуй позже") from exc
    if resp.status_code == 401:
        raise TokenExpired(f"хаб не принял токен — вставь новый SB_API_TOKEN на {PAGE} в поле «Трекер»")
    if resp.status_code >= 500:
        raise provider.down(f"хаб ответил {resp.status_code} — попробуй позже")
    if resp.status_code >= 400:
        refused = NotFound if resp.status_code == 404 else BadRequest
        raise refused(f"хаб отказал ({resp.status_code}): {_hub_error(resp)}")
    return resp.json() if resp.content else {}


def _hub_error(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:500] or resp.reason_phrase
    if isinstance(body, dict) and body.get("error"):
        return str(body["error"])
    return str(body)[:500]
