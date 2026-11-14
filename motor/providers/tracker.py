"""`tracker` — задачи, проекты, someday, пользователь и повестки встреч хаба. Чтение и запись.

Перенос `~/SecondBrain-new/server/mcp/tracker.py` (25 инструментов) плюс повестки из `sb_calendar.py`
(`get_agenda`, `add_agenda_item`, `set_agenda`): повестки хранит хаб, а не Exchange, поэтому они здесь,
а не в `calendar`. Ключ встречи — `key` из `calendar/day` мотора (тот же, что у хаба).
Токен хаба на страничке — в поле этого поставщика, его же берут `people`, `inbox`, `triage`.
"""
from __future__ import annotations

from urllib.parse import quote

from ..errors import BadRequest
from ..provider import Provider, Secret
from . import hub


def _check() -> None:
    hub.call(tracker, "GET", "/me")


tracker = Provider(
    "tracker",
    "Трекер",
    writes=True,
    instructions=(
        "Трекер хаба SecondBrain: задачи (next-steps и задачи проектов), проекты с журналом, someday, "
        "алиасы проектов, пользователь, повестки встреч календаря. Читать и писать."
    ),
    secrets=[Secret(
        "token", "Токен хаба (SB_API_TOKEN)",
        hint="один на tracker, people, inbox и triage; на сервере хаба — SB_API_TOKEN",
    )],
    check=_check,
)


def _seg(value: str) -> str:
    return quote(value, safe="")


def _t(method: str, path: str, *, params: dict | None = None, body=None):
    return hub.call(tracker, method, f"/tracker{path}", params=params, body=body)


def _root(method: str, path: str, *, params: dict | None = None, body=None):
    return hub.call(tracker, method, path, params=params, body=body)


# ── Tasks ────────────────────────────────────────────────────────────────────

@tracker.action
def list_tasks(
    owner: str | None = None,
    initiator: str | None = None,
    project: str | None = None,
    include_done: bool = False,
    overdue: bool = False,
    due_before: str | None = None,
) -> dict:
    """
    List tasks. Without filters — all open tasks across next-steps and projects.

    Args:
        owner: Filter by owner ("me" or person slug). For delegated tasks use person slug.
        initiator: Filter by who requested the task.
        project: Filter by project slug. If set, returns only that project's tasks.
        include_done: Include completed tasks (default false).
        overdue: Only tasks whose due date is before today.
        due_before: Only tasks due on or before this date (YYYY-MM-DD, inclusive).
            With overdue or due_before only open tasks come back, include_done is ignored.
    """
    return _t("GET", "/tasks", params={
        "include_done": "true" if include_done else "false",
        "overdue": "true" if overdue else None,
        "due_before": due_before, "owner": owner, "initiator": initiator, "project": project,
    })


@tracker.action(write=True)
def add_task(
    text: str,
    owner: str = "me",
    initiator: str | None = None,
    project: str | None = None,
    due: str | None = None,
    source_path: str | None = None,
    source_position: str | None = None,
    estimate_minutes: int | None = None,
    source_url: str | None = None,
) -> dict:
    """
    Add a task.

    Args:
        text: Task text (action verb first).
        owner: Who does it — "me" or person slug.
        initiator: Who requested it — person slug or null.
        project: Project slug to add task into. Null for standalone next-step.
        due: Due date YYYY-MM-DD.
        source_path: Source file path (relative to vault).
        source_position: Position in source (L42-L58, T00:15:30, etc).
        estimate_minutes: Time estimate in minutes, for the day planner. Null if unknown.
        source_url: Link to a source that has no vault copy — e.g. a Mattermost
            permalink (https://...). Use when there is no source_path; both may be set.
    """
    return _t("POST", "/tasks", body={
        "text": text, "owner": owner, "initiator": initiator, "project": project,
        "due": due, "source_path": source_path, "source_position": source_position,
        "estimate_minutes": estimate_minutes, "source_url": source_url,
    })


@tracker.action(write=True)
def update_task(
    id: str,
    project: str | None = None,
    text: str | None = None,
    due: str | None = None,
    owner: str | None = None,
    initiator: str | None = None,
    estimate_minutes: int | None = None,
    clear_due: bool = False,
    source_path: str | None = None,
    source_position: str | None = None,
    source_url: str | None = None,
) -> dict:
    """
    Update a task's fields.

    Args:
        id: Task ID (e.g. "ns-001" or "capacity-t1").
        project: Project slug if task is in a project — says where the task is,
                 it does NOT move it (use move_task for that).
        text: New text.
        due: New due date.
        owner: New owner.
        initiator: New initiator.
        estimate_minutes: New time estimate in minutes.
        clear_due: Remove the due date. A null `due` means "leave it alone",
                   so removing a date takes this flag.
        source_path: Add a source (vault path) to a task that has none.
        source_position: Position in that source.
        source_url: Add a source link (e.g. a Mattermost permalink) to a task
            that has none. An existing source is never replaced — that is an error.
    """
    return _t("PATCH", f"/tasks/{_seg(id)}", body={
        "project": project, "text": text, "due": due, "owner": owner, "initiator": initiator,
        "estimate_minutes": estimate_minutes, "has_due": clear_due or due is not None,
        "source_path": source_path, "source_position": source_position, "source_url": source_url,
    })


@tracker.action(write=True)
def move_task(id: str, project: str | None = None) -> dict:
    """
    Move an existing task into a project, or back out of one.

    Nothing becomes a project here (that is create_project / the person page's
    convert): the task keeps its id, owner, due date and provenance and only
    changes where it hangs.

    Args:
        id: Task ID (e.g. "ns-001" or "capacity-t1").
        project: Target project slug, or None to move it out to the standalone
                 next-steps.
    """
    return _t("POST", f"/tasks/{_seg(id)}/move", body={"project": project})


@tracker.action(write=True)
def delete_task(id: str) -> dict:
    """
    Delete a task by id.

    A task inside a project is soft-deleted (it keeps its row, marked
    `deleted_at`); a standalone next-step is removed outright.

    Args:
        id: Task ID.
    """
    return _t("DELETE", f"/tasks/{_seg(id)}")


@tracker.action(write=True)
def complete_task(id: str, project: str | None = None) -> dict:
    """
    Mark a task as done.

    Args:
        id: Task ID.
        project: Project slug if task is in a project.
    """
    return _t("POST", f"/tasks/{_seg(id)}/complete", body={"project": project})


# ── Projects ─────────────────────────────────────────────────────────────────

@tracker.action
def list_projects(status: str | None = None, owner: str | None = None, initiator: str | None = None) -> dict:
    """
    List live (non-archived) projects. Each row carries awaiting_reason,
    review_date and last_log_date (YYYY-MM-DD of the newest feed entry) — enough
    to spot waiting and stale projects without get_project on each.

    Args:
        status: Filter by status (active, awaiting, someday).
        owner: Filter by project owner.
        initiator: Filter by who initiated the project.
    """
    return _t("GET", "/projects", params={"status": status, "owner": owner, "initiator": initiator})


@tracker.action
def get_project(slug: str) -> dict:
    """
    Get full project document.

    Args:
        slug: Project slug (filename without .json).
    """
    return _t("GET", f"/projects/{_seg(slug)}")


@tracker.action(write=True)
def create_project(
    slug: str,
    title: str,
    goal: str,
    owner: str = "me",
    initiator: str | None = None,
    status: str = "active",
    priority: str | None = None,
    urgency: str | None = None,
    context: str | None = None,
    source_path: str | None = None,
    source_position: str | None = None,
    source_url: str | None = None,
) -> dict:
    """
    Create a new project.

    Args:
        slug: URL-safe identifier (lowercase-hyphenated).
        title: Human-readable project title.
        goal: What done looks like.
        owner: Who owns this project ("me" or person slug).
        initiator: Who requested it (person slug or null).
        status: Initial status (default "active").
        priority: Manual priority label (e.g. "high", "medium", "low"). Not set by agents.
        urgency: Urgency note if any.
        context: Background context.
        source_path: Source file path.
        source_position: Position in source.
        source_url: Link to a source that has no vault copy — e.g. a Mattermost
            permalink (https://...).
    """
    return _t("POST", "/projects", body={
        "slug": slug, "title": title, "goal": goal, "owner": owner, "initiator": initiator,
        "status": status, "priority": priority, "urgency": urgency, "context": context,
        "source_path": source_path, "source_position": source_position,
        "source_url": source_url,
    })


@tracker.action(write=True)
def update_project(
    slug: str,
    title: str | None = None,
    priority: str | None = None,
    owner: str | None = None,
    initiator: str | None = None,
    goal: str | None = None,
    urgency: str | None = None,
    context: str | None = None,
    notes: str | None = None,
    source_path: str | None = None,
    source_position: str | None = None,
    source_url: str | None = None,
) -> dict:
    """
    Update project fields. Status is not one of them — use set_project_status
    or merge_project.

    Args:
        slug: Project slug.
        title: New title.
        priority: Manual priority label (e.g. "high", "medium", "low"). Not set by agents.
        owner: New owner.
        initiator: New initiator.
        goal: New goal.
        urgency: New urgency.
        context: New context.
        notes: New notes.
        source_path: Add a source (vault path) to a project that has none.
        source_position: Position in that source.
        source_url: Add a source link (e.g. a Mattermost permalink) to a project
            that has none. An existing source is never replaced — that is an error.
    """
    return _t("PATCH", f"/projects/{_seg(slug)}", body={
        "title": title, "priority": priority, "owner": owner,
        "initiator": initiator, "goal": goal, "urgency": urgency, "context": context, "notes": notes,
        "source_path": source_path, "source_position": source_position, "source_url": source_url,
    })


@tracker.action(write=True)
def set_project_status(
    slug: str,
    status: str,
    awaiting_reason: str | None = None,
    review_date: str | None = None,
) -> dict:
    """
    Change a project's status — the only way to do it.

    done/cancelled send the project to the archive, any other status brings it
    back. The change writes a system entry into the project's log. To merge a
    project into another use merge_project.

    Args:
        slug: Project slug.
        status: active, awaiting, someday, done or cancelled.
        awaiting_reason: For awaiting — what/whom the project waits for (Russian).
            Ignored and cleared for any other status.
        review_date: For awaiting — when to look again, YYYY-MM-DD.
    """
    return _root("PATCH", f"/projects/{_seg(slug)}/status", body={
        "status": status, "awaiting_reason": awaiting_reason, "review_date": review_date,
    })


@tracker.action(write=True)
def merge_project(slug: str, into: str) -> dict:
    """
    Merge a project into another one.

    Open tasks of `slug` move to `into` (same ids, owners, provenance). `slug`
    becomes status=merged with merged_into=`into` and goes to the archive; both
    logs get a system entry. Nothing else moves — artifacts and stakeholders
    stay with the merged project.

    Args:
        slug: Project being merged away.
        into: Surviving project — must exist and not be archived.
    """
    return _root("POST", f"/projects/{_seg(slug)}/merge", body={"into": into})


@tracker.action(write=True)
def add_project_link(slug: str, url: str, title: str | None = None) -> dict:
    """
    Link an artifact to a project — a roadmap, a doc, a board, a thread.

    It shows in the project card's artifacts block, the same as a link pasted
    there by hand. Use this instead of putting links into the log as text.
    Linking the same page twice is harmless (the URL is normalized).

    Args:
        slug: Project slug.
        url: The link.
        title: Display name. Omit to let the card fetch the page title.
    """
    return _root("POST", f"/projects/{_seg(slug)}/resources", body={"url": url, "title": title})


@tracker.action(write=True)
def add_project_stakeholder(slug: str, person: str) -> dict:
    """
    Share a project with a person — they see it on their page and on 1-1s.

    Same as adding a stakeholder on the project card. The person must be in the
    people roster: a name, nickname or slug is resolved to the canonical slug,
    and an unknown one is an error (add the person or an alias first). Adding
    someone twice is harmless. Current list: get_project → stakeholders.

    Args:
        slug: Project slug.
        person: Person slug or name as the resolver knows it.
    """
    return _root("POST", f"/projects/{_seg(slug)}/stakeholders", body={"person": person})


@tracker.action(write=True)
def remove_project_stakeholder(slug: str, person: str) -> dict:
    """
    Stop sharing a project with a person. `initiator` is untouched — it is the
    record of who asked, not a current relation.

    Args:
        slug: Project slug.
        person: Canonical person slug (as get_project lists it).
    """
    return _root("DELETE", f"/projects/{_seg(slug)}/stakeholders/{_seg(person)}")


@tracker.action
def list_project_log(slug: str) -> dict:
    """
    Read a project's log — its news feed, newest entry first.

    Two kinds of entry share the stream: `note`, written by hand (or by an agent
    filing what it learned), and `system`, written by the tracker itself when the
    project's state changed — a task closed, the status moved. Use it to catch up
    on what has happened to a project since you last looked.

    Args:
        slug: Project slug.
    """
    return _t("GET", f"/projects/{_seg(slug)}/log")


@tracker.action(write=True)
def add_project_log(slug: str, text: str, date: str | None = None) -> dict:
    """
    Write a note into a project's log.

    For news a human would want to read on the project card: what moved, what
    was agreed, what is now blocking. Not for state changes — closing a task or
    changing a status already writes its own system entry.

    Args:
        slug: Project slug.
        text: The note, in Russian (vault data language).
        date: Day the event happened, YYYY-MM-DD, for filing it after the fact.
            Omit for today. Future dates are refused.
    """
    return _t("POST", f"/projects/{_seg(slug)}/log", body={"text": text, "date": date})


# ── Someday ──────────────────────────────────────────────────────────────────

@tracker.action
def list_someday() -> dict:
    """List all someday items."""
    return _t("GET", "/someday")


@tracker.action(write=True)
def add_someday(
    text: str,
    source_path: str | None = None,
    source_position: str | None = None,
) -> dict:
    """
    Add a someday item.

    Args:
        text: Idea or wish text.
        source_path: Source file path.
        source_position: Position in source.
    """
    return _t("POST", "/someday", body={
        "text": text, "source_path": source_path, "source_position": source_position,
    })


@tracker.action(write=True)
def promote_someday(id: str, to: str = "task", owner: str = "me", project: str | None = None) -> dict:
    """
    Promote a someday item to a task or project.

    Args:
        id: Someday item ID.
        to: Target — "task" or "project".
        owner: Owner for the new task/project.
        project: If promoting to task, optional project to add it into.
    """
    return _t("POST", f"/someday/{_seg(id)}/promote", body={"to": to, "owner": owner, "project": project})


# ── User / settings ──────────────────────────────────────────────────────────

@tracker.action
def get_me() -> dict:
    """
    Get the single user of this instance.

    Example return: {"user": {"id": 1, "name": "Иван Петров",
    "email": "ivan.petrov@example.com", "mattermost_user_id": "abc123"}}
    or {"user": None} if nobody has set up this instance yet (see /setup).
    """
    return _root("GET", "/me")


@tracker.action(write=True)
def set_me(name: str, email: str | None = None) -> dict:
    """
    Create the user of this instance if none exists yet, else update it.

    Args:
        name: Display name, required (e.g. "Иван Петров").
        email: Email address, optional.

    Example: set_me("Иван Петров", "ivan.petrov@example.com")
    """
    return _root("PUT", "/me", body={"name": name, "email": email})


# ── Project aliases ─────────────────────────────────────────────────────────

@tracker.action
def list_project_aliases() -> dict:
    """
    List all project slug aliases — mentions no amount of string similarity
    would connect to a project's real name (a rename, internal shorthand, an
    old codename).

    Example return: {"aliases": [{"slug": "capacity-h2", "alias": "old-codename"}]}
    """
    return _root("GET", "/projects/aliases")


@tracker.action(write=True)
def add_project_alias(slug: str, alias: str) -> dict:
    """
    Add an alias that resolves to a project slug.

    Args:
        slug: Canonical project slug — must already exist in the tracker.
        alias: The mention/shorthand that should resolve to it.

    Example: add_project_alias("capacity-h2", "old-codename")
    """
    return _root("POST", "/projects/aliases", body={"slug": slug, "alias": alias})


@tracker.action(write=True)
def remove_project_alias(slug: str, alias: str) -> dict:
    """
    Remove a project slug alias.

    Example: remove_project_alias("capacity-h2", "old-codename")
    """
    return _root("DELETE", "/projects/aliases", body={"slug": slug, "alias": alias})


# ── Meeting agendas (stored in the hub, keyed by the calendar event) ─────────

@tracker.action
def get_agenda(key: str) -> dict:
    """
    The agenda of one calendar event, with the event.

    Returns {"key", "text", "updated_at", "in_snapshot", "event"}. `text` is
    markdown-lite: "- " / "1. " list lines, **bold**. `in_snapshot` false means
    the event has left the hub's calendar window (or was cancelled/recreated) and
    `event` holds only the subject/start saved with the agenda.

    Args:
        key: the event's `key` from the motor's calendar (`day` / `upcoming`).
    """
    return _root("GET", "/calendar/agenda", params={"key": key})


@tracker.action(write=True)
def add_agenda_item(key: str, text: str) -> dict:
    """
    Append one item to a calendar event's agenda — the usual way to add to it.

    The item goes in as a "- text" line at the end; what is already there is
    kept. Use `set_agenda` only to rewrite or reorder the whole agenda.

    Args:
        key: the event's `key` from the motor's calendar (`day` / `upcoming`). Find
            the event there first (by date and subject); if several match, ask
            the user which one.
        text: the item, one line.
    """
    return _root("POST", "/calendar/agenda/items", body={"key": key, "text": text})


@tracker.action(write=True)
def set_agenda(key: str, text: str, subject: str = "", start: str = "") -> dict:
    """
    Put the agenda on one calendar event — replaces what was there.

    The agenda is free text, one line per item. Empty `text` clears it.

    Args:
        key: the event's `key` from the motor's calendar (`day` / `upcoming`). An
            event with an empty `key` can't hold an agenda yet.
        text: the whole agenda. Read the event's current agenda (`get_agenda`) first
            and append to it if the user is adding an item rather than replacing.
        subject: the event's subject — kept with the agenda.
        start: the event's start — kept with the agenda.
    """
    if not key:
        raise BadRequest("key: пустой — у такой встречи повестки быть не может")
    return _root("PUT", "/calendar/agenda", body={"key": key, "text": text, "subject": subject, "start": start})
