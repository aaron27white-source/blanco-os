"""Talking to OpenClaw over its ACP bridge instead of `openclaw agent --json`.

`openclaw agent --json` prints banners, thinks for a minute, and then emits one
JSON object. There is nothing partial to show, so the console could only spin.
Measured on this box: **59s for a one-word answer, silent throughout.**

`openclaw acp` is the same Gateway behind a streaming protocol — JSON-RPC over
stdio, with `session/update` notifications as the agent works. The same turn
measured **31s, with the answer arriving in chunks**, because the bridge starts
delivering as the model writes rather than at exit.

Two things make this safe to swap in rather than a rewrite of the world:

* **The session key is the same one the deck already uses.** `--session
  agent:<agent>:deck-<n>` is exactly what `openclaw agent --session-key` took,
  so threads keep their continuity across the change — no re-cold-starting
  every conversation on the box.
* **A turn is still one process.** The bridge is started, prompted once, and
  shut down, so a wedged turn cannot outlive its request the way a resident
  process could.

The cost of that last choice is the bridge's own boot: of the 31s, ~23s is
`initialize` and only ~7s is the answer. Holding one bridge open per thread
would make an OpenClaw turn feel roughly as fast as a local shell, and this
module is written so that is a lifecycle change rather than a protocol one.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
from dataclasses import dataclass

log = logging.getLogger(__name__)

# The bridge answers `initialize` only once the Gateway is reachable, which on
# a cold box is the slowest part of a turn. Generous, because failing here
# looks identical to a broken OpenClaw install.
INITIALIZE_TIMEOUT = 90.0
PROTOCOL_VERSION = 1

# Which ACP updates carry the answer, and which carry the working. Anything not
# listed is protocol bookkeeping the reader never needs to see.
ANSWER_UPDATES = {"agent_message_chunk"}
THOUGHT_UPDATES = {"agent_thought_chunk"}


@dataclass
class Result:
    text: str
    error: str = ""
    stop_reason: str = ""


def _content_text(content) -> str:
    """Flatten an ACP content block, or a list of them, to plain text."""
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, list):
        return "".join(_content_text(c) for c in content)
    return ""


class Bridge:
    """One `openclaw acp` process, driven for a single turn.

    Not thread-safe by design: a turn owns its bridge, and the console already
    serialises turns within a thread.
    """

    def __init__(self, binary: str, session_key: str, cwd: str) -> None:
        self.argv = [binary, "acp", "--session", session_key]
        self.cwd = cwd
        self.proc: subprocess.Popen | None = None
        self._next_id = 0
        self._stderr: list[str] = []
        self._session_id = ""

    # -- lifecycle ---------------------------------------------------------
    def __enter__(self) -> "Bridge":
        self.proc = subprocess.Popen(
            self.argv, cwd=self.cwd,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, bufsize=1,
            # Its own process group: killing the wrapper alone would leave the
            # embedded runner holding the session open.
            start_new_session=True,
        )
        # stderr is drained continuously. Reading it only at the end lets a
        # chatty bridge fill the 64KB pipe buffer and block itself forever,
        # which presents as a turn that hangs until the timeout.
        threading.Thread(target=self._drain_stderr, daemon=True).start()
        return self

    def __exit__(self, *exc) -> None:
        self.kill()

    def _drain_stderr(self) -> None:
        proc = self.proc
        if not proc or not proc.stderr:
            return
        try:
            for line in proc.stderr:
                # Bounded: a bridge that logs in a loop must not become the
                # reason the box runs out of memory.
                if len(self._stderr) < 200:
                    self._stderr.append(line.rstrip())
        except (OSError, ValueError):
            pass

    def kill(self) -> None:
        proc = self.proc
        if not proc or proc.poll() is not None:
            return
        import os
        import signal
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (OSError, ProcessLookupError):
            proc.kill()

    def stderr_tail(self) -> str:
        return "\n".join(self._stderr[-5:])

    # -- protocol ----------------------------------------------------------
    def _send(self, method: str, params: dict, request: bool = True) -> int | None:
        self._next_id += 1
        message: dict = {"jsonrpc": "2.0", "method": method, "params": params}
        if request:
            message["id"] = self._next_id
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(message) + "\n")
        self.proc.stdin.flush()
        return self._next_id if request else None

    def _reply(self, request_id, result: dict) -> None:
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}) + "\n"
        )
        self.proc.stdin.flush()

    def _messages(self):
        """Every JSON-RPC message the bridge writes, in order."""
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue

    def _await_result(self, request_id: int, on_message=None) -> dict:
        """Pump messages until the response to `request_id` arrives."""
        for message in self._messages():
            if on_message:
                on_message(message)
            if message.get("id") == request_id:
                if "error" in message:
                    raise RuntimeError(
                        str((message["error"] or {}).get("message") or "acp error")
                    )
                return message.get("result") or {}
        raise RuntimeError("the ACP bridge closed before answering")

    def start_session(self, cwd: str) -> str:
        init = self._send("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "clientCapabilities": {"fs": {"readTextFile": False, "writeTextFile": False}},
        })
        self._await_result(init)
        new = self._send("session/new", {"cwd": cwd, "mcpServers": []})
        self._session_id = str((self._await_result(new)).get("sessionId") or "")
        return self._session_id

    def prompt(self, text: str, on_update, on_permission) -> Result:
        """Send one prompt and pump updates until the turn stops."""
        request_id = self._send("session/prompt", {
            "sessionId": self._session_id,
            "prompt": [{"type": "text", "text": text}],
        })

        answer: list[str] = []

        def handle(message: dict) -> None:
            method = message.get("method")
            # The bridge asks *us* things too — permission being the one that
            # matters. An unanswered request stalls the turn until timeout.
            if method == "session/request_permission":
                option_id = on_permission(message.get("params") or {})
                self._reply(message.get("id"), {
                    "outcome": ({"outcome": "selected", "optionId": option_id}
                                if option_id else {"outcome": "cancelled"})
                })
                return
            if method != "session/update":
                return
            update = (message.get("params") or {}).get("update") or {}
            kind = update.get("sessionUpdate") or ""
            if kind in ANSWER_UPDATES:
                chunk = _content_text(update.get("content"))
                if chunk:
                    answer.append(chunk)
                    on_update("delta", {"text": chunk})
            elif kind in THOUGHT_UPDATES:
                on_update("thought", {"text": _content_text(update.get("content"))})
            elif kind in {"tool_call", "tool_call_update"}:
                on_update("tool", {
                    "id": str(update.get("toolCallId") or ""),
                    "title": str(update.get("title") or update.get("kind") or "tool"),
                    "status": str(update.get("status") or ""),
                    "raw": update,
                })
            elif kind == "plan":
                on_update("plan", {"entries": update.get("entries") or []})
            elif kind == "usage_update":
                on_update("usage", {
                    "used": update.get("used"), "size": update.get("size"),
                })

        result = self._await_result(request_id, on_message=handle)
        return Result(text="".join(answer), stop_reason=str(result.get("stopReason") or ""))

    def cancel(self) -> None:
        """Ask the turn to stop the way Esc does in a terminal."""
        try:
            self._send("session/cancel", {"sessionId": self._session_id}, request=False)
        except (OSError, ValueError, AssertionError):
            pass
