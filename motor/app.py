"""The motor as one ASGI app.

    GET  /healthz                    open, for infra
    /, /login, /logout, /page/...    the page (password + cookie) — motor/page.py
    POST /<provider>                 MCP of that provider (Bearer)
    GET  /<provider>/<action>        the action as JSON (Bearer); POST for writes
"""
from __future__ import annotations

import contextlib
import logging

from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.datastructures import Headers
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from . import page
from .auth import LoginThrottle, bearer
from .config import Config
from .errors import PAGE, BadRequest, MethodNotAllowed, MotorError, NotFound, Unauthorized
from .provider import Provider, current_consumer, dump
from .store import Store

log = logging.getLogger(__name__)


class ConsumerAuth:
    """Lets a provider request through only with a live consumer token."""

    def __init__(self, app, store: Store):
        self.app = app
        self.store = store

    async def __call__(self, scope, receive, send):
        token = bearer(Headers(scope=scope).get("authorization"))
        consumer = self.store.consumer_by_token(token) if token else None
        if consumer is None:
            error = Unauthorized(
                f"токен мотора не подошёл (отозван?) — выпиши новый на {PAGE}" if token else
                f"нужен токен мотора в Authorization: Bearer … — выпиши его на {PAGE}"
            )
            response = JSONResponse(error.body(), error.status, headers={"WWW-Authenticate": 'Bearer realm="motor"'})
            await response(scope, receive, send)
            return
        reset = current_consumer.set(consumer)
        try:
            await self.app(scope, receive, send)
        finally:
            current_consumer.reset(reset)


class ActionEndpoint:
    """`/<provider>/<action>` — the plain-HTTP face of every action."""

    def __init__(self, provider: Provider):
        self.provider = provider

    async def __call__(self, scope, receive, send):
        request = Request(scope, receive)
        try:
            result = await self._handle(request)
            response: Response = Response(dump(result), media_type="application/json")
        except MotorError as exc:
            response = JSONResponse(exc.body(), exc.status)
        await response(scope, receive, send)

    async def _handle(self, request: Request):
        provider = self.provider
        name = request.path_params["action"]
        if request.method == "POST" and not provider.writes:
            raise provider.forbidden()
        action = provider.actions.get(name)
        if action is None:
            raise NotFound(f"у {provider.title} нет действия {name!r}; есть: {', '.join(sorted(provider.actions))}")
        if request.method != action.method:
            raise MethodNotAllowed(f"{provider.name}/{name} — это {action.method}")
        if request.method == "GET":
            raw = {
                key: (request.query_params.getlist(key) if key in action.list_params else request.query_params[key])
                for key in request.query_params
            }
        else:
            body = await request.body()
            try:
                raw = await request.json() if body.strip() else {}
            except ValueError as exc:
                raise BadRequest("тело запроса — не JSON") from exc
            if not isinstance(raw, dict):
                raise BadRequest("тело запроса — JSON-объект с параметрами")
        return await action.run(action.parse(raw))


async def healthz(request: Request) -> Response:
    return PlainTextResponse("ok")


async def not_found(request: Request, exc) -> Response:
    error = NotFound(f"нет такого адреса: {request.url.path}")
    return JSONResponse(error.body(), error.status)


def create_app(config: Config, providers: list[Provider] | None = None) -> Starlette:
    if providers is None:
        from .providers import PROVIDERS

        providers = PROVIDERS
    store = Store(config.data_dir / "motor.db")
    managers = []
    routes = [Route("/healthz", healthz), *page.routes]
    for provider in providers:
        provider.store = store
        provider.peers = providers
        server = provider.mcp_server()
        # Stateless + plain JSON answers: no sessions to keep, nothing to stream.
        # Host/Origin checks are off: Caddy sets Host, and MCP takes only Bearer, never the cookie.
        server.streamable_http_app(
            stateless_http=True,
            json_response=True,
            transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
        )
        managers.append(server.session_manager)
        mcp = StreamableHTTPASGIApp(server.session_manager)
        routes.append(Route(f"/{provider.name}", ConsumerAuth(mcp, store), methods=["GET", "POST", "DELETE"]))
        routes.append(Route(f"/{provider.name}/{{action}}", ConsumerAuth(ActionEndpoint(provider), store),
                            methods=["GET", "POST"]))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with contextlib.AsyncExitStack() as stack:
            for manager in managers:
                await stack.enter_async_context(manager.run())
            yield
        store.close()

    app = Starlette(routes=routes, lifespan=lifespan, exception_handlers={404: not_found})
    app.state.config = config
    app.state.store = store
    app.state.providers = providers
    app.state.login_throttle = LoginThrottle()
    return app
