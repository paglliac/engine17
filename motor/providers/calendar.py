"""`calendar` — Exchange-календарь (exchange.avito.ru, EWS). Только чтение.

Код взят из хаба (`~/SecondBrain-new/server/api/calendar.py`: `exchangelib`, `DELEGATE`, без
autodiscover), но без снапшота: мотор — тупой прокси, каждый запрос читает Exchange живьём.
Копия «раз в 5 минут» для дашборда остаётся у хаба (Х8).

Только факты о встречах: кто, когда, где, мой ответ. Слаги людей, «1-1 или группа», повестки —
дело потребителя (повестки живут в хабе, а не в Exchange). Отменённые встречи не отдаются.

Exchange не пускает NL-адрес — `exchange.avito.ru` стоит в `direct` мотора (infra).
"""
from __future__ import annotations

import datetime
import os
import threading
import time
from typing import Any
from zoneinfo import ZoneInfo

from ..errors import BadRequest
from ..provider import Provider, Secret

SERVER = os.environ.get("MOTOR_EWS_SERVER", "exchange.avito.ru")
TZ = ZoneInfo(os.environ.get("MOTOR_TZ", "Europe/Moscow"))
TIMEOUT = 30          # секунд на запрос к Exchange (у exchangelib по умолчанию 120 — больше, чем ждёт MCP)
ACCOUNT_TTL = 3600    # раз в час учётка собирается заново
MAX_DAYS = 62         # самый длинный диапазон в одном запросе
MAX_ATTENDEES = 30    # участников на встречу; у общих встреч их сотни
FIELDS = (
    "subject", "start", "end", "is_all_day", "location", "organizer", "required_attendees",
    "optional_attendees", "is_cancelled", "my_response_type", "uid", "recurrence_id",
)


def _check() -> None:
    _view(*_window(_today(), _today()))


calendar = Provider(
    "calendar",
    "Календарь",
    writes=False,
    instructions=(
        "Exchange-календарь Авито (exchange.avito.ru) пользователя, только чтение: встречи за день или "
        "диапазон дат, ближайшие встречи, встречи с конкретным человеком. Время — Москва. "
        "Повесток здесь нет — они в хабе."
    ),
    secrets=[
        Secret("email", "Почта", hint="адрес ящика в Exchange, например имя@avito.ru"),
        Secret("username", "Логин", hint="логин учётки Авито для Exchange (как EWS_USERNAME в хабе)"),
        Secret("password", "Пароль", hint="пароль учётки Авито"),
    ],
    check=_check,
)


# ── учётка ───────────────────────────────────────────────────────────────────

# One account per credentials, rebuilt when the page gets new ones or the account is an hour old.
_lock = threading.Lock()
_current: tuple[tuple[str, str, str], float, Any] | None = None


def _account():
    global _current
    creds = (calendar.secret("email"), calendar.secret("username"), calendar.secret("password"))
    with _lock:
        if _current is None or _current[0] != creds or time.monotonic() - _current[1] > ACCOUNT_TTL:
            _current = (creds, time.monotonic(), _connect(*creds))
        return _current[2]


def _connect(email: str, username: str, password: str):
    from exchangelib import DELEGATE, Account, Configuration, Credentials, EWSTimeZone
    from exchangelib.protocol import BaseProtocol

    BaseProtocol.TIMEOUT = TIMEOUT
    config = Configuration(server=SERVER, credentials=Credentials(username=username, password=password))
    return Account(
        primary_smtp_address=email, config=config, autodiscover=False, access_type=DELEGATE,
        default_timezone=EWSTimeZone(TZ.key),
    )


def _view(start: datetime.datetime, end: datetime.datetime) -> list:
    """Calendar items overlapping [start, end), recurring meetings expanded; Exchange errors → motor errors."""
    from exchangelib import errors as ews

    try:
        account = _account()
        from exchangelib import EWSDateTime

        return list(account.calendar.view(
            start=EWSDateTime.from_datetime(start), end=EWSDateTime.from_datetime(end),
        ).only(*FIELDS))
    except ews.UnauthorizedError as exc:
        raise calendar.expired(
            "Exchange не принял логин или пароль (или учётка заблокирована) — проверь их на motor.gang.su"
        ) from exc
    except (ews.ErrorNonExistentMailbox, ews.ErrorAccessDenied, ews.ErrorImpersonateUserDenied) as exc:
        raise calendar.expired(
            f"Exchange не пускает в ящик ({type(exc).__name__}) — проверь почту на motor.gang.su"
        ) from exc
    except (ews.TransportError, ews.ErrorTimeoutExpired, ews.ErrorServerBusy, ews.ErrorConnectionFailed,
            ews.ErrorExceededConnectionCount, ews.CASError) as exc:
        raise calendar.down(
            f"Exchange не отвечает ({type(exc).__name__}) — попробуй позже; если долго — "
            "проверь, что exchange.avito.ru в direct мотора"
        ) from exc


# ── форма ответа ─────────────────────────────────────────────────────────────

def _person(mailbox) -> dict | None:
    if mailbox is None:
        return None
    email = (getattr(mailbox, "email_address", None) or "").lower()
    name = getattr(mailbox, "name", None) or ""
    if not email and not name:
        return None
    return {"email": email, "name": name} if name else {"email": email}


def _when(value) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.astimezone(TZ).isoformat(timespec="minutes")
    return value.isoformat()  # all-day: a date, the end inclusive


def _event(item, me: str) -> dict:
    attendees = []
    for attendee_list, optional in ((item.required_attendees, False), (item.optional_attendees, True)):
        for attendee in attendee_list or []:
            person = _person(attendee.mailbox)
            if person is None or person["email"] == me:
                continue
            if optional:
                person["optional"] = True
            attendees.append(person)
    event = {
        "subject": item.subject or "(без названия)",
        "start": _when(item.start),
        "end": _when(item.end),
        "all_day": bool(item.is_all_day),
        "location": str(item.location) if item.location else "",
        "organizer": _person(item.organizer),
        "attendees": attendees[:MAX_ATTENDEES],
        "attendees_total": len(attendees),
        "response": str(item.my_response_type or "").lower(),
        # uid + recurrence_id name one occurrence across moves and edits; `id` changes when it's moved.
        "uid": str(item.uid or ""),
        "recurrence_id": _when(item.recurrence_id) if item.recurrence_id else "",
        "id": str(item.id or ""),
    }
    return event


def _matches(event: dict, who: str) -> bool:
    who = who.lower()
    people = [*event["attendees"], event["organizer"] or {}]
    return any(who in p.get("email", "") or who in p.get("name", "").lower() for p in people)


def _events(start: datetime.datetime, end: datetime.datetime, attendee: str) -> list[dict]:
    items = _view(start, end)
    me = (calendar.secret("email") or "").lower()
    events = [_event(item, me) for item in items if not item.is_cancelled]
    if attendee.strip():
        events = [e for e in events if _matches(e, attendee.strip())]
    events.sort(key=lambda e: (e["start"] if not e["all_day"] else e["start"] + "T00:00", e["subject"]))
    return events


# ── параметры ────────────────────────────────────────────────────────────────

def _now() -> datetime.datetime:
    return datetime.datetime.now(TZ)


def _today() -> datetime.date:
    return _now().date()


def _date(value: str | None, name: str) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value.strip()[:10])
    except ValueError as exc:
        raise BadRequest(f"{name}: дата в виде ГГГГ-ММ-ДД, а не {value!r}") from exc


def _window(first: datetime.date, last: datetime.date) -> tuple[datetime.datetime, datetime.datetime]:
    """Whole Moscow days, `last` included."""
    start = datetime.datetime.combine(first, datetime.time.min, TZ)
    end = datetime.datetime.combine(last + datetime.timedelta(days=1), datetime.time.min, TZ)
    return start, end


def _limit(value: int) -> int:
    if value < 1:
        raise BadRequest("limit: не меньше 1")
    return value


# ── действия ─────────────────────────────────────────────────────────────────

@calendar.action
def status() -> dict:
    """Проверить связь с Exchange: чей ящик, какой сервер, сколько встреч сегодня. Звать первым,
    когда другое действие отвечает ошибкой."""
    today = _today()
    return {
        "email": calendar.secret("email"),
        "server": SERVER,
        "timezone": TZ.key,
        "today": today.isoformat(),
        "today_count": len(_events(*_window(today, today), "")),
    }


@calendar.action
def day(date: str | None = None, to: str | None = None, attendee: str = "", limit: int = 30) -> dict:
    """Встречи за день или диапазон дат (включительно), по времени начала. Встреча попадает, если
    хоть как-то задевает эти дни (отпуск с позавчера — тоже).

    Встреча: subject, start, end (ISO с поясом Москвы; у встреч на весь день — даты, end
    включительно), all_day, location, organizer, attendees (кроме меня; первые 30, всего —
    attendees_total; optional у необязательных), response — мой ответ (accept, tentative, decline,
    noresponsereceived, organizer, unknown), uid + recurrence_id — одно повторение встречи
    (не меняется при переносе), id. Пустой список — правда свободный день: календарь читается живьём.

    date — ГГГГ-ММ-ДД, по умолчанию сегодня; to — последний день диапазона (до 62 дней);
    attendee — часть почты или имени участника/организатора («ivanov», «Иванов»)."""
    first = _date(date, "date") or _today()
    last = _date(to, "to") or first
    if last < first:
        first, last = last, first
    if (last - first).days >= MAX_DAYS:
        raise BadRequest(f"диапазон не длиннее {MAX_DAYS} дней")
    events = _events(*_window(first, last), attendee)[:_limit(limit)]
    return {"date": first.isoformat(), "to": last.isoformat(), "count": len(events), "events": events}


@calendar.action
def upcoming(days: int = 7, attendee: str = "", limit: int = 30) -> dict:
    """Встречи, которые ещё не закончились, ближайшие первыми — за `days` дней вперёд, считая
    сегодня (до 62). attendee — часть почты или имени, например чтобы найти следующую 1-1 с
    человеком. Форма встречи — как у `day`."""
    if not 1 <= days <= MAX_DAYS:
        raise BadRequest(f"days: от 1 до {MAX_DAYS}")
    now = _now()
    today = now.date()
    events = _events(*_window(today, today + datetime.timedelta(days=days - 1)), attendee)
    now_iso = now.isoformat(timespec="minutes")
    today_iso = today.isoformat()
    events = [e for e in events if (e["end"] >= today_iso if e["all_day"] else e["end"] > now_iso)]
    events = events[:_limit(limit)]
    return {"now": now_iso, "count": len(events), "events": events}
