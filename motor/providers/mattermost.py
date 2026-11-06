"""`mattermost` — сообщения, каналы, сохранённые и люди mt.avito.ru. Только чтение.

Действия — те же 9 на чтение, что у `mattermost_global` (`~/mcp/mattermost/mattermost_mcp.py`),
без префикса `mm_`; отправки нет (`writes=False` → любой POST — 403).

Клиент перенесён из боевого хаба (`~/SecondBrain-new/server/collectors/mattermost_client.py`):
та же сжатая форма сообщений (логины вместо id, permalink, время в ISO) — главный потребитель
LLM с бюджетом на контекст. Отличия: `httpx` вместо `requests`, ошибки — ошибки мотора,
вход только по Personal Access Token (cookie браузера живёт недолго — не берём), записи нет.
"""
from __future__ import annotations

import datetime
import os
import threading
import time

import httpx

from ..errors import BadRequest, NotFound
from ..provider import Provider, Secret

URL = os.environ.get("MOTOR_MM_URL", "https://mt.avito.ru").rstrip("/")
TIMEOUT = 30
# Кэш логинов и названий каналов живёт в клиенте; раз в час клиент собирается заново,
# чтобы переименования не залёживались.
CLIENT_TTL = 3600

# For tests: an httpx transport to use instead of the network.
transport: httpx.BaseTransport | None = None

_TYPES = {"O": "public", "P": "private", "D": "dm", "G": "group"}


def _check() -> None:
    _client().fresh_me()


mattermost = Provider(
    "mattermost",
    "Mattermost",
    writes=False,
    instructions=(
        "Mattermost Авито (mt.avito.ru) от имени пользователя, только чтение: поиск по сообщениям, "
        "треды, история канала, свои каналы, люди, упоминания, непрочитанное, сохранённые. "
        "Ничего не отправляет и ничего не помечает прочитанным или снятым с «сохранено»."
    ),
    secrets=[Secret(
        "token", "Personal Access Token",
        hint="mt.avito.ru → Профиль → Безопасность → Personal Access Tokens (не cookie MMAUTHTOKEN)",
    )],
    check=_check,
)


# ── клиент ───────────────────────────────────────────────────────────────────

# Mattermost refused a request (4xx other than 401/429): about the input, not the provider's health.
Rejected = (BadRequest, NotFound)


def _iso(ms: int | None) -> str:
    """Mattermost timestamps are epoch milliseconds."""
    if not ms:
        return ""
    moment = datetime.datetime.fromtimestamp(ms / 1000, tz=datetime.UTC)
    return moment.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


class Client:
    def __init__(self, base_url: str, token: str, transport: httpx.BaseTransport | None = None):
        self.base = base_url
        self.http = httpx.Client(
            base_url=f"{base_url}/api/v4",
            headers={"Authorization": f"Bearer {token}", "X-Requested-With": "XMLHttpRequest"},
            timeout=TIMEOUT,
            transport=transport,
        )
        # A thread of 40 posts from 5 people would otherwise be 40 user lookups.
        self._users: dict[str, dict] = {}
        self._channels: dict[str, dict] = {}
        self._teams: list[dict] | None = None
        self._me: dict | None = None

    def close(self) -> None:
        self.http.close()

    # ── transport ────────────────────────────────────────────────────────────

    def _request(self, method: str, path: str, params: dict | None = None, json_body=None):
        try:
            resp = self.http.request(method, path, params=params, json=json_body)
        except httpx.TimeoutException as exc:
            raise mattermost.down(f"Mattermost не ответил за {TIMEOUT} с — попробуй позже") from exc
        except httpx.TransportError as exc:
            raise mattermost.down(f"Mattermost недоступен ({type(exc).__name__}) — попробуй позже") from exc
        if resp.status_code == 401:
            raise mattermost.expired()
        if resp.status_code == 429 or resp.status_code >= 500:
            raise mattermost.down(f"Mattermost ответил {resp.status_code} — попробуй позже")
        if resp.status_code >= 400:
            refused = NotFound if resp.status_code in (403, 404) else BadRequest
            raise refused(f"Mattermost отказал: {method} {path} → {resp.status_code} {_mm_message(resp)}")
        return resp.json() if resp.content else {}

    def _get(self, path: str, params: dict | None = None):
        return self._request("GET", path, params=params)

    def _post(self, path: str, json_body=None):
        # Search goes over POST in the Mattermost API; it still only reads.
        return self._request("POST", path, json_body=json_body)

    # ── identity ─────────────────────────────────────────────────────────────

    def fresh_me(self) -> dict:
        self._me = self._get("/users/me")
        return self._me

    def me(self) -> dict:
        return self._me if self._me is not None else self.fresh_me()

    def teams(self) -> list[dict]:
        if self._teams is None:
            self._teams = self._get("/users/me/teams") or []
        return self._teams

    def permalink(self, post_id: str) -> str:
        """Team-agnostic permalink: the server resolves the team itself."""
        return f"{self.base}/_redirect/pl/{post_id}"

    # ── lookups (cached) ─────────────────────────────────────────────────────

    def user(self, user_id: str) -> dict:
        if user_id not in self._users:
            try:
                self._users[user_id] = self._get(f"/users/{user_id}")
            except Rejected:
                self._users[user_id] = {"id": user_id, "username": user_id}
        return self._users[user_id]

    def _username(self, user_id: str) -> str:
        return self.user(user_id).get("username", user_id) if user_id else ""

    def channel(self, channel_id: str) -> dict:
        if channel_id not in self._channels:
            try:
                self._channels[channel_id] = self._get(f"/channels/{channel_id}")
            except Rejected:
                self._channels[channel_id] = {"id": channel_id, "display_name": channel_id}
        return self._channels[channel_id]

    def _channel_label(self, channel_id: str) -> str:
        """Human name for a channel. DMs/group DMs carry no display_name."""
        if not channel_id:
            return ""
        ch = self.channel(channel_id)
        name = ch.get("display_name") or ""
        if name:
            return name
        if ch.get("type") == "D":
            # DM channel name is "<user_id>__<user_id>" — show the other person.
            my_id = self.me().get("id")
            parts = (ch.get("name") or "").split("__")
            other = next((p for p in parts if p != my_id), my_id)
            return f"DM: @{self._username(other)}"
        return ch.get("name") or channel_id

    # ── shaping ──────────────────────────────────────────────────────────────

    def _shape_post(self, post: dict, *, with_channel: bool = True) -> dict:
        out = {
            "post_id": post.get("id"),
            "user": self._username(post.get("user_id", "")),
            "created": _iso(post.get("create_at")),
            "message": post.get("message", ""),
            "permalink": self.permalink(post.get("id", "")),
        }
        if post.get("root_id"):
            out["root_id"] = post["root_id"]
        if with_channel:
            out["channel"] = self._channel_label(post.get("channel_id", ""))
            out["channel_id"] = post.get("channel_id")
        files = (post.get("metadata") or {}).get("files") or []
        if files:
            out["files"] = [f.get("name") for f in files]
        return out

    # ── search ───────────────────────────────────────────────────────────────

    def search(self, terms: str, *, channel: str = "", from_user: str = "",
               after: str = "", before: str = "", limit: int = 30) -> list[dict]:
        """Full-text search across every team; filters become native modifiers."""
        query = terms.strip()
        if channel:
            query = f"in:{channel.lstrip('#~')} {query}".strip()
        if from_user:
            query = f"from:{from_user.lstrip('@')} {query}".strip()
        if after:
            query = f"after:{_day(after, 'after')} {query}".strip()
        if before:
            query = f"before:{_day(before, 'before')} {query}".strip()
        if not query:
            raise BadRequest("пустой запрос — нужны слова или хотя бы один фильтр")

        collected: dict[str, dict] = {}
        for team in self.teams():
            data = self._post(
                f"/teams/{team['id']}/posts/search",
                {"terms": query, "is_or_search": False, "page": 0, "per_page": min(limit, 100)},
            )
            posts = data.get("posts", {}) or {}
            for pid in data.get("order", []) or list(posts):
                if pid in posts:
                    collected[pid] = posts[pid]
        ordered = sorted(collected.values(), key=lambda p: p.get("create_at", 0), reverse=True)
        return [self._shape_post(p) for p in ordered[:limit]]

    def mentions(self, *, days: int = 7, limit: int = 30) -> list[dict]:
        username = self.me().get("username", "")
        after = (datetime.date.today() - datetime.timedelta(days=max(days, 1))).isoformat()
        return self.search(f"@{username}", after=after, limit=limit)

    # ── threads and channel history ──────────────────────────────────────────

    @staticmethod
    def parse_post_id(value: str) -> str:
        """Accept a bare post id or any Mattermost permalink (.../pl/<id>)."""
        value = (value or "").strip()
        if "/pl/" in value:
            value = value.split("/pl/", 1)[1]
        return value.split("?")[0].split("/")[0]

    def thread(self, post: str) -> dict:
        pid = self.parse_post_id(post)
        if not pid:
            raise BadRequest("post: нужен id сообщения или ссылка …/pl/<id>")
        target = self._get(f"/posts/{pid}")
        root_id = target.get("root_id") or pid
        data = self._get(f"/posts/{root_id}/thread", {"skipFetchThreads": "false"})
        posts = data.get("posts", {}) or {pid: target}
        order = sorted(posts.values(), key=lambda p: p.get("create_at", 0))
        channel_id = target.get("channel_id", "")
        return {
            "channel": self._channel_label(channel_id),
            "channel_id": channel_id,
            "root_id": root_id,
            "target_post_id": pid,
            "permalink": self.permalink(pid),
            "count": len(order),
            "posts": [dict(self._shape_post(p, with_channel=False), is_target=(p.get("id") == pid))
                      for p in order],
        }

    def resolve_channel(self, ref: str) -> dict:
        """A channel id, a ~name, or a display name → the channel object."""
        ref = (ref or "").strip().lstrip("#~")
        if not ref:
            raise BadRequest("channel: нужен id, имя или название канала")
        if len(ref) == 26 and ref.isalnum():  # Mattermost ids are 26 chars
            return self.channel(ref)
        for team in self.teams():
            try:
                return self._get(f"/teams/{team['id']}/channels/name/{ref}")
            except Rejected:
                continue
        matches = [c for c in self.my_channels()
                   if ref.lower() in (c.get("display_name") or "").lower()
                   or ref.lower() in (c.get("name") or "").lower()]
        if len(matches) == 1:
            return self.channel(matches[0]["channel_id"])
        if not matches:
            raise NotFound(f"канал {ref!r} не найден среди твоих — посмотри channels")
        names = ", ".join(m.get("display_name") or m.get("name") for m in matches[:8])
        raise BadRequest(f"под {ref!r} подходит несколько каналов: {names} — уточни")

    def channel_posts(self, ref: str, *, limit: int = 30, since: str = "") -> dict:
        ch = self.resolve_channel(ref)
        params: dict = {"per_page": min(limit, 200)}
        if since:
            day = datetime.date.fromisoformat(_day(since, "since"))
            params["since"] = int(datetime.datetime.combine(day, datetime.time.min).timestamp() * 1000)
        data = self._get(f"/channels/{ch['id']}/posts", params)
        posts = data.get("posts", {}) or {}
        order = sorted(posts.values(), key=lambda p: p.get("create_at", 0))[-limit:]
        return {
            "channel": self._channel_label(ch["id"]),
            "channel_id": ch["id"],
            "count": len(order),
            "posts": [self._shape_post(p, with_channel=False) for p in order],
        }

    # ── navigation ───────────────────────────────────────────────────────────

    def my_channels(self, query: str = "") -> list[dict]:
        out: list[dict] = []
        seen: set[str] = set()
        for team in self.teams():
            for ch in self._get(f"/users/me/teams/{team['id']}/channels") or []:
                cid = ch.get("id")
                if not cid or cid in seen:
                    continue
                seen.add(cid)
                self._channels.setdefault(cid, ch)
                out.append({
                    "channel_id": cid,
                    "name": ch.get("name", ""),
                    "display_name": self._channel_label(cid),
                    "type": _TYPES.get(ch.get("type"), ch.get("type")),
                    "team": team.get("display_name") or team.get("name"),
                    "last_post": _iso(ch.get("last_post_at")),
                })
        if query:
            q = query.lower().lstrip("#~")
            out = [c for c in out if q in c["name"].lower() or q in c["display_name"].lower()]
        out.sort(key=lambda c: c["last_post"], reverse=True)
        return out

    def search_users(self, term: str, limit: int = 20) -> list[dict]:
        term = (term or "").strip().lstrip("@")
        if not term:
            raise BadRequest("query: нужен логин или часть имени")
        found = self._post("/users/search", {"term": term, "allow_inactive": False,
                                             "limit": min(limit, 100)}) or []
        return [{
            "user_id": u.get("id"),
            "username": u.get("username"),
            "name": " ".join(x for x in (u.get("first_name"), u.get("last_name")) if x),
            "nickname": u.get("nickname", ""),
            "email": u.get("email", ""),
            "position": u.get("position", ""),
        } for u in found[:limit]]

    def unreads(self) -> list[dict]:
        """Channels with unread messages; muted ones (mark_unread == "mention") sort last."""
        out = []
        for team in self.teams():
            members = self._get(f"/users/me/teams/{team['id']}/channels/members") or []
            by_channel = {m.get("channel_id"): m for m in members}
            for ch in self._get(f"/users/me/teams/{team['id']}/channels") or []:
                m = by_channel.get(ch.get("id"))
                if not m:
                    continue
                unread = (ch.get("total_msg_count", 0) or 0) - (m.get("msg_count", 0) or 0)
                mentions = m.get("mention_count", 0) or 0
                if unread <= 0 and mentions <= 0:
                    continue
                muted = (m.get("notify_props") or {}).get("mark_unread") == "mention"
                self._channels.setdefault(ch["id"], ch)
                out.append({
                    "channel_id": ch["id"],
                    "channel": self._channel_label(ch["id"]),
                    "type": _TYPES.get(ch.get("type"), ch.get("type")),
                    "unread": max(unread, 0),
                    "mentions": mentions,
                    "muted": muted,
                    "last_post": _iso(ch.get("last_post_at")),
                })
        out.sort(key=lambda c: (not c["muted"], c["mentions"], c["unread"]), reverse=True)
        return out

    def saved_posts(self, limit: int = 100) -> list[dict]:
        user_id = self.me().get("id")
        collected: list[dict] = []
        page = 0
        while True:
            data = self._get(f"/users/{user_id}/posts/flagged", {"page": page, "per_page": 200})
            order = data.get("order", []) or []
            posts = data.get("posts", {}) or {}
            collected += [posts[p] for p in order if p in posts]
            if len(order) < 200 or len(collected) >= limit:
                break
            page += 1
        collected.sort(key=lambda p: p.get("create_at", 0), reverse=True)
        return [self._shape_post(p) for p in collected[:limit]]


def _mm_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("message", ""))[:200]
    except (ValueError, AttributeError):
        return resp.text[:200]


def _day(value: str, name: str) -> str:
    try:
        return datetime.date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise BadRequest(f"{name}: дата в виде ГГГГ-ММ-ДД, а пришло {value!r}") from exc


# One client per token, rebuilt when the token on the page changes or the client is an hour old.
_lock = threading.Lock()
_current: tuple[str, float, Client] | None = None


def _client() -> Client:
    global _current
    token = mattermost.secret()
    with _lock:
        if _current is None or _current[0] != token or time.monotonic() - _current[1] > CLIENT_TTL:
            old = _current
            _current = (token, time.monotonic(), Client(URL, token, transport))
            if old is not None:
                old[2].close()
        return _current[2]


def _limit(value: int) -> int:
    if value < 1:
        raise BadRequest("limit: не меньше 1")
    return value


# ── действия ─────────────────────────────────────────────────────────────────

@mattermost.action
def status() -> dict:
    """Проверить связь с Mattermost: под кем мотор ходит и в каких командах. Звать первым,
    когда другое действие отвечает ошибкой токена."""
    client = _client()
    me = client.fresh_me()
    return {
        "url": client.base,
        "me": {
            "user_id": me.get("id"),
            "username": me.get("username"),
            "name": " ".join(x for x in (me.get("first_name"), me.get("last_name")) if x),
        },
        "teams": [t.get("display_name") or t.get("name") for t in client.teams()],
    }


@mattermost.action
def search(query: str, channel: str = "", from_user: str = "", after: str = "", before: str = "",
           limit: int = 30) -> dict:
    """Полнотекстовый поиск по сообщениям во всех командах, новые сверху: автор, время, канал,
    текст, permalink.

    - query — слова поиска, например «беспарольный вход» или «релиз certs»;
    - channel — имя канала, чтобы искать только в нём, например avitoid-dev;
    - from_user — логин автора, например nakhromushkin;
    - after / before — границы по дате, ГГГГ-ММ-ДД;
    - limit — сколько сообщений вернуть (по умолчанию 30)."""
    posts = _client().search(query, channel=channel, from_user=from_user, after=after, before=before,
                             limit=_limit(limit))
    return {"count": len(posts), "posts": posts}


@mattermost.action
def thread(post: str) -> dict:
    """Весь тред, к которому относится сообщение, от старых к новым; запрошенное помечено
    is_target. Чтобы прочитать обсуждение за найденным сообщением или присланной ссылкой.

    - post — id сообщения или любая ссылка Mattermost (…/pl/<id>)."""
    return _client().thread(post)


@mattermost.action
def channel_posts(channel: str, limit: int = 30, since: str = "") -> dict:
    """Последние сообщения одного канала, от старых к новым (в порядке чтения).

    - channel — id, ~имя или название канала, например avitoid-dev;
    - limit — сколько сообщений (по умолчанию 30);
    - since — с какого дня, ГГГГ-ММ-ДД."""
    return _client().channel_posts(channel, limit=_limit(limit), since=since)


@mattermost.action
def channels(query: str = "", limit: int = 100) -> dict:
    """Каналы, где состоит пользователь, недавно активные сверху: id, имя, тип
    (public/private/dm/group), команда. Чтобы найти точное имя канала для channel_posts или search.

    - query — подстрока имени или названия канала;
    - limit — сколько каналов (по умолчанию 100)."""
    found = _client().my_channels(query)[:_limit(limit)]
    return {"count": len(found), "channels": found}


@mattermost.action
def users(query: str, limit: int = 20) -> dict:
    """Найти людей по логину, имени, фамилии или нику: логин, полное имя, должность. Чтобы понять,
    кто стоит за логином из сообщения.

    - query — логин или часть имени, например nakhrom или Рустэм;
    - limit — сколько людей (по умолчанию 20)."""
    found = _client().search_users(query, limit=_limit(limit))
    return {"count": len(found), "users": found}


@mattermost.action
def mentions(days: int = 7, limit: int = 30) -> dict:
    """Сообщения за последние days дней, где упомянут пользователь (@логин), новые сверху.

    - days — за сколько дней (по умолчанию 7);
    - limit — сколько сообщений (по умолчанию 30)."""
    posts = _client().mentions(days=days, limit=_limit(limit))
    return {"count": len(posts), "posts": posts}


@mattermost.action
def unreads() -> dict:
    """Каналы с непрочитанным, сначала те, где есть упоминания: название, сколько непрочитанных,
    сколько упоминаний, флаг muted. В muted-канале (пользователь выключил уведомления) число
    непрочитанных — не сигнал, а упоминание — сигнал; такие каналы идут после остальных.
    Ничего не помечает прочитанным."""
    found = _client().unreads()
    return {"count": len(found), "channels": found}


@mattermost.action
def saved_posts(limit: int = 100) -> dict:
    """Сообщения, которые пользователь сохранил (флажок «сохранено»), новые сверху. Только чтение:
    с «сохранено» ничего не снимается.

    - limit — сколько сообщений (по умолчанию 100)."""
    posts = _client().saved_posts(limit=_limit(limit))
    return {"count": len(posts), "posts": posts}

