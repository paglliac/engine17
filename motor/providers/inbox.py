"""`inbox` — инбокс хаба: состояние конвейера, захваты, ручной запуск. Чтение и запись.

Перенос `~/SecondBrain-new/server/mcp/inbox.py` (10 инструментов). Токен хаба — из поля «Трекер».
"""
from __future__ import annotations

from ..provider import Provider
from . import hub

inbox = Provider(
    "inbox",
    "Инбокс",
    writes=True,
    instructions=(
        "Инбокс хаба SecondBrain: состояние конвейера (источники, ошибки, очереди), захваты, "
        "ручной обход и обработка, повтор и переобработка захвата. process_now тратит вызовы LLM."
    ),
)


def _i(method: str, path: str, *, params: dict | None = None, body=None):
    return hub.call(inbox, method, f"/inbox{path}", params=params, body=body)


@inbox.action
def status() -> dict:
    """
    Pipeline health: per-source last successful listing and last error, capture
    counters (active / failed), job counters, and whether auto-processing is on.

    This is where a dead credential shows up — a source that cannot be listed
    produces no captures at all, so nothing else would report it.
    """
    return _i("GET", "/status")


@inbox.action
def list_captures(state: str = "active", source: str | None = None) -> dict:
    """
    The ledger: captures with their stage, errors and triage counters.

    Args:
        state: active | failed | done | all
        source: optional inbox source name (todoist, mattermost, ktalk)
    """
    return _i("GET", "/captures", params={"state": state, "source": source or None})


@inbox.action(write=True)
def discover_now() -> dict:
    """List every source right now instead of waiting for the 10-minute tick."""
    return _i("POST", "/discover")


@inbox.action(write=True)
def process_now() -> dict:
    """
    Drain the queues now, even when auto-processing is off.

    Costs one LLM call per capture (extraction) plus classification, so this is
    the manual cost gate — not something to call reflexively.
    """
    return _i("POST", "/process")


@inbox.action(write=True)
def retry_capture(capture_id: int) -> dict:
    """Put a failed capture back in the queue, resuming from where it stopped."""
    return _i("POST", f"/captures/{capture_id}/retry")


@inbox.action(write=True)
def run_capture(capture_id: int) -> dict:
    """
    Reset one capture's backoff/failed state and drive it, alone, straight
    through to classified or a new failure — in the background, ignoring
    auto-processing. The rest of the queue is untouched.

    Returns {"started": true} once scheduled, {"already": true} if something
    else already holds this capture (the background loop, a manual /process
    drain, or an earlier run_capture), or an error if the capture isn't found.
    """
    return _i("POST", f"/captures/{capture_id}/run")


@inbox.action(write=True)
def reprocess_capture(capture_id: int) -> dict:
    """
    Send a finished capture back through extraction.

    The only way to redo one: re-saving a post in Mattermost does not resurrect
    it, because the ledger remembers every capture forever (docs/adr/0003).
    """
    return _i("POST", f"/captures/{capture_id}/reprocess")


@inbox.action(write=True)
def sweep_ktalk(since: str) -> dict:
    """
    List ktalk recordings from an older date — the one-off archive sweep.

    Routine listing already covers the last week; this is for reaching further
    back. Nothing already in the ledger is listed twice.

    Args:
        since: ISO date 'YYYY-MM-DD'.
    """
    return _i("POST", "/sweep", body={"since": since})


@inbox.action
def get_config() -> dict:
    """Enabled inbox sources + whether their credentials are present."""
    return _i("GET", "/config")


@inbox.action(write=True)
def setup(collectors: list[str]) -> dict:
    """
    Write source config to .env with empty credential fields for the user to fill.

    Args:
        collectors: enabled sources. Valid: todoist, mattermost, ktalk.
    """
    return _i("POST", "/setup", body={"collectors": collectors})
