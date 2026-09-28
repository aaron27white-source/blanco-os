"""Tasks, scheduled events and quick notes.

Source of truth is the vault's `02-areas/todo-data.json` — the same file the
Command Center HTML and the 4-hourly sync cron already use. We read and write
that file directly so all three surfaces stay in agreement.
"""

from __future__ import annotations

import uuid
from datetime import date

from app import schemas
from app.config import get_settings
from app.vault import modified_at, read_json, write_json

EMPTY = {"version": 1, "last_synced": None, "events": [], "boardTasks": [], "notes": [], "activity": []}


def _load() -> dict:
    data = read_json(get_settings().todo_data_file, default=None)
    if not isinstance(data, dict):
        return dict(EMPTY)
    for key in ("events", "boardTasks", "notes", "activity"):
        if not isinstance(data.get(key), list):
            data[key] = []
    return data


def _save(data: dict) -> None:
    data["last_synced"] = date.today().isoformat()
    write_json(get_settings().todo_data_file, data)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _to_subtask(raw: dict) -> schemas.Subtask | None:
    """One step out of the vault JSON, or None if it is too broken to show.

    Hand-edited JSON is a first-class input here — the vault file is Blanco's
    and Sweet Jones writes to it too — so a malformed entry is dropped rather
    than taking the whole board down with a validation error.
    """
    if not isinstance(raw, dict):
        return None
    title = str(raw.get("title") or "").strip()
    if not title:
        return None
    return schemas.Subtask(
        id=str(raw.get("id") or _new_id("st")),
        title=title,
        done=bool(raw.get("done")),
    )


def _to_task(raw: dict) -> schemas.Task:
    status = raw.get("status", "todo")
    if status not in ("todo", "in_progress", "blocked", "done"):
        status = "done" if status in ("complete", "completed") else "todo"
    priority = raw.get("priority", "medium")
    if priority not in ("high", "medium", "low"):
        priority = "medium"
    return schemas.Task(
        id=str(raw.get("id") or _new_id("bt")),
        title=str(raw.get("title", "")),
        status=status,
        priority=priority,
        due_date=raw.get("dueDate") or raw.get("due_date") or None,
        notes=str(raw.get("notes") or ""),
        agent=bool(raw.get("agent")),
        agent_reason=str(raw.get("agentReason") or raw.get("agent_reason") or ""),
        subtasks=[s for s in map(_to_subtask, raw.get("subtasks") or []) if s],
    )


def _from_task(task: schemas.Task) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "priority": task.priority,
        "status": task.status,
        "dueDate": task.due_date or "",
        "notes": task.notes,
        "agent": task.agent,
        "agentReason": task.agent_reason,
        # Written even when empty, so the key's absence never means anything.
        "subtasks": [s.model_dump() for s in task.subtasks],
    }


def _to_event(raw: dict) -> schemas.ScheduledEvent:
    return schemas.ScheduledEvent(
        id=str(raw.get("id") or _new_id("ev")),
        title=str(raw.get("title", "")),
        date=str(raw.get("date", "")),
        time=str(raw.get("time") or ""),
        agent=bool(raw.get("agent")),
        reason=str(raw.get("reason") or ""),
    )


def _to_note(raw: dict) -> schemas.QuickNote:
    return schemas.QuickNote(
        id=str(raw.get("id") or _new_id("n")),
        text=str(raw.get("text", "")),
        date=str(raw.get("date") or date.today().isoformat()),
    )


# --- reads ------------------------------------------------------------------


def board(status: str | None = None, priority: str | None = None) -> schemas.TaskBoard:
    data = _load()
    tasks = [_to_task(t) for t in data["boardTasks"]]
    if status:
        tasks = [t for t in tasks if t.status == status]
    if priority:
        tasks = [t for t in tasks if t.priority == priority]

    all_tasks = [_to_task(t) for t in data["boardTasks"]]
    today = date.today().isoformat()
    counts = {
        "total": len(all_tasks),
        "todo": sum(1 for t in all_tasks if t.status == "todo"),
        "in_progress": sum(1 for t in all_tasks if t.status == "in_progress"),
        "blocked": sum(1 for t in all_tasks if t.status == "blocked"),
        "done": sum(1 for t in all_tasks if t.status == "done"),
        "overdue": sum(
            1 for t in all_tasks if t.status != "done" and t.due_date and t.due_date < today
        ),
        "due_today": sum(1 for t in all_tasks if t.status != "done" and t.due_date == today),
        "agent_proposed": sum(1 for t in all_tasks if t.agent and t.status == "todo"),
    }
    return schemas.TaskBoard(
        last_synced=data.get("last_synced"),
        source_file=str(get_settings().todo_data_file),
        tasks=sorted(tasks, key=_task_sort_key),
        events=sorted((_to_event(e) for e in data["events"]), key=lambda e: (e.date, e.time)),
        notes=[_to_note(n) for n in data["notes"]],
        counts=counts,
    )


def _task_sort_key(task: schemas.Task) -> tuple:
    status_rank = {"in_progress": 0, "blocked": 1, "todo": 2, "done": 3}
    prio_rank = {"high": 0, "medium": 1, "low": 2}
    return (
        status_rank.get(task.status, 4),
        task.due_date or "9999-12-31",
        prio_rank.get(task.priority, 3),
    )


def timeline(days_ahead: int = 30) -> list[schemas.ScheduledEvent]:
    today = date.today().isoformat()
    events = [_to_event(e) for e in _load()["events"]]
    upcoming = [e for e in events if e.date >= today]
    return sorted(upcoming, key=lambda e: (e.date, e.time))[: days_ahead * 5]


def last_modified() -> str | None:
    return modified_at(get_settings().todo_data_file)


# --- writes -----------------------------------------------------------------


def create_task(payload: schemas.TaskCreate) -> schemas.Task:
    data = _load()
    task = schemas.Task(id=_new_id("bt"), **payload.model_dump())
    data["boardTasks"].append(_from_task(task))
    _save(data)
    return task


def update_task(task_id: str, payload: schemas.TaskUpdate) -> schemas.Task | None:
    data = _load()
    for i, raw in enumerate(data["boardTasks"]):
        if str(raw.get("id")) != task_id:
            continue
        task = _to_task(raw)
        # A null only ever means something for due_date — dragging a task off
        # the calendar unschedules it. Anywhere else, null is "leave it alone".
        updates = {
            k: v
            for k, v in payload.model_dump(exclude_unset=True).items()
            if v is not None or k == "due_date"
        }
        merged = task.model_copy(update=updates)
        data["boardTasks"][i] = _from_task(merged)
        _save(data)
        return merged
    return None


# --- subtasks ---------------------------------------------------------------
#
# Steps live inside their parent task rather than in a table of their own: the
# vault JSON is the source of truth and it is a document, not a database. That
# also means a subtask id is only ever resolved *within* a task, so a stray id
# from the client can't reach into another task's steps.


def _write_task(data: dict, index: int, task: schemas.Task) -> schemas.Task:
    data["boardTasks"][index] = _from_task(task)
    _save(data)
    return task


def _find_task(data: dict, task_id: str) -> tuple[int, schemas.Task] | tuple[None, None]:
    for i, raw in enumerate(data["boardTasks"]):
        if str(raw.get("id")) == task_id:
            return i, _to_task(raw)
    return None, None


def add_subtask(task_id: str, payload: schemas.SubtaskCreate) -> schemas.Task | None:
    data = _load()
    index, task = _find_task(data, task_id)
    if task is None:
        return None
    # Server-side id, always. A client-supplied one is how you end up with two
    # steps that answer to the same handle.
    step = schemas.Subtask(id=_new_id("st"), title=payload.title.strip())
    task.subtasks.append(step)
    return _write_task(data, index, task)


def update_subtask(
    task_id: str, subtask_id: str, payload: schemas.SubtaskUpdate
) -> schemas.Task | None:
    data = _load()
    index, task = _find_task(data, task_id)
    if task is None:
        return None
    updates = payload.model_dump(exclude_unset=True, exclude_none=True)
    if not updates:
        return task
    for i, step in enumerate(task.subtasks):
        if step.id != subtask_id:
            continue
        task.subtasks[i] = step.model_copy(update=updates)
        # Ticking the first step off a task nobody had started is what actually
        # moving means. Finishing the last one is *not* the same claim — a task
        # is done when Blanco says so, not when its checklist runs out, because
        # the checklist is rarely the whole job.
        if updates.get("done") and task.status == "todo":
            task.status = "in_progress"
        return _write_task(data, index, task)
    return None


def delete_subtask(task_id: str, subtask_id: str) -> schemas.Task | None:
    data = _load()
    index, task = _find_task(data, task_id)
    if task is None:
        return None
    remaining = [s for s in task.subtasks if s.id != subtask_id]
    if len(remaining) == len(task.subtasks):
        return None
    task.subtasks = remaining
    return _write_task(data, index, task)


def delete_task(task_id: str) -> bool:
    data = _load()
    remaining = [t for t in data["boardTasks"] if str(t.get("id")) != task_id]
    if len(remaining) == len(data["boardTasks"]):
        return False
    data["boardTasks"] = remaining
    _save(data)
    return True


def create_event(payload: schemas.EventCreate) -> schemas.ScheduledEvent:
    data = _load()
    event = schemas.ScheduledEvent(id=_new_id("ev"), **payload.model_dump())
    data["events"].append(event.model_dump())
    _save(data)
    return event


def update_event(event_id: str, payload: schemas.EventUpdate) -> schemas.ScheduledEvent | None:
    data = _load()
    for i, raw in enumerate(data["events"]):
        if str(raw.get("id")) != event_id:
            continue
        merged = _to_event(raw).model_copy(
            update=payload.model_dump(exclude_unset=True, exclude_none=True)
        )
        data["events"][i] = merged.model_dump()
        _save(data)
        return merged
    return None


def delete_event(event_id: str) -> bool:
    data = _load()
    remaining = [e for e in data["events"] if str(e.get("id")) != event_id]
    if len(remaining) == len(data["events"]):
        return False
    data["events"] = remaining
    _save(data)
    return True


def create_note(payload: schemas.NoteCreate) -> schemas.QuickNote:
    data = _load()
    note = schemas.QuickNote(id=_new_id("n"), text=payload.text, date=date.today().isoformat())
    data["notes"].insert(0, note.model_dump())
    _save(data)
    return note
