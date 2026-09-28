"""Write-through to the vault's Debt-Tracker.md.

The database is the queryable truth; `Personal/areas/finance/Debt-Tracker.md`
is the one Blanco actually opens in Obsidian. This keeps the note current after
every write instead of letting it drift into a ledger that disagrees with the
OS about what he owes.

Same two hard rules `vault_sync` set, for the same reasons:

* **Only the "What I Owe" section is touched.** The "What I'm Owed" table and
  the Log below it are hand-curated — the database has never held either, so
  rewriting the file wholesale would delete content it cannot reconstruct.
* **Never corrupt on failure.** A missing section marker or an unreadable file
  is reported and the note is left exactly as it was.

Import runs the other way, once, when the debts table is empty: the vault is
the source of truth on a cold start, and a fresh database should not silently
present an empty ledger when the note has three names in it.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date

from app.config import get_settings
from app.services import store

SECTION = "## 💸 What I Owe"
TABLE_HEADER = (
    "| Creditor | Amount | Date Added | Status | Notes |\n"
    "|----------|--------|------------|--------|-------|\n"
)
# "| Cedar Bank | $5,000.00 | 2026-08-02 | Outstanding | demo loan |"
ROW_RE = re.compile(r"^\|(?!\s*-)([^|]+)\|([^|]*)\|([^|]*)\|([^|]*)\|([^|]*)\|\s*$")
MONEY_RE = re.compile(r"[^0-9.]")


def _money(cell: str) -> float | None:
    cleaned = MONEY_RE.sub("", cell)
    try:
        return float(cleaned) if cleaned else None
    except ValueError:
        return None


def _section_bounds(md: str) -> tuple[int, int] | None:
    start = md.find(SECTION)
    if start == -1:
        return None
    # Stop at whichever comes first: the horizontal rule that closes the
    # section, or the next heading. Overrunning the rule would swallow the
    # separator and slowly reflow a file we are supposed to leave alone.
    after = start + len(SECTION)
    candidates = [i for i in (md.find("\n---", after), md.find("\n## ", after)) if i != -1]
    return start, min(candidates) if candidates else len(md)


def parse_debts(md: str) -> list[dict]:
    """Rows of the 'What I Owe' table, skipping the header and any em-dash filler."""
    bounds = _section_bounds(md)
    if not bounds:
        return []
    start, end = bounds

    found = []
    for line in md[start:end].splitlines():
        match = ROW_RE.match(line.strip())
        if not match:
            continue
        creditor, amount_cell, added, status, notes = (c.strip() for c in match.groups())
        if not creditor or creditor.lower() == "creditor" or creditor == "—":
            continue
        amount = _money(amount_cell)
        if amount is None or amount <= 0:
            continue
        found.append(
            {
                "creditor": creditor,
                "amount": amount,
                "added_at": added if re.fullmatch(r"\d{4}-\d{2}-\d{2}", added) else None,
                "status": status.lower() if status.lower() in ("outstanding", "partial", "paid") else "outstanding",
                "notes": "" if notes == "—" else notes,
            }
        )
    return found


def import_debts(conn: sqlite3.Connection, force: bool = False) -> int:
    """Seed the debts table from the vault note. Returns rows inserted.

    A no-op once anything is in the table: the database is authoritative after
    the first boot, and re-importing would resurrect debts he has since paid.
    """
    if not force:
        row = conn.execute("SELECT COUNT(*) AS n FROM debts").fetchone()
        if row["n"]:
            return 0

    path = get_settings().debt_tracker_file
    try:
        md = path.read_text(encoding="utf-8")
    except OSError:
        return 0

    inserted = 0
    for entry in parse_debts(md):
        if conn.execute(
            "SELECT 1 FROM debts WHERE creditor = ?", (entry["creditor"],)
        ).fetchone():
            continue
        conn.execute(
            """INSERT INTO debts (creditor, amount, balance, status, notes, added_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                entry["creditor"], entry["amount"], entry["amount"],
                entry["status"], entry["notes"], entry["added_at"] or date.today().isoformat(),
            ),
        )
        inserted += 1

    if inserted:
        conn.commit()
        store.log(conn, "finance", "vault_import", path.name, debts=inserted)
    return inserted


def _row(debt) -> str:
    status = {"outstanding": "Outstanding", "partial": "Partial", "paid": "✅ Paid"}[debt.status]
    cells = [
        debt.creditor,
        f"${debt.balance:,.2f}" if debt.status != "paid" else f"~~${debt.amount:,.2f}~~",
        debt.added_at,
        status,
        debt.notes or "",
    ]
    # A stray pipe in a creditor name or note would break the table.
    return "| " + " | ".join(str(c).replace("|", "\\|") for c in cells) + " |"


def export_debts(conn: sqlite3.Connection) -> tuple[bool, str, int]:
    """Rebuild the 'What I Owe' table from the database. (ok, detail, rows)."""
    from app.services import finance_service

    path = get_settings().debt_tracker_file
    try:
        md = path.read_text(encoding="utf-8")
    except OSError:
        return False, f"{path.name} not found", 0

    bounds = _section_bounds(md)
    if not bounds:
        return False, f"'{SECTION}' section not found — left untouched", 0
    start, end = bounds

    debts = finance_service.list_debts(conn)
    outstanding = sum(d.balance for d in debts)
    rows = [_row(d) for d in debts]
    body = "\n".join(rows) if rows else "| — | — | — | — | — |"

    replacement = (
        f"{SECTION}\n\n{TABLE_HEADER}{body}\n\n"
        f"**Total owed: ${outstanding:,.2f}**\n"
    )
    updated = _stamp(md[:start] + replacement + md[end:])

    if updated == md:
        return True, "already up to date", len(rows)

    try:
        path.write_text(updated, encoding="utf-8")
    except OSError as exc:
        return False, f"could not write {path.name}: {exc.strerror}", 0

    store.log(conn, "finance", "vault_export", path.name, debts=len(rows), total=round(outstanding, 2))
    return True, f"Debt-Tracker.md rebuilt ({len(rows)} debts, ${outstanding:,.2f} owed)", len(rows)


def _stamp(md: str) -> str:
    """Keep the '**Updated:**' date in the header block honest."""
    return re.sub(
        r"\*\*Updated:\*\*\s*\d{4}-\d{2}-\d{2}",
        f"**Updated:** {date.today().isoformat()}",
        md,
        count=1,
    )
