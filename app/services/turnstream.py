"""What a commander is doing right now, while it is still doing it.

The console used to carry one string per pending turn: the answer so far. That
is all a reader needs *once the answer starts*, and for a 59-second OpenClaw
turn the answer starts at second 55 — so the deck showed a spinner for almost
the whole turn and the box felt like a form submission with a long POST.

A native terminal is not quiet during those 55 seconds. It says which tool it
reached for, what came back, that it is still thinking. So a turn here is a
*stream of typed events* rather than a growing string:

* ``delta``    — answer text, the only thing that lands in the message body.
* ``activity`` — the working: tool calls, status changes, thinking. Shown
                 above the answer and collapsible; never stored as the reply.
* ``usage``    — tokens and dollars, once a commander reports them.
* ``done``     — terminal, exactly one, carrying the finished row.

Events are numbered from 1 per turn so a reader that drops its connection can
say where it got to and be sent only what it missed — a reload mid-turn used
to strand a bubble as "pending" forever, because nothing re-attached.

The buffer is in memory on purpose. A half-finished turn is worth nothing after
a restart, and the DB row stays the source of truth for the finished answer;
this is the live view, not the record.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

# A turn's activity is bounded so one pathological run (a commander looping
# over a tool) cannot grow the buffer without limit. The answer text is kept
# whole regardless — it is what becomes the message body.
MAX_ACTIVITY_EVENTS = 400


@dataclass
class Event:
    seq: int
    kind: str
    data: dict


@dataclass
class Turn:
    """One in-flight turn's live state."""

    reply_id: int
    text: str = ""
    events: list[Event] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    finished: bool = False
    #: Set when a reader asked for the turn to stop, so the worker can tell a
    #: cancellation apart from a crash and keep what was already written.
    cancelled: bool = False
    _seq: int = 0

    def add(self, kind: str, data: dict) -> Event:
        self._seq += 1
        event = Event(self._seq, kind, data)
        self.events.append(event)
        # Trim the working, never the answer: `text` is held separately, so a
        # reader that reconnects late still gets the whole reply.
        if len(self.events) > MAX_ACTIVITY_EVENTS:
            keep = [e for e in self.events if e.kind != "activity"]
            activity = [e for e in self.events if e.kind == "activity"]
            self.events = sorted(
                keep + activity[-(MAX_ACTIVITY_EVENTS // 2):], key=lambda e: e.seq
            )
        return event


class Registry:
    """Every turn currently in flight, keyed by its pending reply row's id."""

    def __init__(self) -> None:
        self._turns: dict[int, Turn] = {}
        self._lock = threading.Lock()
        # Readers wait on this instead of polling on a timer: a delta wakes
        # them immediately, so the deck is limited by how fast the commander
        # writes rather than by a tick interval.
        self._changed = threading.Condition(self._lock)

    # -- writing (the worker thread) ---------------------------------------
    def open(self, reply_id: int) -> Turn:
        with self._changed:
            turn = Turn(reply_id)
            self._turns[reply_id] = turn
            self._changed.notify_all()
            return turn

    def _emit(self, reply_id: int, kind: str, data: dict) -> None:
        with self._changed:
            turn = self._turns.get(reply_id)
            if not turn:
                return
            turn.add(kind, data)
            self._changed.notify_all()

    def delta(self, reply_id: int, text: str) -> None:
        """Answer text. Appended to the reply body as well as streamed."""
        if not text:
            return
        with self._changed:
            turn = self._turns.get(reply_id)
            if not turn:
                return
            turn.text += text
            turn.add("delta", {"text": text})
            self._changed.notify_all()

    def replace(self, reply_id: int, text: str) -> None:
        """Set the answer outright, for a commander that rewrites rather than
        appends. Hermes redraws its box, so "what it has said so far" can
        genuinely shrink — appending would duplicate the reply."""
        with self._changed:
            turn = self._turns.get(reply_id)
            if turn is None or turn.text == text:
                return
            turn.text = text
            turn.add("replace", {"text": text})
            self._changed.notify_all()

    def activity(self, reply_id: int, label: str, detail: str = "",
                 icon: str = "", state: str = "", key: str = "") -> None:
        """One line of the commander's working — a tool call, a status change.

        `state` distinguishes a tool that is still running from one that has
        returned, so the deck can show a spinner on the live one the way a
        terminal does rather than a flat list of everything that happened.

        `key` identifies *which* call a later update belongs to. Without it the
        deck can only match on the tool's name, and two `Bash` calls in one
        turn collapse into a single line — a terminal shows both.
        """
        self._emit(reply_id, "activity", {
            "label": label, "detail": detail, "icon": icon,
            "state": state, "key": key,
        })

    def usage(self, reply_id: int, **fields) -> None:
        with self._changed:
            turn = self._turns.get(reply_id)
            if not turn:
                return
            turn.usage.update({k: v for k, v in fields.items() if v is not None})
            turn.add("usage", dict(turn.usage))
            self._changed.notify_all()

    def close(self, reply_id: int) -> None:
        """Mark the turn finished and wake every reader so none waits out the
        timeout on a turn that has already landed."""
        with self._changed:
            turn = self._turns.get(reply_id)
            if turn:
                turn.finished = True
            self._changed.notify_all()

    def drop(self, reply_id: int) -> None:
        with self._changed:
            self._turns.pop(reply_id, None)
            self._changed.notify_all()

    # -- reading (the SSE endpoint) ----------------------------------------
    def get(self, reply_id: int) -> Turn | None:
        with self._lock:
            return self._turns.get(reply_id)

    def text(self, reply_id: int) -> str:
        with self._lock:
            turn = self._turns.get(reply_id)
            return turn.text if turn else ""

    def since(self, reply_id: int, after_seq: int, timeout: float) -> tuple[list[Event], bool]:
        """Events numbered above `after_seq`, waiting up to `timeout` for one.

        Returns `(events, finished)`. Blocking here rather than polling is what
        makes a delta appear as fast as the commander produces it; the timeout
        only exists so the connection can send a keep-alive and re-check
        whether the client is still there.
        """
        with self._changed:
            turn = self._turns.get(reply_id)
            if turn is None:
                # The worker may not have opened the turn yet — a reader can
                # connect first. Returning immediately here spins the endpoint
                # at full speed until it does, so wait to be told.
                self._changed.wait(min(timeout, 1.0))
                turn = self._turns.get(reply_id)
                if turn is None:
                    return [], True
            if not any(e.seq > after_seq for e in turn.events) and not turn.finished:
                self._changed.wait(timeout)
                turn = self._turns.get(reply_id)
                if turn is None:
                    return [], True
            return [e for e in turn.events if e.seq > after_seq], turn.finished

    # -- cancelling ---------------------------------------------------------
    def request_cancel(self, reply_id: int) -> bool:
        """Flag a turn as cancelled. The worker owns the actual kill."""
        with self._changed:
            turn = self._turns.get(reply_id)
            if not turn:
                return False
            turn.cancelled = True
            self._changed.notify_all()
            return True

    def is_cancelled(self, reply_id: int) -> bool:
        with self._lock:
            turn = self._turns.get(reply_id)
            return bool(turn and turn.cancelled)


#: One registry per process. The worker threads and the SSE endpoints all live
#: in the same uvicorn process, so this needs no coordination beyond its lock.
turns = Registry()
