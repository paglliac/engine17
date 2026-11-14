"""`triage` — очередь разбора хаба: что лежит, поля, ручные пункты, решения. Чтение и запись.

Перенос `~/SecondBrain-new/server/mcp/triage.py` (6 инструментов). Токен хаба — из поля «Трекер».
"""
from __future__ import annotations

from ..provider import Provider
from . import hub

triage = Provider(
    "triage",
    "Разбор",
    writes=True,
    instructions=(
        "Очередь разбора (triage) хаба SecondBrain — то же, что страница /triage: пункты с предложенным "
        "маршрутом, поля для правки, ручное добавление пунктов, решения accept/dismiss. Читать и писать."
    ),
)


def _tr(method: str, path: str, *, params: dict | None = None, body=None):
    return hub.call(triage, method, f"/triage{path}", params=params, body=body)


@triage.action
def debug_route(dest: str) -> dict:
    """
    Resolve a destination path string into a structured action + human label,
    exactly as build_triage would store it in routing. No LLM call.

    Args:
        dest: A destination path, e.g. "people/petr-volkov/tasks" or
              "tracker/projects/resource-planning.json".
    """
    return _tr("GET", "/debug/route", params={"dest": dest})


@triage.action
def list_items() -> dict:
    """
    List all pending triage items (the queue the /triage UI shows). Returns
    {items, files}: each item has id, text, owner, due, source, group, routing
    (suggested destination + confidence), entities, and decision_status
    if already decided. `files` are per-batch summaries (total/decided/remaining).
    Fully-applied batches are excluded.
    """
    return _tr("GET", "/items")


@triage.action
def triage_status() -> dict:
    """
    Triage queue stats without listing items: total, decided, applied, writing,
    pending, errors, remaining, and the number of active batches.
    """
    return _tr("GET", "/status")


@triage.action
def list_fields() -> dict:
    """
    Every field a triage item can be edited by: name, RU label, value kind, and
    the routes each one applies to.

    Ask this before composing `edits` rather than working from a remembered list:
    the server owns the registry (`server/api/triage_fields.py`), and a field
    added there shows up here without this tool changing.
    """
    return _tr("GET", "/fields")


@triage.action(write=True)
def create_items(items: list[dict]) -> dict:
    """
    Insert items into the triage queue by hand, for cases where a route is
    already known and there is no capture to run classification on — e.g. items
    an agent found by reading Mattermost directly rather than through the intake
    pipeline. They land in the same queue `list_items()` shows, marked
    `source_label: "Ручной ввод"` so the UI can tell them apart from pipeline
    output, and the user decides them exactly like anything else in triage.

    Args:
        items: one dict per item. `text` is the only required key. Everything
               else is an optional field from the registry (call `list_fields()`
               for the current set) — commonly `owner` ("me" or a person-slug),
               `initiator`, `project` (a slug or a mention — resolved the same
               way triage chat resolves one), `due` (YYYY-MM-DD),
               `estimate_minutes`, plus `category` and `source`
               (a URL/path string, or `{"path", "position"}`) which are stored
               as-is. `action` may be given explicitly (`next_step` (default),
               `someday`, `project_task`, `new_project`, `delegate`,
               `project_info`, `knowledge`) — it defaults to `project_task` when
               `project` is given, else `next_step`.

    The whole call is one batch: if any item fails validation (an unresolvable
    owner/initiator/project, a malformed date, a field that does not apply to
    its route), NOTHING is inserted — the error names which item and why, fix it
    and resend the full list rather than only the failed ones.
    """
    return _tr("POST", "/items", body={"items": items})


@triage.action(write=True)
def decide(item_id: int, action: str, edits: dict | None = None) -> dict:
    """
    Apply one triage decision (same as clicking accept/dismiss in the UI). The
    server writes to the KB immediately and updates inbox.db.

    Args:
        item_id: id of the triage item (from list_items).
        action: "accept" (route to KB using routing/edits) or "dismiss"
                (mark inbox item done, no KB write).
        edits: optional field overrides — the route (`action` plus its
               `project_slug` / `project_title` / `person`), the item `text`, and
               any other editable field: due, estimate_minutes, owner, initiator,
               and the new-project ones (goal, priority, urgency, context,
               stakeholders). Call `list_fields()` for the current set and which
               routes each field applies to; include only what you are changing.
               Person mentions may be names — the server resolves them to slugs
               and refuses the ones it cannot place.
    """
    return _tr("POST", "/decide", body={"id": item_id, "action": action, "edits": edits})
