"""motor.gang.su in the browser: login, providers with their tokens, consumers.

Server-rendered HTML with plain forms (no JS). Every form is a POST under
`/page/...` and ends with a redirect back to `/`, except «новый потребитель»,
which renders the page once with the new token on it.

CSRF: the cookie is SameSite=Lax (not sent on cross-site POSTs) and every
POST must come with a same-host Origin when the browser sends one.
"""
from __future__ import annotations

import time
from html import escape
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response
from starlette.routing import Route

from .auth import COOKIE_NAME, SESSION_TTL, make_session, session_valid, verify_password
from .provider import Provider

STATUS = {
    "ok": ("работает", "ok"),
    "expired": ("протух", "bad"),
    "down": ("лежит", "bad"),
    "error": ("ошибка", "bad"),
}

NO_STORE = {"Cache-Control": "no-store"}

CSS = """
:root { color-scheme: light dark; --bg:#fafafa; --fg:#1d1d1f; --muted:#6e6e73; --card:#fff; --line:#e3e3e8;
        --ok:#1a7f37; --bad:#c62828; --accent:#2f5bd3; }
@media (prefers-color-scheme: dark) {
  :root { --bg:#151517; --fg:#ececf0; --muted:#9a9aa2; --card:#1f1f23; --line:#33333a;
          --ok:#4ac26b; --bad:#ff6b6b; --accent:#7aa2ff; } }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--fg);
       font:15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; } h2 { font-size: 17px; margin: 32px 0 10px; }
.muted { color: var(--muted); } .small { font-size: 13px; }
.card { background:var(--card); border:1px solid var(--line); border-radius:10px; padding:14px 16px; margin:10px 0; }
.row { display:flex; gap:12px; align-items:baseline; justify-content:space-between; flex-wrap:wrap; }
.ok { color: var(--ok); } .bad { color: var(--bad); }
form { margin: 0; } form.inline { display:inline; }
.fields { display:grid; gap:8px; margin-top:10px; }
input[type=text], input[type=password], textarea { width:100%; padding:8px 10px; font:inherit; color:inherit;
       background:var(--bg); border:1px solid var(--line); border-radius:8px; }
textarea { min-height: 80px; font-family: ui-monospace, Menlo, monospace; font-size: 13px; }
button { font:inherit; padding:7px 14px; border-radius:8px; border:1px solid var(--line);
         background:var(--card); color:inherit; cursor:pointer; }
button.primary { background:var(--accent); border-color:var(--accent); color:#fff; }
button.danger { color: var(--bad); }
.token { font-family: ui-monospace, Menlo, monospace; word-break: break-all; padding:10px;
         border:1px dashed var(--ok); border-radius:8px; margin:8px 0; }
code { font-family: ui-monospace, Menlo, monospace; font-size: 13px; }
.error { color: var(--bad); margin: 8px 0; }
"""


def _doc(title: str, body: str) -> HTMLResponse:
    html = (
        "<!doctype html><html lang=ru><head><meta charset=utf-8>"
        "<meta name=viewport content='width=device-width, initial-scale=1'>"
        f"<title>{escape(title)}</title><style>{CSS}</style></head>"
        f"<body><main>{body}</main></body></html>"
    )
    return HTMLResponse(html, headers=NO_STORE)


def _ago(ts: float | None) -> str:
    if not ts:
        return "никогда"
    delta = time.time() - ts
    if delta < 90:
        return "только что"
    if delta < 3600:
        return f"{int(delta // 60)} мин назад"
    if delta < 86400:
        return f"{int(delta // 3600)} ч назад"
    return time.strftime("%d.%m.%Y %H:%M", time.localtime(ts))


def logged_in(request: Request) -> bool:
    config = request.app.state.config
    return bool(config.password_hash) and session_valid(config.session_secret, request.cookies.get(COOKIE_NAME))


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return origin is None
    return urlsplit(origin).netloc == request.headers.get("host")


def _guard(request: Request) -> Response | None:
    """For page POSTs: logged in and same origin, or a ready answer."""
    if not _same_origin(request):
        return Response("чужой Origin", status_code=403)
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return None


def _home() -> RedirectResponse:
    return RedirectResponse("/", status_code=303)


# ── login ────────────────────────────────────────────────────────────────────


def _login_page(error: str = "", configured: bool = True) -> HTMLResponse:
    if not configured:
        body = (
            "<h1>Мотор</h1><div class=card><p>Пароль странички ещё не задан.</p>"
            "<p class=small>На маке: <code>python -m motor hash-password</code> → строку — в "
            "<code>make env S=motor K=MOTOR_PASSWORD_HASH</code> (из папки infra).</p></div>"
        )
        return _doc("Мотор — вход", body)
    body = (
        "<h1>Мотор</h1><p class=muted>motor.gang.su</p><div class=card>"
        "<form method=post action=/login class=fields>"
        "<input type=password name=password placeholder=Пароль autofocus autocomplete=current-password>"
        f"{f'<div class=error>{escape(error)}</div>' if error else ''}"
        "<div><button class=primary>Войти</button></div></form></div>"
    )
    return _doc("Мотор — вход", body)


async def login_page(request: Request) -> Response:
    if logged_in(request):
        return _home()
    return _login_page(configured=bool(request.app.state.config.password_hash))


async def login(request: Request) -> Response:
    config = request.app.state.config
    throttle = request.app.state.login_throttle
    if not _same_origin(request):
        return Response("чужой Origin", status_code=403)
    if not config.password_hash:
        return _login_page(configured=False)
    wait = throttle.retry_after()
    if wait:
        return _login_page(f"Слишком много попыток — подожди {wait // 60 + 1} мин")
    form = await request.form()
    password = form.get("password")
    if not isinstance(password, str) or not verify_password(password, config.password_hash):
        throttle.failed()
        return _login_page("Неверный пароль")
    throttle.succeeded()
    response = _home()
    response.set_cookie(
        COOKIE_NAME, make_session(config.session_secret), max_age=SESSION_TTL, path="/",
        httponly=True, samesite="lax", secure=request.url.scheme == "https",
    )
    return response


async def logout(request: Request) -> Response:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax")
    return response


# ── main page ────────────────────────────────────────────────────────────────


def _provider_card(provider: Provider, store) -> str:
    state = store.provider_state(provider.name)
    if state is None:
        status = "<span class=muted>ещё не звали</span>"
    else:
        label, cls = STATUS.get(state.status, (state.status, "bad"))
        status = f"<span class={cls}>{label}</span>"
        if state.status != "ok" and state.message:
            status += f" <span class=small>— {escape(state.message)}</span>"
    answered = _ago(state.last_ok_at) if state else "никогда"
    actions = ", ".join(f"<code>{escape(a)}</code>" for a in sorted(provider.actions)) or "—"
    rule = "чтение и запись" if provider.writes else "только чтение"
    html = (
        f"<div class=card id=p-{provider.name}><div class=row><strong>{escape(provider.title)}</strong>"
        f"<span>{status}</span></div>"
        f"<div class='small muted'><code>/{provider.name}</code> · {rule} · последний раз ответил: {answered}</div>"
        f"<div class='small muted'>действия: {actions}</div>"
    )
    if provider.secrets:
        have = store.secrets_set(provider.name)
        fields = []
        for secret in provider.secrets:
            set_note = f"задан {_ago(have[secret.name])}" if secret.name in have else "не задан"
            placeholder = escape(f"{secret.label} — {set_note}")
            name = f"secret.{escape(secret.name)}"
            control = (
                f"<textarea name='{name}' placeholder='{placeholder}'></textarea>" if secret.multiline else
                f"<input type=password name='{name}' placeholder='{placeholder}' autocomplete=off>"
            )
            hint = f"<div class='small muted'>{escape(secret.hint)}</div>" if secret.hint else ""
            fields.append(control + hint)
        html += (
            f"<form method=post action='/page/providers/{provider.name}' class=fields>{''.join(fields)}"
            "<div><button class=primary>Сохранить</button>"
            + (" <span class='small muted'>после сохранения мотор сразу проверит токен</span>" if provider.check else "")
            + "</div></form>"
        )
    if provider.check:
        html += (
            f"<form method=post action='/page/providers/{provider.name}/check' style='margin-top:8px'>"
            "<button>Проверить</button></form>"
        )
    return html + "</div>"


def _main_page(request: Request, new_token: tuple[str, str] | None = None, error: str = "") -> HTMLResponse:
    store = request.app.state.store
    providers: list[Provider] = request.app.state.providers
    parts = [
        "<div class=row><h1>Мотор</h1>"
        "<form method=post action=/logout class=inline><button>Выйти</button></form></div>"
        "<p class='muted small'>Один вход ко всем поставщикам: "
        "<code>GET /&lt;поставщик&gt;/&lt;действие&gt;</code> → JSON, MCP — <code>/&lt;поставщик&gt;</code>, "
        "токен потребителя — <code>Authorization: Bearer …</code></p>",
        "<h2>Поставщики</h2>",
        *(_provider_card(p, store) for p in providers),
        "<h2>Потребители</h2>",
    ]
    if new_token:
        name, token = new_token
        parts.append(
            f"<div class=card><strong>{escape(name)}</strong> — токен показывается один раз, скопируй сейчас:"
            f"<div class=token>{escape(token)}</div>"
            "<div class='small muted'>Сервис на сервере: <code>make env S=&lt;сервис&gt; K=MOTOR_TOKEN</code>. "
            "Мак: <code>export MOTOR_TOKEN=…</code> в <code>~/.zshrc</code>.</div></div>"
        )
    parts.append(
        "<div class=card><form method=post action=/page/consumers class=row>"
        "<input type=text name=name placeholder='Имя: мак, recaps, хаб…' required style='flex:1;min-width:200px'>"
        "<button class=primary>Новый потребитель</button></form>"
        f"{f'<div class=error>{escape(error)}</div>' if error else ''}</div>"
    )
    consumers = store.consumers()
    if not consumers:
        parts.append("<p class=muted>Пока никого.</p>")
    for consumer in consumers:
        parts.append(
            f"<div class=card><div class=row><span><strong>{escape(consumer.name)}</strong> "
            f"<span class='small muted'>· последний раз ходил: {_ago(consumer.last_seen_at)}"
            f" · выписан {_ago(consumer.created_at)}</span></span>"
            f"<form method=post action='/page/consumers/{consumer.id}/revoke' class=inline>"
            "<button class=danger>Отозвать</button></form></div></div>"
        )
    return _doc("Мотор", "".join(parts))


async def home(request: Request) -> Response:
    if not logged_in(request):
        return RedirectResponse("/login", status_code=303)
    return _main_page(request)


async def add_consumer(request: Request) -> Response:
    if (answer := _guard(request)) is not None:
        return answer
    form = await request.form()
    name = str(form.get("name") or "").strip()
    store = request.app.state.store
    if not name or len(name) > 64:
        return _main_page(request, error="Имя — от 1 до 64 символов")
    if any(c.name == name for c in store.consumers()):
        return _main_page(request, error=f"«{name}» уже есть — отзови старый или возьми другое имя")
    token = store.add_consumer(name)
    return _main_page(request, new_token=(name, token))


async def revoke_consumer(request: Request) -> Response:
    if (answer := _guard(request)) is not None:
        return answer
    request.app.state.store.revoke(int(request.path_params["consumer_id"]))
    return _home()


def _provider(request: Request) -> Provider | None:
    name = request.path_params["name"]
    return next((p for p in request.app.state.providers if p.name == name), None)


async def save_secrets(request: Request) -> Response:
    if (answer := _guard(request)) is not None:
        return answer
    provider = _provider(request)
    if provider is None or not provider.secrets:
        return Response("нет такого поставщика", status_code=404)
    form = await request.form()
    changed = False
    for secret in provider.secrets:
        value = str(form.get(f"secret.{secret.name}") or "").strip()
        if value:
            request.app.state.store.set_secret(provider.name, secret.name, value)
            changed = True
    if changed:
        await provider.run_check()
    return _home()


async def check_provider(request: Request) -> Response:
    if (answer := _guard(request)) is not None:
        return answer
    provider = _provider(request)
    if provider is None:
        return Response("нет такого поставщика", status_code=404)
    await provider.run_check()
    return _home()


routes = [
    Route("/", home, methods=["GET"]),
    Route("/login", login_page, methods=["GET"]),
    Route("/login", login, methods=["POST"]),
    Route("/logout", logout, methods=["POST"]),
    Route("/page/consumers", add_consumer, methods=["POST"]),
    Route("/page/consumers/{consumer_id:int}/revoke", revoke_consumer, methods=["POST"]),
    Route("/page/providers/{name}", save_secrets, methods=["POST"]),
    Route("/page/providers/{name}/check", check_provider, methods=["POST"]),
]
