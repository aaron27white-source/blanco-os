"""Inbox view.

Reuses the existing categorizer's own logic rather than reimplementing it —
`email_checker.py` owns the VIP list, the category rules and the importance
scoring, and it is what already feeds the Discord #emails channel. Importing it
keeps the deck and Discord telling the same story.

Nothing is stored: messages are fetched from Gmail on demand and cached in
memory briefly. The OAuth token belongs to the categorizer and is not copied.
"""

from __future__ import annotations

import importlib
import logging
import re
import sqlite3
import sys
import threading
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app import schemas
from app.config import get_settings
from app.vault import modified_at, read_json

log = logging.getLogger(__name__)

_CACHE_TTL = 90.0
# Past the TTL a cached inbox is served anyway while a refresh runs behind the
# request — a Gmail round trip is ~2.5s and this sits on the critical path of
# the home screen. Past _STALE_MAX the data is too old to pass off as current,
# so the caller waits for a real fetch (and sees any error).
_STALE_MAX = 900.0
_cache: dict[str, tuple[float, Any]] = {}
_refreshing: set[str] = set()
_lock = threading.Lock()

# Importance level -> label, mirroring email_checker.IMPORTANCE_LABELS.
FALLBACK_LABELS = {1: "low", 2: "normal", 3: "medium", 4: "important", 5: "critical"}


def _categorizer_dir() -> Path:
    return get_settings().workspace_path / "email-categorizer"


def _load_checker():
    """Import email_checker from the categorizer's directory, or None."""
    path = _categorizer_dir()
    if not (path / "email_checker.py").is_file():
        return None
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        return importlib.import_module("email_checker")
    except Exception:
        return None


def _service():
    """A Gmail API client, or None if the token isn't usable."""
    checker = _load_checker()
    if checker is None:
        return None
    try:
        return checker.get_gmail_service()
    except SystemExit:
        # get_gmail_service() calls sys.exit when token.json is missing.
        return None
    except Exception:
        return None


def status() -> schemas.EmailStatus:
    """Health of the categorizer pipeline — cheap, no Gmail call."""
    path = _categorizer_dir()
    state = read_json(path / "state.json", default={}) or {}
    categories = state.get("categories") or []
    checker = _load_checker()

    return schemas.EmailStatus(
        configured=(path / "token.json").is_file(),
        tracked_total=len(state.get("processed_uids") or []),
        last_check=state.get("last_check"),
        categories=[str(c) for c in categories],
        vip_sender_count=len(getattr(checker, "VIP_SENDERS", {})) if checker else 0,
        state_file=str(path / "state.json"),
        log_last_written=modified_at(get_settings().workspace_path / "email-categorizer" / "email-categorizer.log"),
    )


def _header(headers: list[dict], name: str) -> str:
    lowered = name.lower()
    for h in headers:
        if h.get("name", "").lower() == lowered:
            return h.get("value", "")
    return ""


def _pretty_date(raw: str) -> str:
    """RFC-2822 header -> ISO 8601. Falls back to the raw header."""
    if not raw:
        return ""
    try:
        return parsedate_to_datetime(raw).astimezone(timezone.utc).isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return raw


# --------------------------------------------------------------------------
# importance corrections
# --------------------------------------------------------------------------


def _address(from_addr: str) -> str:
    """'Dice <alerts@dice.com>' -> 'alerts@dice.com'."""
    match = re.search(r"<([^>]+)>", from_addr)
    raw = match.group(1) if match else from_addr
    return raw.strip().strip("<>").lower()


def _domain(from_addr: str) -> str:
    addr = _address(from_addr)
    return addr.split("@", 1)[1] if "@" in addr else ""


def list_rules(conn: sqlite3.Connection) -> list[schemas.EmailRule]:
    rows = conn.execute(
        "SELECT * FROM email_rules ORDER BY scope, match_value"
    ).fetchall()
    return [schemas.EmailRule(**dict(r)) for r in rows]


def upsert_rule(conn: sqlite3.Connection, payload: schemas.EmailRuleCreate) -> schemas.EmailRule:
    """Teach the OS that this sender/domain/category is worth more or less."""
    value = payload.match_value.strip().lower()
    conn.execute(
        """INSERT INTO email_rules (scope, match_value, importance, note, original_importance)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(scope, match_value) DO UPDATE SET
               importance          = excluded.importance,
               note                = excluded.note,
               original_importance = COALESCE(excluded.original_importance,
                                              email_rules.original_importance),
               updated_at          = datetime('now')""",
        (payload.scope, value, payload.importance, payload.note, payload.original_importance),
    )
    conn.commit()
    clear_cache()
    row = conn.execute(
        "SELECT * FROM email_rules WHERE scope = ? AND match_value = ?", (payload.scope, value)
    ).fetchone()
    return schemas.EmailRule(**dict(row))


def delete_rule(conn: sqlite3.Connection, rule_id: int) -> bool:
    deleted = conn.execute("DELETE FROM email_rules WHERE id = ?", (rule_id,)).rowcount
    conn.commit()
    if deleted:
        clear_cache()
    return bool(deleted)


def _rule_index(conn: sqlite3.Connection) -> dict[tuple[str, str], sqlite3.Row]:
    return {
        (r["scope"], r["match_value"]): r
        for r in conn.execute("SELECT * FROM email_rules").fetchall()
    }


def _apply_rules(rules, from_addr: str, category: str, level: int):
    """Most specific match wins: sender, then domain, then category."""
    candidates = (
        ("sender", _address(from_addr)),
        ("domain", _domain(from_addr)),
        ("category", (category or "").lower()),
    )
    for scope, value in candidates:
        if not value:
            continue
        rule = rules.get((scope, value))
        if rule:
            return int(rule["importance"]), f"{scope}:{value}", rule["id"]
    return level, "", None


def _display_name(from_addr: str) -> str:
    """'Micro1 Team <no-reply@micro1.ai>' -> 'Micro1 Team'."""
    addr = from_addr.strip()
    if "<" in addr:
        name = addr.split("<", 1)[0].strip().strip('"')
        if name:
            return name
    return addr.split("@")[0].lstrip("<") or addr


def _preview(checker, msg) -> str:
    """Body text exactly as the categorizer's own cron path derives it.

    Falls back to Gmail's snippet if the checker cannot parse the payload, so a
    malformed message degrades to a worse preview rather than no message.
    """
    try:
        text = checker.extract_body_preview(msg.get("payload", {}))
        if text:
            return text
    except Exception:  # noqa: BLE001 — snippet is a fine fallback
        log.warning("extract_body_preview failed for %s", msg.get("id"), exc_info=True)
    return msg.get("snippet", "") or ""


def _category_text(checker, msg, preview: str) -> str:
    """The long, boilerplate-stripped body the categorizer's cron path reads.

    The two verdicts have to stay identical, so this mirrors that path exactly:
    categorisation gets the whole message, importance keeps the calibrated
    200-character preview. Falls back to the preview on an older checker that
    predates the function.
    """
    try:
        text = checker.extract_body_for_category(msg.get("payload", {}))
        if text:
            return text
    except AttributeError:
        pass  # older categorizer — preview is the correct fallback
    except Exception:  # noqa: BLE001
        log.warning("extract_body_for_category failed", exc_info=True)
    return preview


def _classify(checker, msg, from_addr: str, subject: str, preview: str):
    """(category, importance, label, is_vip, vip_label) via the checker."""
    category = "Uncategorized"
    level, label, is_vip, vip_label = 2, "normal", False, ""
    try:
        result = checker.categorize_email(
            msg, from_addr, subject, _category_text(checker, msg, preview))
        # categorize_email returns (category, per-category score dict).
        if isinstance(result, tuple):
            result = result[0] if result else None
        category = str(result) if result else category
    except Exception:  # noqa: BLE001 — one bad message must not lose the inbox
        # Logged, not swallowed. Silently degrading to "Uncategorized"/normal
        # meant a change to the checker's signature would look like a quiet
        # inbox rather than a broken classifier.
        log.warning("categorize_email failed for %s", from_addr, exc_info=True)
    try:
        # The raw message carries the headers the checker uses to tell bulk mail
        # from a note a person typed — List-Unsubscribe, Precedence, campaign
        # ids. Older checkers take four arguments, so fall back rather than
        # breaking if this one predates the shape check.
        try:
            result = checker.get_importance(subject, preview, from_addr, category, msg)
        except TypeError:
            result = checker.get_importance(subject, preview, from_addr, category)
        if isinstance(result, tuple) and len(result) >= 4:
            level, label, is_vip, vip_label = result[0], result[1], bool(result[2]), result[3] or ""
        elif isinstance(result, tuple) and len(result) == 2:
            level, label = result
    except Exception:  # noqa: BLE001
        log.warning("get_importance failed for %s", from_addr, exc_info=True)
    return category, int(level), str(label or FALLBACK_LABELS.get(level, "normal")), is_vip, str(vip_label)


def _spawn_refresh(key: str, limit: int, unread_only: bool, query: str, category: str) -> None:
    """Re-fetch `key` in the background, on a connection of its own.

    The process-wide sqlite handle can be closed while this thread is mid-query
    (tests do exactly that), and using a closed handle segfaults the
    interpreter — so this opens and closes its own.
    """

    def run() -> None:
        conn = None
        try:
            from app import db

            conn = db.connect()
            fetch(conn, limit=limit, unread_only=unread_only,
                  query=query, category=category, _force=True)
        except Exception:  # noqa: BLE001 — a failed refresh just leaves the stale entry
            pass
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
            with _lock:
                _refreshing.discard(key)

    threading.Thread(target=run, daemon=True).start()


def fetch(
    conn: sqlite3.Connection | None = None,
    limit: int = 25,
    unread_only: bool = True,
    query: str = "",
    category: str = "",
    _force: bool = False,
) -> schemas.Inbox:
    """Recent messages, categorised and corrected. Cached — Gmail is not free.

    `query` is passed to Gmail verbatim, so its full search syntax works
    (`from:`, `has:attachment`, `newer_than:7d`, …). `category` filters after
    classification, since the category is ours, not Gmail's.
    """
    key = f"{limit}|{unread_only}|{query}|{category}"
    if not _force:
        with _lock:
            hit = _cache.get(key)
            age = time.monotonic() - hit[0] if hit else None
            stale = hit is not None and _CACHE_TTL <= age < _STALE_MAX
            if stale and key not in _refreshing:
                _refreshing.add(key)
                refresh = True
            else:
                refresh = False
        if hit is not None and age < _CACHE_TTL:
            return hit[1]
        if stale:
            if refresh:
                _spawn_refresh(key, limit, unread_only, query, category)
            return hit[1]

    st = status()
    if not st.configured:
        inbox = schemas.Inbox(
            status=st, fetched=False,
            error="Gmail is not connected — token.json is missing from the categorizer.",
            messages=[], counts={},
        )
        return inbox

    service = _service()
    checker = _load_checker()
    if service is None or checker is None:
        inbox = schemas.Inbox(
            status=st, fetched=False,
            error="Could not reach Gmail. The OAuth token may need refreshing "
                  "(run oauth_auto.py in the email-categorizer directory).",
            messages=[], counts={},
        )
        return inbox

    q = query or ("is:unread in:inbox" if unread_only else "in:inbox")
    rules = _rule_index(conn) if conn is not None else {}
    applied_rule_ids: list[int] = []
    messages: list[schemas.EmailMessage] = []
    try:
        listing = (
            service.users().messages()
            .list(userId="me", q=q, maxResults=min(limit, 50))
            .execute()
        )
        for ref in listing.get("messages", []):
            # `full`, not `metadata`. The categoriser scores the body text, and
            # on `metadata` the only body available is Gmail's ~100-char
            # snippet — so the deck was classifying different input than the
            # cron job, which reads 200 chars of decoded body with URLs
            # stripped. Same message, two verdicts, despite this module's
            # promise that the deck and Discord tell the same story. The 90s
            # cache below absorbs the extra payload.
            msg = (
                service.users().messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
            headers = msg.get("payload", {}).get("headers", [])
            from_addr = _header(headers, "From")
            subject = _header(headers, "Subject") or "(no subject)"
            preview = _preview(checker, msg)

            msg_category, agent_level, label, is_vip, vip_label = _classify(
                checker, msg, from_addr, subject, preview
            )
            level, overridden_by, rule_id = _apply_rules(
                rules, from_addr, msg_category, agent_level
            )
            if rule_id is not None:
                applied_rule_ids.append(rule_id)
                label = FALLBACK_LABELS.get(level, label)
            if category and msg_category.lower() != category.lower():
                continue
            # A single malformed message must not lose the whole inbox.
            try:
                messages.append(
                    schemas.EmailMessage(
                        id=msg.get("id", ""),
                        thread_id=msg.get("threadId", ""),
                        sender=_display_name(from_addr),
                        # Bare address, not the raw header — the UI builds
                        # correction rules straight from this field.
                        sender_address=_address(from_addr),
                        subject=subject,
                        preview=preview[:220],
                        category=msg_category,
                        importance=max(1, min(5, level)),
                        importance_label=label,
                        agent_importance=max(1, min(5, agent_level)),
                        overridden_by=overridden_by,
                        is_vip=is_vip,
                        vip_label=vip_label,
                        sender_domain=_domain(from_addr),
                        unread="UNREAD" in (msg.get("labelIds") or []),
                        received_at=_pretty_date(_header(headers, "Date")),
                        link=f"https://mail.google.com/mail/u/0/#inbox/{msg.get('id','')}",
                    )
                )
            except ValidationError:
                continue
    except Exception as exc:  # network, quota, revoked token
        inbox = schemas.Inbox(
            status=st, fetched=False,
            error=f"Gmail request failed: {type(exc).__name__}",
            messages=[], counts={},
        )
        return inbox

    # Credit the rules that actually fired, so the Rules panel can show what is
    # earning its keep.
    if conn is not None and applied_rule_ids:
        conn.executemany(
            "UPDATE email_rules SET hits = hits + 1 WHERE id = ?",
            [(rid,) for rid in applied_rule_ids],
        )
        conn.commit()

    messages.sort(key=lambda m: (-m.importance, m.sender.lower()))
    counts: dict[str, int] = {
        "total": len(messages),
        "vip": sum(1 for m in messages if m.is_vip),
        "corrected": sum(1 for m in messages if m.overridden_by),
    }
    for m in messages:
        counts[m.category] = counts.get(m.category, 0) + 1
        counts[f"importance_{m.importance}"] = counts.get(f"importance_{m.importance}", 0) + 1

    inbox = schemas.Inbox(
        status=st, fetched=True, error="", messages=messages, counts=counts,
        categories=sorted({m.category for m in messages}), query=q,
    )
    with _lock:
        _cache[key] = (time.monotonic(), inbox)
    return inbox


def clear_cache() -> None:
    with _lock:
        _cache.clear()


def mark_read(message_id: str) -> bool:
    service = _service()
    if service is None:
        return False
    try:
        service.users().messages().modify(
            userId="me", id=message_id, body={"removeLabelIds": ["UNREAD"]}
        ).execute()
    except Exception:
        return False
    clear_cache()
    return True
