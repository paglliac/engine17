"""`ktalk` — записи встреч и их расшифровки в avito.ktalk.ru. Только чтение.

Клиент перенесён из хаба (`~/SecondBrain-new/server/collectors/ktalk.py`): те же запросы
(`/api/recordings`, `/api/recordings/<id>/transcript`), те же заголовки и cookie, тот же разбор
«Copy as cURL» и та же лента расшифровки «[мм:сс] Говорящий: текст». Без файлов и индекса хаба:
мотор отдаёт живьём, что лежит в ktalk; копии расшифровок — дело хаба.

ktalk не пускает NL-адрес — `avito.ktalk.ru` стоит в `direct` мотора (infra).
"""
from __future__ import annotations

import datetime
import os
import re
import shlex
import time

import httpx

from ..errors import BadRequest, NotFound
from ..provider import Provider, Secret

URL = os.environ.get("MOTOR_KTALK_URL", "https://avito.ktalk.ru").rstrip("/")
TIMEOUT = 30
PAGE_SIZE = 50
MAX_PAGES = 8          # не дальше 400 записей назад за один запрос
MAX_DAYS = 90

# For tests: an httpx transport to use instead of the network.
transport: httpx.BaseTransport | None = None

# Cookie names of a ktalk session (the hub keeps them as KTALK_TOKEN / KTALK_NGTOKEN / KTALK_KONTUR_NGTOKEN).
_COOKIES = ("sessionToken", "ngtoken", "kontur_ngtoken")
_READY = {"complete", "completed", "done", "ready", "success"}


def _check() -> None:
    _recordings_page(0, 1)


ktalk = Provider(
    "ktalk",
    "ktalk",
    writes=False,
    instructions=(
        "Записи встреч Авито в ktalk (avito.ktalk.ru), только чтение: список записей за последние дни "
        "и расшифровка одной встречи целиком — лента «[время] Говорящий: текст»."
    ),
    secrets=[Secret(
        "curl", "Copy as cURL из ktalk",
        hint="avito.ktalk.ru → записи → DevTools → Network → любой запрос к /api → Copy as cURL; "
             "годится и голый заголовок Cookie",
        multiline=True,
    )],
    check=_check,
)


# ── вход ─────────────────────────────────────────────────────────────────────

def session_from_curl(text: str) -> dict[str, str]:
    """The ktalk session cookies from a «Copy as cURL» paste (or a bare Cookie header).

    `sessionToken` falls back to `Authorization: Session <token>` when the paste carries no such cookie.
    """
    text = (text or "").strip()
    parts: list[str] = []
    auth = ""
    if text.lower().startswith("curl"):
        try:
            args = shlex.split(text.replace("\\\n", " "))
        except ValueError:
            args = []
        for flag, value in zip(args, args[1:]):
            if flag in ("-b", "--cookie"):
                parts.append(value)
            elif flag in ("-H", "--header") and ":" in value:
                name, _, rest = value.partition(":")
                if name.strip().lower() == "cookie":
                    parts.append(rest)
                elif name.strip().lower() == "authorization":
                    auth = rest.strip()
    else:
        parts.append(re.sub(r"^cookie:\s*", "", text, flags=re.IGNORECASE))
    jar: dict[str, str] = {}
    for part in ";".join(parts).split(";"):
        name, eq, value = part.strip().partition("=")
        if eq:
            jar[name.strip()] = value.strip()
    found = {name: jar[name] for name in _COOKIES if jar.get(name)}
    if "sessionToken" not in found and auth.lower().startswith("session "):
        found["sessionToken"] = auth.split(None, 1)[1].strip()
    return found


def _get(path: str, params: dict | None = None):
    cookies = session_from_curl(ktalk.secret("curl"))
    token = cookies.get("sessionToken")
    if not token:
        raise ktalk.expired("во вставке нет sessionToken — вставь «Copy as cURL» запроса к avito.ktalk.ru/api "
                            "на motor.gang.su")
    headers = {
        "Authorization": f"Session {token}",
        "Accept": "application/json",
        "Referer": f"{URL}/content/artifacts/recordings",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
        "X-Platform": "web",
        "Cookie": "; ".join(f"{k}={v}" for k, v in cookies.items()),
    }
    try:
        with httpx.Client(base_url=f"{URL}/api", timeout=TIMEOUT, transport=transport) as http:
            resp = http.get(path, params=params, headers=headers)
    except httpx.TimeoutException as exc:
        raise ktalk.down(f"ktalk не ответил за {TIMEOUT} с — попробуй позже; если долго — проверь, что "
                         "avito.ktalk.ru в direct мотора") from exc
    except httpx.TransportError as exc:
        raise ktalk.down(f"ktalk недоступен ({type(exc).__name__}) — попробуй позже") from exc
    if resp.status_code in (401, 403):
        raise ktalk.expired("сессия ktalk протухла — вставь свежий «Copy as cURL» на motor.gang.su")
    if resp.status_code == 404:
        raise NotFound(f"в ktalk нет такого: {path}")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise ktalk.down(f"ktalk ответил {resp.status_code} — попробуй позже")
    if resp.status_code >= 400:
        raise BadRequest(f"ktalk отказал: {path} → {resp.status_code} {resp.text[:200]}")
    return resp.json()


# ── записи ───────────────────────────────────────────────────────────────────

def _recordings_page(skip: int, top: int) -> list[dict]:
    data = _get("/recordings", {"skip": skip, "top": top, "initiator": "false"})
    if isinstance(data, list):
        return data
    return data.get("recordings", data.get("items", data.get("data", []))) or []


def _shape(rec: dict) -> dict:
    meeting_id = str(rec.get("id") or rec.get("meetingId") or "")
    return {
        "id": meeting_id,
        "title": rec.get("title") or rec.get("name") or meeting_id,
        "date": rec.get("createdDate") or rec.get("startedAt") or rec.get("date") or "",
        "url": f"{URL}/recordings/{meeting_id}",
    }


def _day(value: str, name: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise BadRequest(f"{name}: дата в виде ГГГГ-ММ-ДД, а не {value!r}") from exc


def _clock(ms: int) -> str:
    total = int(ms / 1000)
    h, m, s = total // 3600, (total % 3600) // 60, total % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _timeline(transcript: dict) -> tuple[str, list[str]]:
    chunks, speakers = [], []
    for track in transcript.get("tracks") or []:
        info = (track.get("speaker") or {}).get("userInfo") or {}
        speaker = f"{info.get('firstname', '')} {info.get('surname', '')}".strip() or "?"
        if speaker not in speakers:
            speakers.append(speaker)
        for chunk in track.get("chunks") or []:
            start = chunk.get("startTimeOffsetInMillis") or chunk.get("timeOffsetInMillis") or 0
            chunks.append((start, speaker, chunk.get("text", "")))
    chunks.sort(key=lambda c: c[0])
    return "\n".join(f"[{_clock(ms)}] {who}: {text}" for ms, who, text in chunks), speakers


# ── действия ─────────────────────────────────────────────────────────────────

@ktalk.action
def status() -> dict:
    """Проверить вход в ktalk: отвечает ли и какая запись последняя. Звать первым, когда другое
    действие отвечает ошибкой токена."""
    latest = _recordings_page(0, 1)
    return {"server": URL, "latest": _shape(latest[0]) if latest else None}


@ktalk.action
def recordings(since: str | None = None, days: int = 7, limit: int = 30) -> dict:
    """Записи встреч, новые сверху: id, title, date (как отдаёт ktalk), url.

    since — с какого дня, ГГГГ-ММ-ДД; без него — за последние `days` дней (до 90), считая сегодня.
    limit — сколько записей (по умолчанию 30). Расшифровка одной встречи — `transcript` с её id."""
    if limit < 1:
        raise BadRequest("limit: не меньше 1")
    if since:
        first = _day(since, "since")
    else:
        if not 1 <= days <= MAX_DAYS:
            raise BadRequest(f"days: от 1 до {MAX_DAYS}")
        first = datetime.date.today() - datetime.timedelta(days=days - 1)
    first_str = first.isoformat()
    found: list[dict] = []
    for page in range(MAX_PAGES):
        batch = _recordings_page(page * PAGE_SIZE, PAGE_SIZE)
        shaped = [_shape(r) for r in batch]
        found.extend(r for r in shaped if r["id"] and (r["date"][:10] or "0") >= first_str)
        if len(found) >= limit or len(batch) < PAGE_SIZE or all((r["date"][:10] or "0") < first_str for r in shaped):
            break
        time.sleep(0.3)
    found = found[:limit]
    return {"since": first_str, "count": len(found), "recordings": found}


@ktalk.action
def transcript(id: str) -> dict:
    """Расшифровка одной встречи целиком: text — лента «[мм:сс] Имя Фамилия: текст» по времени,
    speakers — кто говорил. Пока ktalk не закончил распознавание — ready: false и пустой text
    (это не ошибка: зайди позже).

    id — id записи из `recordings` (или из ссылки avito.ktalk.ru/recordings/<id>)."""
    meeting_id = id.strip().rstrip("/").rsplit("/", 1)[-1]
    if not meeting_id:
        raise BadRequest("id: пустой")
    data = _get(f"/recordings/{meeting_id}/transcript")
    if not isinstance(data, dict):
        raise BadRequest("ktalk вернул расшифровку в непонятном виде")
    state = str(data.get("status") or "").lower()
    ready = state in _READY if state else bool(data.get("tracks"))
    text, speakers = _timeline(data) if ready else ("", [])
    return {
        "id": meeting_id,
        "url": f"{URL}/recordings/{meeting_id}",
        "ready": ready,
        "status": data.get("status") or "",
        "speakers": speakers,
        "text": text,
    }
