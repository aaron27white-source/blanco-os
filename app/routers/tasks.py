"""Tasks, calendar events and quick notes — writes land in the vault JSON."""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import store, tasks_service, vault_sync

router = APIRouter(prefix="/api/tasks", tags=["tasks"])


@router.get("", response_model=schemas.TaskBoard, summary="The whole board with counts")
def get_board(
    status: schemas.TaskStatus | None = None,
    priority: schemas.Priority | None = None,
):
    return tasks_service.board(status=status, priority=priority)


@router.post("", response_model=schemas.Task, status_code=201)
def create_task(payload: schemas.TaskCreate, db: sqlite3.Connection = Depends(get_db)):
    task = tasks_service.create_task(payload)
    store.log(db, "tasks", "create", task.title)
    return task


@router.patch("/{task_id}", response_model=schemas.Task)
def update_task(
    task_id: str, payload: schemas.TaskUpdate, db: sqlite3.Connection = Depends(get_db)
):
    task = tasks_service.update_task(task_id, payload)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    store.log(db, "tasks", "update", task.title, **payload.model_dump(exclude_none=True))
    return task


@router.post("/{task_id}/subtasks", response_model=schemas.Task, status_code=201,
             summary="Add one step to a task")
def add_subtask(
    task_id: str, payload: schemas.SubtaskCreate, db: sqlite3.Connection = Depends(get_db)
):
    task = tasks_service.add_subtask(task_id, payload)
    if not task:
        raise HTTPException(status_code=404, detail="task not found")
    store.log(db, "tasks", "add step", f"{task.title} · {payload.title}")
    return task


@router.patch("/{task_id}/subtasks/{subtask_id}", response_model=schemas.Task,
              summary="Check a step off, or rename it")
def update_subtask(
    task_id: str, subtask_id: str, payload: schemas.SubtaskUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    task = tasks_service.update_subtask(task_id, subtask_id, payload)
    if not task:
        raise HTTPException(status_code=404, detail="task or step not found")
    step = next((s for s in task.subtasks if s.id == subtask_id), None)
    if step:
        store.log(db, "tasks", "step done" if step.done else "step reopened",
                  f"{task.title} · {step.title}")
    return task


@router.delete("/{task_id}/subtasks/{subtask_id}", response_model=schemas.Task)
def delete_subtask(
    task_id: str, subtask_id: str, db: sqlite3.Connection = Depends(get_db)
):
    task = tasks_service.delete_subtask(task_id, subtask_id)
    if not task:
        raise HTTPException(status_code=404, detail="task or step not found")
    store.log(db, "tasks", "remove step", task.title)
    return task


@router.delete("/{task_id}", response_model=schemas.Ack)
def delete_task(task_id: str, db: sqlite3.Connection = Depends(get_db)):
    if not tasks_service.delete_task(task_id):
        raise HTTPException(status_code=404, detail="task not found")
    store.log(db, "tasks", "delete", task_id)
    return schemas.Ack(message="deleted")


@router.get("/timeline", response_model=list[schemas.ScheduledEvent], summary="Upcoming events")
def timeline(days: int = Query(30, ge=1, le=365)):
    return tasks_service.timeline(days_ahead=days)


events_router = APIRouter(prefix="/api/events", tags=["tasks"])


@events_router.post("", response_model=schemas.ScheduledEvent, status_code=201)
def create_event(payload: schemas.EventCreate, db: sqlite3.Connection = Depends(get_db)):
    event = tasks_service.create_event(payload)
    result = vault_sync.sync_appointments()
    store.log(db, "tasks", "schedule", event.title, date=event.date, vault_sync=result.detail)
    return event


@events_router.patch("/{event_id}", response_model=schemas.ScheduledEvent,
                     summary="Move or retitle an event (calendar drag-and-drop)")
def update_event(
    event_id: str, payload: schemas.EventUpdate, db: sqlite3.Connection = Depends(get_db)
):
    event = tasks_service.update_event(event_id, payload)
    if not event:
        raise HTTPException(status_code=404, detail="event not found")
    result = vault_sync.sync_appointments()
    store.log(db, "tasks", "reschedule", event.title, date=event.date, vault_sync=result.detail)
    return event


@events_router.delete("/{event_id}", response_model=schemas.Ack)
def delete_event(event_id: str):
    if not tasks_service.delete_event(event_id):
        raise HTTPException(status_code=404, detail="event not found")
    result = vault_sync.sync_appointments()
    return schemas.Ack(message=f"deleted · {result.detail}")


@events_router.post("/sync", response_model=schemas.Ack,
                    summary="Force the todo-list.md Appointments rebuild")
def sync_now(db: sqlite3.Connection = Depends(get_db)):
    result = vault_sync.sync_appointments()
    store.log(db, "tasks", "vault sync", result.detail)
    if not result.ok:
        raise HTTPException(status_code=409, detail=result.detail)
    return schemas.Ack(message=result.detail)


notes_router = APIRouter(prefix="/api/notes", tags=["tasks"])


@notes_router.post("", response_model=schemas.QuickNote, status_code=201)
def create_note(payload: schemas.NoteCreate, db: sqlite3.Connection = Depends(get_db)):
    note = tasks_service.create_note(payload)
    store.log(db, "tasks", "note", note.text[:60])
    return note
