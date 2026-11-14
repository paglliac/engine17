"""`people` — люди хаба: список, алиасы, профиль. Чтение и запись.

Перенос `~/SecondBrain-new/server/mcp/people.py` (8 инструментов). Токен хаба — из поля «Трекер».
"""
from __future__ import annotations

from urllib.parse import quote

from ..errors import BadRequest
from ..provider import Provider
from . import hub

people = Provider(
    "people",
    "Люди",
    writes=True,
    instructions=(
        "Люди хаба SecondBrain: список с алиасами (ники, логины Mattermost, варианты имени), "
        "поиск слага по упоминанию, профиль, заведение и правка человека. Читать и писать."
    ),
)


def _p(method: str, path: str, *, params: dict | None = None, body=None):
    return hub.call(people, method, f"/people{path}", params=params, body=body)


@people.action
def list_people() -> dict:
    """
    The people roster: every known person with slug, name, role, relation, and
    aliases (nicknames, Mattermost logins, name variants). Use to check whether
    a person exists in the knowledge base before delegating a task to them.
    """
    return _p("GET", "/")


@people.action
def resolve_person(query: str) -> dict:
    """
    Resolve a free-form person mention to a canonical person slug.

    Matches the query (a name, Mattermost login, nickname, or slug) against the
    roster and its aliases. Returns {"query": ..., "slug": <slug>|null}.
    A null slug means the mention is NOT a known person (or is ambiguous) —
    callers should not file action items addressed to them.

    Args:
        query: the mention to resolve, e.g. "Ваня", "ivan.petrov", "Иван".
    """
    return _p("GET", "/resolve", params={"q": query})


@people.action
def list_aliases(slug: str | None = None) -> dict:
    """
    Person aliases stored in the DB (person_aliases table), grouped by slug:
    {"aliases": {slug: [alias, ...]}}. Pass `slug` to scope to one person.

    Args:
        slug: optional person slug to filter by.
    """
    return _p("GET", "/aliases", params={"slug": slug or None})


@people.action(write=True)
def add_alias(slug: str, alias: str) -> dict:
    """
    Add an alias (nickname, Mattermost login, name variant) for a person, so a
    future mention resolves to their slug. Stored in the DB.

    Args:
        slug: canonical person slug, e.g. "ivan-petrov".
        alias: the alias to add, e.g. "Ваня" or "ivanp".
    """
    return _p("POST", "/aliases/add", body={"slug": slug, "alias": alias})


@people.action(write=True)
def remove_alias(slug: str, alias: str) -> dict:
    """
    Remove one alias for a person from the DB.

    Args:
        slug: canonical person slug.
        alias: the alias to remove.
    """
    return _p("POST", "/aliases/remove", body={"slug": slug, "alias": alias})


@people.action
def get_person(slug: str) -> dict:
    """
    Full profile for one person: name, role, relation, team, delegated tasks
    (expectations), meetings, avatar.

    Args:
        slug: canonical person slug, e.g. "ivan-petrov".
    """
    return _p("GET", f"/{quote(slug, safe='')}")


@people.action(write=True)
def create_person(slug: str, name: str, role: str | None = None, relation: str | None = None) -> dict:
    """
    Add a person to the roster. Fails if the slug already exists.

    Args:
        slug: canonical slug, lowercase-hyphenated latin, e.g. "ivan-petrov".
            Immutable once created.
        name: display name, e.g. "Иван Петров".
        role: intro paragraph (title, team, working context).
        relation: "direct-report", "peer", "manager", "skip-level-manager",
            "adjacent", or omitted.
    """
    body: dict = {"slug": slug, "name": name}
    if role is not None:
        body["role"] = role
    if relation is not None:
        body["relation"] = relation
    return _p("POST", "/", body=body)


@people.action(write=True)
def update_person(slug: str, name: str | None = None, role: str | None = None,
                  relation: str | None = None) -> dict:
    """
    Edit a person's editable profile fields. Omitted arguments are left alone.

    Everything is stored in the DB (the `person` table).
    The slug itself is immutable — tasks, aliases and meeting links point at it.

    Args:
        slug: canonical person slug, e.g. "ivan-petrov".
        name: new display name.
        role: new intro paragraph (title, team, working context).
        relation: how they relate to the user — one of "direct-report", "peer",
            "manager", "skip-level-manager", "adjacent", or "" to clear it.
    """
    body = {k: v for k, v in (("name", name), ("role", role), ("relation", relation)) if v is not None}
    if not body:
        raise BadRequest("нечего менять — передай name, role или relation")
    return _p("POST", f"/{quote(slug, safe='')}", body=body)
