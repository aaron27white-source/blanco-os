"""The console — talking to the three commanders and their sub-agents.

A turn is a cold CLI start every time, because each binary boots a runner per
invocation. Blocking a request for that long would freeze the deck, so a send
returns immediately with a `pending` assistant row and a background thread
fills it in. The deck watches that turn over SSE rather than re-polling the
timeline.

What it watches has changed. The stream used to carry one string — the answer
so far — which is all a reader needs *once the answer starts*; on an OpenClaw
turn that was second 55 of 59, so the console was a spinner for the whole turn
and the box felt like a form submission with a long POST. Now each commander is
driven so that its working is visible, and the turn is a stream of typed events
(see `turnstream`):

* **OpenClaw** goes over the ACP bridge (`acp.py`) rather than
  `openclaw agent --json`, which is silent until it exits. Same session key,
  same Gateway; measured 31s against 59s, and the answer arrives in chunks.
* **Claude Code** already spoke stream-json — the deck was only reading the
  text deltas out of it and dropping every tool call, result and cost.
* **Hermes** is no longer run with `-Q`, whose own help says it suppresses
  "banner, spinner, and tool previews".

A turn can also be stopped now, keeps what it wrote when it is, and survives a
reload: events are numbered so a reconnecting reader resumes where it left off.

Two things this deliberately does *not* do:

* **Mix commanders inside a session.** A thread carries one commander's native
  conversation id, and that id means nothing to the other two — a mixed thread
  looked like one conversation while actually being three cold contexts
  wearing the same scrollback. Switching commander switches session list.
* **Carry OS notices.** Those go to the alert tray, which can acknowledge them.
  A notice in the timeline scrolled away between two questions and was gone.

Everything commander-specific — rosters, argv, reply parsing — lives in
`commanders`; this module owns storage, threading and session continuity.
"""

from __future__ import annotations

import logging
import os
import signal
import sqlite3
import subprocess
import threading
import time
from typing import Callable

from app import schemas
from app.config import get_settings
from app.db import connect
from app.services import acp, commanders, turnstream

log = logging.getLogger(__name__)

DEFAULT_ENGINE = commanders.DEFAULT_ENGINE

# Everything a turn is doing while it is still doing it. The worker thread
# writes events, the SSE endpoint reads them. In memory on purpose: a
# half-finished answer is worth nothing after a restart, and the DB row stays
# the source of truth for the finished one.
turns = turnstream.turns

# How to stop each running turn, keyed by its pending row's id. A turn used to
# be unstoppable — a wedged commander held the console's Send button disabled
# for the full seven-minute ceiling with no way to take the box back.
_CANCELLERS: dict[int, Callable[[], None]] = {}
_CANCEL_LOCK = threading.Lock()

# One turn at a time per thread. Two turns sharing a session would race on the
# same resume handle, and the loser silently answers with the other one's
# context — the exact failure `update_session` already guards against when a
# thread is repointed at a different sub-agent.
_SESSION_LOCKS: dict[int, threading.Lock] = {}
_SESSION_LOCKS_GUARD = threading.Lock()

# How long a finished turn's live buffer is kept for late reconnects.
BUFFER_GRACE_SECONDS = 120.0


def _session_lock(session_id: int) -> threading.Lock:
    with _SESSION_LOCKS_GUARD:
        return _SESSION_LOCKS.setdefault(session_id, threading.Lock())


def _register_canceller(reply_id: int, kill: Callable[[], None]) -> None:
    with _CANCEL_LOCK:
        _CANCELLERS[reply_id] = kill


def _forget_canceller(reply_id: int) -> None:
    with _CANCEL_LOCK:
        _CANCELLERS.pop(reply_id, None)


def partial(reply_id: int) -> str:
    """Whatever the commander has written so far, or "" if nothing yet."""
    return turns.text(reply_id)


def cancel(conn: sqlite3.Connection, reply_id: int) -> bool:
    """Stop a turn the way Esc does in a terminal, keeping what it wrote.

    The worker still owns the row: it sees the cancelled flag, records whatever
    text had arrived and marks the turn done rather than failed, so a long
    answer cut short is kept rather than thrown away.
    """
    message = get(conn, reply_id)
    if not message or message.status != "pending":
        return False
    turns.request_cancel(reply_id)
    with _CANCEL_LOCK:
        kill = _CANCELLERS.get(reply_id)
    if kill:
        try:
            kill()
        except OSError:
            log.warning("could not signal the turn for reply %s", reply_id)
    return True


def pending_replies(conn: sqlite3.Connection) -> list[schemas.ChatMessage]:
    """Turns still in flight. The deck asks on load so a reply that was
    running when the page was reloaded is picked back up rather than left
    spinning forever."""
    rows = conn.execute(
        "SELECT * FROM chat_messages WHERE status = 'pending' ORDER BY id"
    ).fetchall()
    return [_row(r) for r in rows]


def turn_timeout() -> float:
    return get_settings().turn_timeout_seconds


# --------------------------------------------------------------------------
# rows
# --------------------------------------------------------------------------
def _row(r: sqlite3.Row) -> schemas.ChatMessage:
    return schemas.ChatMessage(
        id=r["id"],
        role=r["role"],
        agent=r["agent"],
        body=r["body"],
        status=r["status"],
        error=r["error"],
        duration_ms=r["duration_ms"],
        engine=r["engine"],
        session_id=r["session_id"],
        created_at=r["created_at"],
    )


def _session_row(r: sqlite3.Row) -> schemas.ChatSessionInfo:
    return schemas.ChatSessionInfo(
        id=r["id"],
        label=r["label"],
        engine=r["engine"],
        agent=r["agent"],
        created_at=r["created_at"],
        # OpenClaw threads by a key we choose, so they resume from turn one.
        resumable=bool(r["native_session_id"]) or r["engine"] == commanders.OPENCLAW.id,
    )


# --------------------------------------------------------------------------
# sessions
# --------------------------------------------------------------------------
def list_sessions(
    conn: sqlite3.Connection, engine: str | None = None
) -> list[schemas.ChatSessionInfo]:
    """Every session, or one commander's — which is how the deck reads it, since
    the session tabs belong to the selected commander."""
    if engine:
        rows = conn.execute(
            "SELECT * FROM chat_sessions WHERE engine = ? ORDER BY id", (engine,)
        ).fetchall()
    else:
        rows = conn.execute("SELECT * FROM chat_sessions ORDER BY id").fetchall()
    return [_session_row(r) for r in rows]


def get_session(conn: sqlite3.Connection, session_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,)).fetchone()
    if not row:
        raise LookupError(f"no session {session_id}")
    return row


def ensure_session(conn: sqlite3.Connection, engine: str) -> schemas.ChatSessionInfo:
    """The commander's first session, created on demand.

    Switching to a commander for the first time should land in a usable thread,
    not an empty tab strip with a "+" the user has to find.
    """
    existing = list_sessions(conn, engine)
    if existing:
        return existing[0]
    return create_session(conn, "Main", engine, commanders.get(engine).primary_id)


def create_session(
    conn: sqlite3.Connection, label: str, engine: str, agent: str = ""
) -> schemas.ChatSessionInfo:
    commander = commanders.get(engine)
    agent_id = commanders.resolve(engine, agent or commander.primary_id).id
    label = label.strip() or f"{commander.name} {len(list_sessions(conn, engine)) + 1}"
    cur = conn.execute(
        "INSERT INTO chat_sessions (label, engine, agent) VALUES (?, ?, ?)",
        (label, commander.id, agent_id),
    )
    conn.commit()
    return _session_row(get_session(conn, cur.lastrowid))


def update_session(
    conn: sqlite3.Connection,
    session_id: int,
    label: str | None = None,
    agent: str | None = None,
    forget: bool = False,
) -> schemas.ChatSessionInfo:
    """Rename a session, repoint it at another sub-agent, or start it fresh.

    Repointing drops the remembered conversation id along with it: a resume
    handle belongs to the sub-agent that produced it, and handing one
    sub-agent's thread to another is how you get an agent answering with
    someone else's context.
    """
    row = get_session(conn, session_id)
    native = "" if (forget or agent is not None) else row["native_session_id"]
    conn.execute(
        """UPDATE chat_sessions
              SET label = ?, agent = ?, native_session_id = ?, updated_at = datetime('now')
            WHERE id = ?""",
        (
            label.strip() if label is not None else row["label"],
            commanders.resolve(row["engine"], agent).id if agent is not None else row["agent"],
            native,
            session_id,
        ),
    )
    conn.commit()
    return _session_row(get_session(conn, session_id))


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    row = get_session(conn, session_id)
    remaining = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_sessions WHERE engine = ?", (row["engine"],)
    ).fetchone()["n"]
    if remaining <= 1:
        raise ValueError(f"can't close the last {row['engine']} session")
    conn.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
    conn.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))
    conn.commit()


def _remember_native_session(session_id: int, native: str) -> None:
    """Store the id the commander handed back, once. Owns its own connection —
    this runs on the worker thread, after the request is long gone."""
    if not native:
        return
    conn = connect()
    try:
        conn.execute(
            "UPDATE chat_sessions SET native_session_id = ? WHERE id = ? AND native_session_id = ''",
            (native, session_id),
        )
        conn.commit()
    except sqlite3.Error:
        log.warning("could not persist native session id for session %s", session_id)
    finally:
        conn.close()


def _session_key(session_id: int) -> str:
    """A stable, collision-proof handle for OpenClaw's `--session-key`.

    The row id alone would be reused after a delete-and-recreate, quietly
    handing a brand new thread the previous one's context.
    """
    return f"deck-{session_id}"


# --------------------------------------------------------------------------
# timeline
# --------------------------------------------------------------------------
def history(
    conn: sqlite3.Connection, limit: int = 60, session_id: int | None = None
) -> list[schemas.ChatMessage]:
    """Newest `limit` messages, oldest first — one session's, or all of them."""
    if session_id is None:
        rows = conn.execute(
            "SELECT * FROM chat_messages ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id DESC LIMIT ?",
            (session_id, limit),
        ).fetchall()
    return [_row(r) for r in reversed(rows)]


def get(conn: sqlite3.Connection, message_id: int) -> schemas.ChatMessage | None:
    row = conn.execute("SELECT * FROM chat_messages WHERE id = ?", (message_id,)).fetchone()
    return _row(row) if row else None


def clear(conn: sqlite3.Connection, session_id: int | None = None) -> int:
    """Wipe one session's history, or the whole console.

    The remembered conversation id goes with it. Leaving it would give the
    commander a memory of everything the deck just told the user was gone.
    """
    if session_id is None:
        count = conn.execute("DELETE FROM chat_messages").rowcount
        conn.execute("UPDATE chat_sessions SET native_session_id = ''")
    else:
        count = conn.execute(
            "DELETE FROM chat_messages WHERE session_id = ?", (session_id,)
        ).rowcount
        conn.execute(
            "UPDATE chat_sessions SET native_session_id = '' WHERE id = ?", (session_id,)
        )
    conn.commit()
    return count


# --------------------------------------------------------------------------
# roster / engines
# --------------------------------------------------------------------------
def known_agents(engine: str | None = None) -> list[schemas.ChatAgent]:
    """Who you can address — one commander's roster, or all three."""
    found = commanders.roster(engine) if engine else commanders.full_roster()
    return [
        schemas.ChatAgent(
            id=a.id, engine=a.engine, name=a.name, emoji=a.emoji,
            role=a.role, primary=a.primary, source=a.source,
        )
        for a in found
    ]


def known_engines() -> list[schemas.ChatEngineInfo]:
    """The three commander tabs, each reporting whether it is actually here."""
    settings = get_settings()
    models = {
        commanders.OPENCLAW.id: settings.openclaw_model,
        commanders.CLAUDE.id: settings.claude_model,
        commanders.HERMES.id: settings.hermes_model,
    }
    out = []
    for commander in commanders.ALL:
        available = commander.available()
        out.append(
            schemas.ChatEngineInfo(
                id=commander.id,
                name=commander.name,
                emoji=commander.emoji,
                note=commander.note,
                binary=commander.binary(),
                available=available,
                model=models.get(commander.id, ""),
                # Probing a missing binary's roster would shell out three times
                # to report zero.
                agent_count=len(commanders.roster(commander.id)) if available else 0,
            )
        )
    return out


# --------------------------------------------------------------------------
# turns
# --------------------------------------------------------------------------
def send(conn: sqlite3.Connection, payload: schemas.ChatSend) -> schemas.ChatExchange:
    """Store the question, queue the answer, return both immediately."""
    session = get_session(conn, payload.session_id)
    # The session owns the commander and sub-agent, not the payload: a message
    # typed into a Hermes thread is a Hermes turn even if the client is stale.
    engine = session["engine"]
    agent = commanders.resolve(engine, payload.agent or session["agent"])

    cur = conn.execute(
        "INSERT INTO chat_messages (role, agent, body, engine, session_id) "
        "VALUES ('user', ?, ?, ?, ?)",
        (agent.id, payload.message, engine, session["id"]),
    )
    user_id = cur.lastrowid
    cur = conn.execute(
        "INSERT INTO chat_messages (role, agent, body, status, engine, session_id) "
        "VALUES ('assistant', ?, '', 'pending', ?, ?)",
        (agent.id, engine, session["id"]),
    )
    reply_id = cur.lastrowid
    conn.commit()

    threading.Thread(
        target=_run_turn,
        args=(reply_id, session["id"], engine, agent, payload.message,
              session["native_session_id"]),
        daemon=True,
    ).start()

    return schemas.ChatExchange(message=get(conn, user_id), reply=get(conn, reply_id))


def _run_turn(reply_id: int, session_id: int, engine: str, agent: commanders.Subagent,
              message: str, native_session_id: str) -> None:
    """Background worker. Owns its own connection — it outlives the request."""
    started = time.monotonic()
    text, native, error = "", "", ""
    turn = turns.open(reply_id)

    # Serialised per thread: a second turn waits for the first rather than
    # racing it onto the same resume handle.
    lock = _session_lock(session_id)
    if lock.locked():
        turns.activity(reply_id, "queued", "waiting for this thread's previous turn",
                       icon="wait")
    with lock:
        try:
            if commanders.get(engine) is commanders.OPENCLAW:
                text, native, error = _invoke_openclaw(
                    agent, message, _session_key(session_id), reply_id)
            else:
                text, native, error = _invoke(engine, agent, message, native_session_id,
                                              _session_key(session_id), reply_id)
        except subprocess.TimeoutExpired:
            error = f"{agent.name} did not answer within {turn_timeout():.0f}s"
        except Exception as exc:  # noqa: BLE001 — see below
            # Deliberately every exception, not a list of the expected ones.
            # The row is only moved off `pending` after this block, so anything
            # that escapes here leaves the turn pending forever: the Send button
            # stays disabled and the bubble spins with no way back short of a
            # restart. A wrong answer with an error on it beats a dead thread.
            error = f"{type(exc).__name__}: {exc}"
            log.exception("turn %s failed", reply_id)
        finally:
            _forget_canceller(reply_id)

    # A cancelled turn is not a failed one. Whatever had already been written
    # is the answer as far as the reader is concerned — throwing it away was
    # the single most annoying thing about stopping a long turn.
    if turn.cancelled:
        text = text or turn.text.strip()
        error = "" if text else "stopped before it answered"
        if text:
            text += "\n\n_(stopped)_"
    elif error and not text:
        # Same for a turn that died mid-answer: a partial reply beats an empty
        # bubble with an error on it.
        salvaged = turn.text.strip()
        if salvaged:
            text = salvaged + "\n\n_(interrupted — " + error + ")_"
            error = ""

    if native:
        _remember_native_session(session_id, native)

    duration = int((time.monotonic() - started) * 1000)
    conn = connect()
    try:
        conn.execute(
            """UPDATE chat_messages
                  SET body = ?, status = ?, error = ?, duration_ms = ?
                WHERE id = ?""",
            (text, "done" if text else "failed", error, duration, reply_id),
        )
        conn.commit()
    finally:
        conn.close()
        # Wake every reader before dropping the buffer, or an SSE connection
        # sits waiting on a turn that has already landed.
        turns.close(reply_id)
        # The buffer outlives the turn briefly so a browser that reconnects
        # just as the answer lands still gets its `done`, rather than finding
        # nothing and falling back to a refresh.
        threading.Timer(BUFFER_GRACE_SECONDS, turns.drop, [reply_id]).start()


def _invoke_openclaw(agent: commanders.Subagent, message: str, session_key: str,
                     reply_id: int) -> tuple[str, str, str]:
    """One OpenClaw turn over the ACP bridge, streaming as it goes.

    `openclaw agent --json` is silent for the whole turn — measured 59s for a
    one-word answer — because it emits a single JSON object at exit. The bridge
    is the same Gateway speaking a streaming protocol, and the same session key
    goes to both, so this is a change of transport rather than of thread.

    A bridge that cannot start falls back to the old argv, because a slow
    answer is still an answer.
    """
    commander = commanders.OPENCLAW
    key = commanders.openclaw_session_key(agent, session_key)
    workspace = str(get_settings().workspace_path)

    def on_update(kind: str, data: dict) -> None:
        if kind == "delta":
            turns.delta(reply_id, data.get("text", ""))
        elif kind == "thought":
            turns.activity(reply_id, "thinking", data.get("text", ""), icon="thought")
        elif kind == "tool":
            turns.activity(reply_id, data.get("title", "tool"), "",
                           icon="tool", state=data.get("status", ""),
                           key=data.get("id", ""))
        elif kind == "plan":
            turns.activity(reply_id, "plan",
                           f"{len(data.get('entries') or [])} steps", icon="plan")
        elif kind == "usage":
            turns.usage(reply_id, context_used=data.get("used"),
                        context_size=data.get("size"))

    def on_permission(params: dict) -> str:
        """Nothing here can approve on Blanco's behalf.

        A turn fired from a chat box should answer and read, not silently
        rewrite the filesystem — the same posture the Claude tab has always
        taken. Declining is surfaced as working so the reason a turn stopped
        short is visible, instead of the reply just being oddly cautious.
        """
        title = str(((params.get("toolCall") or {}).get("title")) or "a tool")
        turns.activity(reply_id, "permission declined", title, icon="denied")
        return ""

    try:
        with acp.Bridge(commander.binary(), key, workspace) as bridge:
            _register_canceller(reply_id, lambda: (bridge.cancel(), bridge.kill()))
            turns.activity(reply_id, "connecting", "openclaw acp", icon="wait")
            bridge.start_session(workspace)
            turns.activity(reply_id, "connected", key, icon="ok")
            result = bridge.prompt(message, on_update, on_permission)
    except (OSError, RuntimeError) as exc:
        # Stopping a turn closes the bridge, which looks from here exactly like
        # a bridge that failed. Falling back then would re-run the whole turn
        # the user just cancelled — so a cancel keeps what it had and stops.
        if turns.is_cancelled(reply_id):
            return turns.text(reply_id), "", ""
        log.warning("ACP bridge unusable (%s); falling back to `openclaw agent`", exc)
        turns.activity(reply_id, "bridge unavailable", "falling back to openclaw agent",
                       icon="warn")
        return _invoke(commander.id, agent, message, "", session_key, reply_id)

    if turns.is_cancelled(reply_id):
        return result.text, "", ""
    if not result.text:
        return "", "", f"OpenClaw ended the turn without an answer ({result.stop_reason or 'no reason given'})"
    # The run id is per-turn, not per-conversation, so continuity stays with
    # the session key we sent rather than with anything handed back.
    return result.text, "", ""


def _invoke(engine: str, agent: commanders.Subagent, message: str,
            native_session_id: str, session_key: str,
            reply_id: int) -> tuple[str, str, str]:
    """Run one turn as a subprocess, publishing the working as it arrives.

    stdout is read line by line rather than captured whole, so the answer
    appears the way it would in a terminal instead of arriving all at once
    after a minute of spinner. `commanders.Reader` owns the per-CLI
    differences; this only has to pump lines into it and enforce the ceiling.
    """
    turn = commanders.build_turn(engine, agent, message, native_session_id, session_key)

    def sink(kind: str, data: dict) -> None:
        if kind == "delta":
            turns.delta(reply_id, data.get("text", ""))
        elif kind == "replace":
            turns.replace(reply_id, data.get("text", ""))
        elif kind == "thought":
            turns.activity(reply_id, "thinking", data.get("text", ""), icon="thought")
        elif kind == "status":
            turns.activity(reply_id, data.get("text", ""), "", icon="status")
        elif kind == "interim":
            turns.activity(reply_id, "said", data.get("text", ""), icon="note")
        elif kind == "tool":
            turns.activity(reply_id, data.get("title", "tool"),
                           data.get("detail") or data.get("result", ""),
                           icon="tool", state=data.get("status", ""),
                           key=data.get("id", ""))
        elif kind == "usage":
            turns.usage(reply_id, **data)

    reader = commanders.Reader(engine, sink)
    deadline = time.monotonic() + turn_timeout()
    stderr_lines: list[str] = []

    proc = subprocess.Popen(
        turn.argv, cwd=turn.cwd, env={**os.environ, **turn.env},
        # stdin is closed rather than inherited. Claude waits three seconds for
        # input that is never coming and says so on stderr — three seconds
        # added to every single turn on this tab.
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        # Its own process group, so cancelling kills the CLI *and* whatever it
        # spawned. Killing the wrapper alone leaves the real work running.
        start_new_session=True,
    )
    _register_canceller(reply_id, lambda: _terminate(proc))

    def drain_stderr() -> None:
        # Read continuously. Reading stderr only after stdout is exhausted lets
        # a chatty CLI fill the 64KB pipe buffer and block itself forever,
        # which presents as a turn that hangs until the ceiling.
        try:
            for line in proc.stderr:
                if len(stderr_lines) < 200:
                    stderr_lines.append(line)
        except (OSError, ValueError):
            pass

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    def watchdog() -> None:
        # The ceiling has to be wall-clock. Waiting on the process only after
        # stdout closes bounds nothing: a CLI that keeps writing slowly, or one
        # that never closes the pipe, runs forever.
        while proc.poll() is None:
            if time.monotonic() > deadline or turns.is_cancelled(reply_id):
                _terminate(proc)
                return
            time.sleep(0.25)

    threading.Thread(target=watchdog, daemon=True).start()

    # Kept alongside the reader's parse so a failure printed *outside* the
    # answer box is still visible afterwards — see the fatal_output check below.
    # Bounded: a chatty CLI must not be able to grow this without limit.
    raw_head: list[str] = []
    try:
        for line in proc.stdout:
            if len(raw_head) < 400:
                raw_head.append(line)
            reader.feed(line)
    finally:
        proc.wait()
        stderr_thread.join(timeout=2)

    stderr = "".join(stderr_lines)
    timed_out = time.monotonic() > deadline and not turns.is_cancelled(reply_id)
    if timed_out:
        raise subprocess.TimeoutExpired(turn.argv, turn_timeout())

    text, native, error = reader.finish(stderr, proc.returncode)

    # A commander that never reached a model still exits 0 and prints the
    # reason as ordinary output, so nothing above this catches it.
    #
    # `text` is not a useful guard here: with no answer box to parse, the
    # Hermes reader falls back to raw output, so `text` came back as the
    # echoed prompt plus the error — non-empty, and stored as though it were
    # the reply. Hence the text is *discarded* rather than kept: a turn that
    # never reached a model did not answer, and `_run_turn` only records a
    # turn as failed when there is no text to show.
    if not error:
        if fatal := commanders.fatal_output(
            commanders.strip_ansi("".join(raw_head) + stderr)
        ):
            error = f"{agent.name} could not start: {fatal}"
            text = ""

    # Hermes prints the answer and nothing else, so its conversation id has to
    # be read back out of its own store.
    if not error and not native and commanders.get(engine) is commanders.HERMES:
        native = commanders.latest_hermes_session(turn.cwd)

    return text, native, error


def _terminate(proc: subprocess.Popen) -> None:
    """Stop a CLI and everything it started.

    A wedged CLI holds an embedded runner and its context open, and `kill()`
    on the wrapper alone leaves that child behind — so the signal goes to the
    whole process group.
    """
    if proc.poll() is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (OSError, ProcessLookupError):
        proc.kill()
        return
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()


def pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM chat_messages WHERE status = 'pending'"
    ).fetchone()
    return int(row["n"])
