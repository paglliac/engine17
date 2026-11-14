"""`family` — семейный календарь Google (общий с женой). Чтение и запись: создать, изменить/перенести, удалить.

Вход — служебная учётка Google: её JSON-ключ и id календаря вводятся на страничке, календарь расшарен
на адрес учётки с правом «Вносить изменения в мероприятия». Так нет OAuth-входа и он не протухает;
приглашений участникам служебная учётка не рассылает (и не пытается: `sendUpdates=none`).

Google Calendar API v3 по REST через httpx; токен доступа выпускает `google-auth` из ключа и сам
обновляет его, когда истёк. Время — Москва (как у `calendar`); встречи на весь день — даты, `end`
включительно.
"""
from __future__ import annotations

import datetime
import json
import os
import threading
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import httpx

from ..errors import BadRequest, NotFound
from ..provider import Provider, Secret

API = os.environ.get("MOTOR_GCAL_URL", "https://www.googleapis.com/calendar/v3").rstrip("/")
TZ = ZoneInfo(os.environ.get("MOTOR_TZ", "Europe/Moscow"))
TIMEOUT = 30
SCOPE = "https://www.googleapis.com/auth/calendar.events"
MAX_DAYS = 366
DEFAULT_MINUTES = 60

# For tests: an httpx transport to use instead of the network, and a token source.
transport: httpx.BaseTransport | None = None


def _check() -> None:
    _calendar_info()


family = Provider(
    "family",
    "Семейный календарь",
    writes=True,
    instructions=(
        "Семейный Google-календарь (общий с женой): встречи за день или диапазон, ближайшие, поиск по "
        "тексту; создать, изменить или перенести, удалить встречу. Время — Москва. Удаление необратимо — "
        "только по прямой просьбе."
    ),
    secrets=[
        Secret("key", "JSON-ключ служебной учётки Google", multiline=True,
               hint="Google Cloud → IAM → Service accounts → учётка → Keys → Add key → JSON; вставить файл целиком"),
        Secret("calendar", "Идентификатор календаря",
               hint="Google Calendar → настройки календаря → Интеграция календаря → Идентификатор календаря"),
    ],
    check=_check,
)


# ── вход ─────────────────────────────────────────────────────────────────────

_lock = threading.Lock()
_creds: tuple[str, Any] | None = None  # (key text, google credentials)


def _token() -> str:
    """An access token for the stored key; refreshed by google-auth when it runs out."""
    global _creds
    raw = family.secret("key")
    with _lock:
        if _creds is None or _creds[0] != raw:
            try:
                info = json.loads(raw)
                from google.oauth2 import service_account

                creds = service_account.Credentials.from_service_account_info(info, scopes=[SCOPE])
            except (ValueError, KeyError) as exc:
                raise family.expired("ключ не похож на JSON служебной учётки Google — вставь файл ключа "
                                     "целиком на motor.gang.su") from exc
            _creds = (raw, creds)
        creds = _creds[1]
        if not creds.valid:
            from google.auth import exceptions as gexc
            from google.auth.transport.requests import Request

            try:
                creds.refresh(Request())
            except gexc.RefreshError as exc:
                raise family.expired("Google не принял ключ служебной учётки (удалён или отключён?) — "
                                     "выпусти новый и вставь на motor.gang.su") from exc
            except gexc.TransportError as exc:
                raise family.down("Google не отвечает — попробуй позже") from exc
        return creds.token


def _account() -> str:
    try:
        return json.loads(family.secret("key")).get("client_email", "")
    except ValueError:
        return ""


def _cal() -> str:
    return quote(family.secret("calendar").strip(), safe="")


def _request(method: str, path: str, *, params: dict | None = None, body: dict | None = None):
    headers = {"Authorization": f"Bearer {_token()}"}
    try:
        with httpx.Client(base_url=API, timeout=TIMEOUT, transport=transport) as http:
            resp = http.request(method, path, params=params, json=body, headers=headers)
    except httpx.TimeoutException as exc:
        raise family.down(f"Google Calendar не ответил за {TIMEOUT} с — попробуй позже") from exc
    except httpx.TransportError as exc:
        raise family.down(f"Google Calendar недоступен ({type(exc).__name__}) — попробуй позже") from exc
    return resp


def _json(resp: httpx.Response, what: str):
    if resp.status_code == 401:
        raise family.expired("Google не принял ключ служебной учётки — вставь новый на motor.gang.su")
    if resp.status_code == 403:
        if _reason(resp) in ("rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded"):
            raise family.down("Google Calendar просит притормозить — попробуй через минуту")
        raise family.expired(f"у служебной учётки {_account()} нет права на это в календаре — в настройках "
                             "доступа календаря дай ей «Вносить изменения в мероприятия»")
    if resp.status_code == 429 or resp.status_code >= 500:
        raise family.down(f"Google Calendar ответил {resp.status_code} — попробуй позже")
    if resp.status_code in (404, 410):
        raise NotFound(f"{what}: нет такого ({resp.status_code})")
    if resp.status_code >= 400:
        raise BadRequest(f"Google Calendar отказал ({resp.status_code}): {_message(resp)}")
    return resp.json() if resp.content else {}


def _reason(resp: httpx.Response) -> str:
    try:
        return resp.json()["error"]["errors"][0]["reason"]
    except (ValueError, KeyError, IndexError, TypeError):
        return ""


def _message(resp: httpx.Response) -> str:
    try:
        return str(resp.json()["error"]["message"])[:300]
    except (ValueError, KeyError, TypeError):
        return resp.text[:300]


def _calendar_info() -> dict:
    resp = _request("GET", f"/calendars/{_cal()}")
    if resp.status_code == 404:
        raise family.expired(f"календарь не найден — проверь идентификатор на motor.gang.su и что календарь "
                             f"расшарен на {_account()}")
    return _json(resp, "календарь")


# ── форма ────────────────────────────────────────────────────────────────────

def _when(value: dict | None) -> tuple[str, bool]:
    """Google's {dateTime} or {date} → (Moscow ISO or date, all_day)."""
    value = value or {}
    if value.get("date"):
        return value["date"], True
    raw = value.get("dateTime") or ""
    if not raw:
        return "", False
    return datetime.datetime.fromisoformat(raw).astimezone(TZ).isoformat(timespec="minutes"), False


def _event(ev: dict) -> dict:
    start, all_day = _when(ev.get("start"))
    end, _ = _when(ev.get("end"))
    if all_day and end:
        # Google's all-day end is exclusive; ours is inclusive, like `calendar`.
        end = (datetime.date.fromisoformat(end) - datetime.timedelta(days=1)).isoformat()
    shaped = {
        "id": ev.get("id", ""),
        "title": ev.get("summary") or "(без названия)",
        "start": start,
        "end": end,
        "all_day": all_day,
        "location": ev.get("location") or "",
        "description": ev.get("description") or "",
        "created_by": (ev.get("creator") or {}).get("email", ""),
        "url": ev.get("htmlLink") or "",
    }
    if ev.get("recurringEventId"):
        shaped["recurring"] = True
    return shaped


# ── разбор ввода ─────────────────────────────────────────────────────────────

def _today() -> datetime.date:
    return datetime.datetime.now(TZ).date()


def _date(value: str, name: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise BadRequest(f"{name}: дата в виде ГГГГ-ММ-ДД, а не {value!r}") from exc


def _moment(value: str, name: str) -> datetime.date | datetime.datetime:
    """'2026-11-20' → a date (all day); '2026-11-20T18:00' (Moscow if no offset) → an aware datetime."""
    value = value.strip()
    if len(value) == 10:
        return _date(value, name)
    try:
        moment = datetime.datetime.fromisoformat(value)
    except ValueError as exc:
        raise BadRequest(f"{name}: ГГГГ-ММ-ДД (на весь день) или ГГГГ-ММ-ДДTЧЧ:ММ, а не {value!r}") from exc
    return moment if moment.tzinfo else moment.replace(tzinfo=TZ)


def _google_times(start: str, end: str) -> tuple[dict, dict]:
    begin = _moment(start, "start")
    if isinstance(begin, datetime.datetime):
        finish = _moment(end, "end") if end else begin + datetime.timedelta(minutes=DEFAULT_MINUTES)
        if not isinstance(finish, datetime.datetime):
            raise BadRequest("end: у встречи со временем конец тоже со временем, ГГГГ-ММ-ДДTЧЧ:ММ")
        if finish <= begin:
            raise BadRequest("end раньше start")
        return ({"dateTime": begin.isoformat(), "timeZone": TZ.key},
                {"dateTime": finish.isoformat(), "timeZone": TZ.key})
    last = _moment(end, "end") if end else begin
    if isinstance(last, datetime.datetime):
        raise BadRequest("end: у встречи на весь день конец — тоже дата, ГГГГ-ММ-ДД (включительно)")
    if last < begin:
        raise BadRequest("end раньше start")
    return {"date": begin.isoformat()}, {"date": (last + datetime.timedelta(days=1)).isoformat()}


def _list(first: datetime.datetime, last: datetime.datetime, query: str, limit: int) -> list[dict]:
    if limit < 1:
        raise BadRequest("limit: не меньше 1")
    params = {"timeMin": first.isoformat(), "timeMax": last.isoformat(), "singleEvents": "true",
              "orderBy": "startTime", "maxResults": min(limit, 2500), "timeZone": TZ.key}
    if query.strip():
        params["q"] = query.strip()
    data = _json(_request("GET", f"/calendars/{_cal()}/events", params=params), "календарь")
    return [_event(ev) for ev in data.get("items") or [] if ev.get("status") != "cancelled"][:limit]


def _event_id(id: str) -> str:
    value = id.strip()
    if not value:
        raise BadRequest("id: пустой")
    return quote(value, safe="")


# ── действия ─────────────────────────────────────────────────────────────────

@family.action
def status() -> dict:
    """Проверить доступ к семейному календарю: его название и пояс, под какой служебной учёткой
    мотор ходит. Звать первым, когда другое действие отвечает ошибкой."""
    info = _calendar_info()
    return {"calendar": info.get("summary", ""), "timezone": info.get("timeZone", ""), "account": _account()}


@family.action
def day(date: str | None = None, to: str | None = None, query: str = "", limit: int = 30) -> dict:
    """Встречи семейного календаря за день или диапазон дат (включительно), по времени начала.

    Встреча: id, title, start, end (ISO с поясом Москвы; у встреч на весь день — даты, end
    включительно), all_day, location, description, created_by, url; recurring: true у повторов
    (id повтора — одно это повторение).

    date — ГГГГ-ММ-ДД, по умолчанию сегодня; to — последний день (до года); query — поиск по тексту."""
    first = _date(date, "date") if date else _today()
    last = _date(to, "to") if to else first
    if last < first:
        first, last = last, first
    if (last - first).days >= MAX_DAYS:
        raise BadRequest(f"диапазон не длиннее {MAX_DAYS} дней")
    start = datetime.datetime.combine(first, datetime.time.min, TZ)
    end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time.min, TZ)
    events = _list(start, end, query, limit)
    return {"date": first.isoformat(), "to": last.isoformat(), "count": len(events), "events": events}


@family.action
def upcoming(days: int = 14, query: str = "", limit: int = 30) -> dict:
    """Встречи, которые ещё не закончились, ближайшие первыми — на `days` дней вперёд (до года).
    query — поиск по тексту («врач», «Маша»). Форма встречи — как у `day`."""
    if not 1 <= days <= MAX_DAYS:
        raise BadRequest(f"days: от 1 до {MAX_DAYS}")
    now = datetime.datetime.now(TZ)
    events = _list(now, now + datetime.timedelta(days=days), query, limit)
    return {"now": now.isoformat(timespec="minutes"), "count": len(events), "events": events}


@family.action(write=True)
def create_event(title: str, start: str, end: str = "", location: str = "", description: str = "") -> dict:
    """Создать встречу в семейном календаре. Её сразу видят все, у кого есть календарь.

    start — ГГГГ-ММ-ДДTЧЧ:ММ (Москва, если без пояса) или ГГГГ-ММ-ДД — на весь день;
    end — так же; по умолчанию: час для встречи со временем, тот же день для встречи на весь день
    (у дат end включительно). Возвращает созданную встречу (её id — для правки и удаления)."""
    if not title.strip():
        raise BadRequest("title: пустой")
    begin, finish = _google_times(start, end)
    body = {"summary": title.strip(), "start": begin, "end": finish}
    if location:
        body["location"] = location
    if description:
        body["description"] = description
    created = _json(_request("POST", f"/calendars/{_cal()}/events", params={"sendUpdates": "none"}, body=body),
                    "календарь")
    return {"ok": True, "event": _event(created)}


@family.action(write=True)
def update_event(id: str, title: str | None = None, start: str | None = None, end: str | None = None,
                 location: str | None = None, description: str | None = None) -> dict:
    """Изменить или перенести встречу: меняется только то, что передано.

    Перенос — start (и, если надо, end) в том же виде, что у `create_event`; без end длительность
    сохраняется. У повторяющейся встречи id повтора меняет только это повторение.
    location или description = "" — очистить."""
    event_id = _event_id(id)
    patch: dict = {}
    if title is not None:
        if not title.strip():
            raise BadRequest("title: пустой")
        patch["summary"] = title.strip()
    if location is not None:
        patch["location"] = location
    if description is not None:
        patch["description"] = description
    if start is not None or end is not None:
        current = _json(_request("GET", f"/calendars/{_cal()}/events/{event_id}"), f"встреча {id}")
        if start is None:
            raise BadRequest("перенос — с start; только end поменять: передай и start тоже")
        if end is None:
            end = _keep_duration(current, start)
        patch["start"], patch["end"] = _google_times(start, end)
    if not patch:
        raise BadRequest("нечего менять — передай title, start, end, location или description")
    updated = _json(_request("PATCH", f"/calendars/{_cal()}/events/{event_id}",
                             params={"sendUpdates": "none"}, body=patch), f"встреча {id}")
    return {"ok": True, "event": _event(updated)}


def _keep_duration(current: dict, start: str) -> str:
    """The new end that keeps the event's length (in minutes, or in days for an all-day one)."""
    old_start, old_all_day = _when(current.get("start"))
    old_end, _ = _when(current.get("end"))
    begin = _moment(start, "start")
    if isinstance(begin, datetime.datetime):
        if old_all_day:
            return ""  # all-day → timed: the default hour
        length = datetime.datetime.fromisoformat(old_end) - datetime.datetime.fromisoformat(old_start)
        return (begin + length).isoformat()
    if not old_all_day:
        return ""  # timed → all-day: that day
    days = datetime.date.fromisoformat(old_end) - datetime.date.fromisoformat(old_start)  # Google's end: exclusive
    return (begin + days - datetime.timedelta(days=1)).isoformat()


@family.action(write=True)
def delete_event(id: str) -> dict:
    """Удалить встречу из семейного календаря — необратимо, только по прямой просьбе. Уже удалённая —
    не ошибка. У повторяющейся встречи id повтора удаляет только это повторение."""
    resp = _request("DELETE", f"/calendars/{_cal()}/events/{_event_id(id)}", params={"sendUpdates": "none"})
    if resp.status_code not in (404, 410):
        _json(resp, f"встреча {id}")
    return {"ok": True, "id": id.strip()}
