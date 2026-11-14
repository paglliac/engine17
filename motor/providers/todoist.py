"""`todoist` — задачи Todoist. Чтение и запись (удаление).

Перенесено из хаба (`~/SecondBrain-new/server/collectors/todoist.py`): API v1, Bearer-токен,
«Входящие» — проект с `inbox_project`, удалённая до нас задача (404) — не ошибка. Отличия:
листание `next_cursor` до `limit` (хаб брал только первую страницу), ошибки — ошибки мотора.
Задачи отдаются как их отдаёт Todoist: хабу (Х10) нужен весь объект — он кладёт его в источник.
"""
from __future__ import annotations

import os
from urllib.parse import quote

import httpx

from ..errors import BadRequest, NotFound
from ..provider import Provider, Secret

URL = os.environ.get("MOTOR_TODOIST_URL", "https://api.todoist.com/api/v1").rstrip("/")
TIMEOUT = 30
PAGE = 200  # Todoist's own maximum per page

# For tests: an httpx transport to use instead of the network.
transport: httpx.BaseTransport | None = None


def _check() -> None:
    _get("/projects", {"limit": 1})


todoist = Provider(
    "todoist",
    "Todoist",
    writes=True,
    instructions=(
        "Todoist пользователя: проекты, задачи проекта (по умолчанию — «Входящие») и удаление задачи. "
        "Удаление необратимо — только когда пользователь просил или задача уже разобрана."
    ),
    secrets=[Secret(
        "token", "API-токен",
        hint="app.todoist.com → Настройки → Интеграции → Для разработчиков → API-токен",
    )],
    check=_check,
)


def _request(method: str, path: str, params: dict | None = None) -> httpx.Response:
    headers = {"Authorization": f"Bearer {todoist.secret()}"}
    try:
        with httpx.Client(base_url=URL, timeout=TIMEOUT, transport=transport) as http:
            resp = http.request(method, path, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise todoist.down(f"Todoist не ответил за {TIMEOUT} с — попробуй позже") from exc
    except httpx.TransportError as exc:
        raise todoist.down(f"Todoist недоступен ({type(exc).__name__}) — попробуй позже") from exc
    if resp.status_code in (401, 403):
        raise todoist.expired()
    if resp.status_code == 429 or resp.status_code >= 500:
        raise todoist.down(f"Todoist ответил {resp.status_code} — попробуй позже")
    return resp


def _get(path: str, params: dict | None = None):
    resp = _request("GET", path, params)
    if resp.status_code == 404:
        raise NotFound(f"в Todoist нет такого: {path}")
    if resp.status_code >= 400:
        raise BadRequest(f"Todoist отказал: {path} → {resp.status_code} {resp.text[:200]}")
    return resp.json()


def _all(path: str, params: dict, limit: int) -> list[dict]:
    """Follow `next_cursor` until `limit` items or the end."""
    out: list[dict] = []
    cursor = None
    while len(out) < limit:
        page = _get(path, {**params, "limit": min(PAGE, limit - len(out)), **({"cursor": cursor} if cursor else {})})
        out.extend(page.get("results") or [])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    return out[:limit]


def _inbox_id() -> str:
    for project in _all("/projects", {}, 1000):
        if project.get("inbox_project"):
            return str(project["id"])
    raise todoist.down("в Todoist не нашлось проекта «Входящие» — странно, проверь аккаунт")


def _limit(value: int) -> int:
    if value < 1:
        raise BadRequest("limit: не меньше 1")
    return value


# ── действия ─────────────────────────────────────────────────────────────────

@todoist.action
def status() -> dict:
    """Проверить токен Todoist: сколько проектов и какой из них «Входящие». Звать первым, когда
    другое действие отвечает ошибкой токена."""
    projects = _all("/projects", {}, 1000)
    inbox = next((p for p in projects if p.get("inbox_project")), None)
    return {"projects": len(projects), "inbox": {"id": str(inbox["id"]), "name": inbox.get("name")} if inbox else None}


@todoist.action
def projects(limit: int = 100) -> dict:
    """Проекты: id, name, inbox (это «Входящие»), parent_id, url."""
    found = _all("/projects", {}, _limit(limit))
    return {
        "count": len(found),
        "projects": [{"id": str(p.get("id")), "name": p.get("name"), "inbox": bool(p.get("inbox_project")),
                      "parent_id": p.get("parent_id"), "url": p.get("url") or ""} for p in found],
    }


@todoist.action
def tasks(project: str = "inbox", limit: int = 100) -> dict:
    """Открытые задачи одного проекта — объекты Todoist как есть (id, content, description, due,
    labels, priority, added_at, url, …).

    project — id проекта из `projects` или "inbox" («Входящие», по умолчанию); limit — сколько (100)."""
    project_id = _inbox_id() if project.strip().lower() == "inbox" else project.strip()
    found = _all("/tasks", {"project_id": project_id}, _limit(limit))
    for task in found:
        task.setdefault("url", f"https://app.todoist.com/app/task/{task.get('id')}")
    return {"project_id": project_id, "count": len(found), "tasks": found}


@todoist.action(write=True)
def delete_task(id: str) -> dict:
    """Удалить задачу Todoist насовсем (не «выполнить»). Задача, которой уже нет, — не ошибка:
    existed: false.

    id — id задачи из `tasks`."""
    task_id = id.strip()
    if not task_id:
        raise BadRequest("id: пустой")
    resp = _request("DELETE", f"/tasks/{quote(task_id, safe='')}")
    if resp.status_code == 404:
        return {"ok": True, "id": task_id, "existed": False}
    if resp.status_code >= 400:
        raise BadRequest(f"Todoist не удалил задачу {task_id}: {resp.status_code} {resp.text[:200]}")
    return {"ok": True, "id": task_id, "existed": True}
