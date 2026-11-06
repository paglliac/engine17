"""Provider and action: describe an action once, get an HTTP address and an MCP tool.

    from motor.provider import Provider, Secret

    mattermost = Provider(
        "mattermost", "Mattermost", writes=False,
        instructions="Сообщения, каналы, сохранённые и люди Mattermost (только чтение).",
        secrets=[Secret("token", "Personal access token")],
    )

    @mattermost.action
    def saved_posts(since: str | None = None, limit: int = 100) -> list[dict]:
        '''Сохранённые сообщения, новые сверху.'''
        token = mattermost.secret()
        ...

- `GET /mattermost/saved_posts?limit=10` → JSON (`Authorization: Bearer <токен мотора>`).
- MCP at `/mattermost`, tool `saved_posts` with the same parameters and docstring.
- Parameters come from the signature; plain types (str, int, bool, float,
  `X | None`, `list[str]`) work in both. A list in the query string is the
  parameter repeated (`?id=a&id=b`).
- Long lists: give `limit` a default (сообщения 30, сохранённые 100, …) and
  filters; no paging (docs/motor.md, «Длинные списки»).
- Errors: raise `provider.expired()`, `provider.down(...)` or another
  `motor.errors` class — both sides show its text. Anything else is a 502.
- Writes: one rule per provider (`writes=`). Only a provider with
  `writes=True` may have `@p.action(write=True)`; those are POST with a JSON
  body. A provider with `writes=False` answers 403 to every POST.
- Sync functions run in a thread, so blocking clients (`requests`,
  `exchangelib`) are fine.
"""
from __future__ import annotations

import functools
import inspect
import json
import logging
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable, get_args, get_origin

import anyio.to_thread
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import ConfigDict, ValidationError, create_model

from .errors import PAGE, BadRequest, MotorError, ProviderFailed, TokenExpired, Unavailable, WriteForbidden

if TYPE_CHECKING:
    from .store import Consumer, Store

log = logging.getLogger(__name__)

# Who is calling — set by the auth middleware for every provider request.
current_consumer: ContextVar["Consumer | None"] = ContextVar("current_consumer", default=None)

# Paths of the page; a provider cannot take these names.
RESERVED = {"", "login", "logout", "page", "healthz", "static", "favicon.ico"}


@dataclass(frozen=True)
class Secret:
    """An external token field on the page."""

    name: str
    label: str
    hint: str = ""
    multiline: bool = False


class Action:
    def __init__(self, provider: Provider, fn: Callable[..., Any], write: bool):
        self.provider = provider
        self.fn = fn
        self.write = write
        self.name = fn.__name__
        self.description = inspect.getdoc(fn) or ""
        self.signature = inspect.signature(fn, eval_str=True)
        fields: dict[str, Any] = {}
        self.list_params: set[str] = set()
        for param in self.signature.parameters.values():
            if param.kind not in (param.POSITIONAL_OR_KEYWORD, param.KEYWORD_ONLY):
                raise TypeError(f"{provider.name}.{self.name}: only named parameters, not *args/**kwargs")
            annotation = Any if param.annotation is param.empty else param.annotation
            default = ... if param.default is param.empty else param.default
            fields[param.name] = (annotation, default)
            if _is_list(annotation):
                self.list_params.add(param.name)
        self.model = create_model(
            f"{provider.name}_{self.name}", __config__=ConfigDict(extra="forbid"), **fields
        )

    @property
    def method(self) -> str:
        return "POST" if self.write else "GET"

    def parse(self, raw: dict[str, Any]) -> dict[str, Any]:
        """Check and convert HTTP input ('30' → 30) against the signature."""
        try:
            parsed = self.model.model_validate(raw)
        except ValidationError as exc:
            raise BadRequest(_explain(exc)) from exc
        return {name: getattr(parsed, name) for name in type(parsed).model_fields}

    async def run(self, kwargs: dict[str, Any]) -> Any:
        """Call the action and remember how the provider answered."""
        provider = self.provider
        try:
            if inspect.iscoroutinefunction(self.fn):
                result = await self.fn(**kwargs)
            else:
                result = await anyio.to_thread.run_sync(functools.partial(self.fn, **kwargs))
        except TokenExpired as exc:
            provider.record("expired", exc.message)
            raise
        except Unavailable as exc:
            provider.record("down", exc.message)
            raise
        except MotorError:
            raise  # bad input, write refused, not found: says nothing about the provider's health
        except Exception as exc:
            log.exception("%s.%s failed", provider.name, self.name)
            message = (
                f"{provider.title} ответил неожиданно ({type(exc).__name__}: {str(exc)[:200]}) — "
                "подробности в журнале мотора (journalctl -u motor)"
            )
            provider.record("error", message)
            raise ProviderFailed(message) from exc
        provider.record("ok")
        return result


class Provider:
    def __init__(
        self,
        name: str,
        title: str,
        *,
        writes: bool,
        instructions: str,
        secrets: list[Secret] | tuple[Secret, ...] = (),
        check: Callable[[], Any] | None = None,
    ):
        """`writes` — can anyone write through the motor (one rule for all consumers).
        `instructions` — what the provider is for; shown to Claude with its tools.
        `check` — a cheap call that proves the stored token works; the page runs it
        after a token is saved (raise `expired()` if not)."""
        if name in RESERVED or not name.isidentifier():
            raise ValueError(f"bad provider name: {name!r}")
        self.name = name
        self.title = title
        self.writes = writes
        self.instructions = instructions
        self.secrets = list(secrets)
        self.check = check
        self.actions: dict[str, Action] = {}
        self.store: Store | None = None  # bound by create_app
        self.peers: list[Provider] = []  # every provider of the same app, bound by create_app

    def action(self, fn: Callable[..., Any] | None = None, *, write: bool = False):
        """Register an action: `@p.action` or `@p.action(write=True)`."""

        def register(fn: Callable[..., Any]) -> Callable[..., Any]:
            if write and not self.writes:
                raise ValueError(f"{self.name}: writes=False, a write action is not allowed")
            action = Action(self, fn, write)
            if action.name in self.actions:
                raise ValueError(f"{self.name}.{action.name} is already registered")
            self.actions[action.name] = action
            return fn

        return register(fn) if fn is not None else register

    # ── for action code ──────────────────────────────────────────────────────

    def secret(self, field: str | None = None) -> str:
        """The stored external token; missing → 401 «вставь на странице»."""
        if field is None:
            if len(self.secrets) != 1:
                raise ValueError(f"{self.name}: name the secret field")
            field = self.secrets[0].name
        value = self._store().secret(self.name, field)
        if not value:
            raise TokenExpired(f"токена {self.title} нет — вставь его на {PAGE}")
        return value

    def expired(self, message: str | None = None) -> TokenExpired:
        return TokenExpired(message or f"токен {self.title} протух — вставь новый на {PAGE}")

    def down(self, message: str | None = None) -> Unavailable:
        return Unavailable(message or f"{self.title} не отвечает — попробуй позже")

    def forbidden(self) -> WriteForbidden:
        return WriteForbidden(f"запись в {self.title} через мотор запрещена — только чтение")

    # ── for the motor ────────────────────────────────────────────────────────

    def _store(self) -> Store:
        if self.store is None:
            raise RuntimeError(f"provider {self.name} is not bound to a store")
        return self.store

    def record(self, status: str, message: str = "") -> None:
        self._store().record(self.name, status, message)

    async def run_check(self) -> None:
        """Run `check` and record the outcome; errors are recorded, not raised."""
        if self.check is None:
            return
        try:
            if inspect.iscoroutinefunction(self.check):
                await self.check()
            else:
                await anyio.to_thread.run_sync(self.check)
        except MotorError as exc:
            self.record("expired" if isinstance(exc, TokenExpired) else "down", exc.message)
        except Exception as exc:
            log.exception("%s check failed", self.name)
            self.record("error", f"проверка упала: {type(exc).__name__}: {str(exc)[:200]}")
        else:
            self.record("ok")

    def mcp_server(self) -> MCPServer:
        server = MCPServer(self.name, title=f"Мотор: {self.title}", instructions=self.instructions)
        for action in self.actions.values():
            server.add_tool(
                _tool(action),
                name=action.name,
                description=action.description,
                annotations=ToolAnnotations(
                    read_only_hint=not action.write,
                    destructive_hint=action.write,
                    open_world_hint=True,
                ),
                structured_output=False,
            )
        return server


def dump(result: Any) -> str:
    return json.dumps(result, ensure_ascii=False, default=str)


def _tool(action: Action) -> Callable[..., Any]:
    """The MCP face of an action: same parameters, JSON text out, motor errors as tool errors."""

    async def tool(**kwargs: Any) -> Any:
        try:
            result = await action.run(kwargs)
        except MotorError as exc:
            return CallToolResult(content=[TextContent(type="text", text=exc.message)], is_error=True)
        return dump(result)

    tool.__name__ = action.name
    tool.__doc__ = action.description
    tool.__signature__ = action.signature.replace(return_annotation=Any)  # type: ignore[attr-defined]
    return tool


def _is_list(annotation: Any) -> bool:
    if get_origin(annotation) in (list, tuple, set):
        return True
    return any(_is_list(arg) for arg in get_args(annotation) if arg is not type(None))


def _explain(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors():
        where = ".".join(str(item) for item in error["loc"]) or "запрос"
        if error["type"] == "extra_forbidden":
            parts.append(f"{where}: такого параметра нет")
        elif error["type"] == "missing":
            parts.append(f"{where}: обязательный параметр")
        else:
            parts.append(f"{where}: {error['msg']}")
    return "неверные параметры — " + "; ".join(parts)
