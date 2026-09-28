"""Ventures and money.

The north star is income diversification, so ventures are first-class OS
objects rather than notes: each one carries a stage, a monthly target, and the
single next action that would move it.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app import schemas
from app.services import store


def _venture(row: sqlite3.Row) -> schemas.Venture:
    target = row["monthly_target"] or 0
    actual = row["monthly_actual"] or 0
    return schemas.Venture(
        id=row["id"],
        name=row["name"],
        emoji=row["emoji"],
        thesis=row["thesis"],
        stage=row["stage"],
        health=row["health"],
        monthly_target=target,
        monthly_actual=actual,
        capital_in=row["capital_in"] or 0,
        next_action=row["next_action"],
        vault_path=row["vault_path"],
        repo_path=row["repo_path"],
        attainment=round(actual / target, 4) if target else 0.0,
        division_id=row["division_id"],
        updated_at=row["updated_at"],
    )


def list_ventures(
    conn: sqlite3.Connection,
    stage: str | None = None,
    division_id: str | None = None,
) -> list[schemas.Venture]:
    sql = "SELECT * FROM ventures"
    where: list[str] = []
    params: list = []
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if division_id:
        where.append("division_id = ?")
        params.append(division_id)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY sort_order, name"
    return [_venture(r) for r in conn.execute(sql, params).fetchall()]


def get_venture(conn: sqlite3.Connection, venture_id: str) -> schemas.Venture | None:
    row = conn.execute("SELECT * FROM ventures WHERE id = ?", (venture_id,)).fetchone()
    return _venture(row) if row else None


def update_venture(
    conn: sqlite3.Connection, venture_id: str, payload: schemas.VentureUpdate
) -> schemas.Venture | None:
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_venture(conn, venture_id)
    if not conn.execute("SELECT 1 FROM ventures WHERE id = ?", (venture_id,)).fetchone():
        return None

    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE ventures SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), venture_id),
    )
    conn.commit()
    store.log(conn, "money", "update_venture", venture_id, **fields)
    return get_venture(conn, venture_id)


def _event(row: sqlite3.Row) -> schemas.VentureEvent:
    return schemas.VentureEvent(
        id=row["id"],
        venture_id=row["venture_id"],
        kind=row["kind"],
        amount=row["amount"],
        label=row["label"],
        occurred_on=row["occurred_on"],
    )


def list_events(
    conn: sqlite3.Connection, venture_id: str | None = None, limit: int = 50
) -> list[schemas.VentureEvent]:
    sql = "SELECT * FROM venture_events"
    params: list = []
    if venture_id:
        sql += " WHERE venture_id = ?"
        params.append(venture_id)
    sql += " ORDER BY occurred_on DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_event(r) for r in conn.execute(sql, params).fetchall()]


def add_event(
    conn: sqlite3.Connection, venture_id: str, payload: schemas.VentureEventCreate
) -> schemas.VentureEvent | None:
    if not conn.execute("SELECT 1 FROM ventures WHERE id = ?", (venture_id,)).fetchone():
        return None
    occurred = payload.occurred_on or date.today().isoformat()
    cur = conn.execute(
        """INSERT INTO venture_events (venture_id, kind, amount, label, occurred_on)
           VALUES (?, ?, ?, ?, ?)""",
        (venture_id, payload.kind, payload.amount, payload.label, occurred),
    )
    conn.commit()
    _recalc_actual(conn, venture_id)
    store.log(conn, "money", payload.kind, f"{venture_id}: {payload.label}", amount=payload.amount)
    row = conn.execute("SELECT * FROM venture_events WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _event(row)


def delete_event(conn: sqlite3.Connection, venture_id: str, event_id: int) -> bool:
    cur = conn.execute(
        "DELETE FROM venture_events WHERE id = ? AND venture_id = ?", (event_id, venture_id)
    )
    conn.commit()
    if not cur.rowcount:
        return False
    _recalc_actual(conn, venture_id)
    store.log(conn, "money", "delete_event", f"{venture_id}#{event_id}")
    return True


def _recalc_actual(conn: sqlite3.Connection, venture_id: str) -> None:
    """Keep monthly_actual equal to this calendar month's net revenue."""
    month = date.today().strftime("%Y-%m")
    row = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN kind = 'revenue' THEN amount ELSE 0 END), 0) AS rev,
             COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount ELSE 0 END), 0) AS exp
           FROM venture_events
           WHERE venture_id = ? AND occurred_on LIKE ?""",
        (venture_id, f"{month}%"),
    ).fetchone()
    conn.execute(
        "UPDATE ventures SET monthly_actual = ?, updated_at = datetime('now') WHERE id = ?",
        (round((row["rev"] or 0) - (row["exp"] or 0), 2), venture_id),
    )
    conn.commit()


def overview(conn: sqlite3.Connection) -> schemas.MoneyOverview:
    month = date.today().strftime("%Y-%m")
    ventures = list_ventures(conn)
    totals = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN kind = 'revenue' THEN amount ELSE 0 END), 0) AS rev,
             COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount ELSE 0 END), 0) AS exp
           FROM venture_events WHERE occurred_on LIKE ?""",
        (f"{month}%",),
    ).fetchone()

    target = sum(v.monthly_target for v in ventures)
    actual = sum(v.monthly_actual for v in ventures)
    revenue = round(totals["rev"] or 0, 2)
    expenses = round(totals["exp"] or 0, 2)

    return schemas.MoneyOverview(
        month=month,
        total_target=round(target, 2),
        total_actual=round(actual, 2),
        attainment=round(actual / target, 4) if target else 0.0,
        revenue_mtd=revenue,
        expenses_mtd=expenses,
        net_mtd=round(revenue - expenses, 2),
        capital_deployed=round(sum(v.capital_in for v in ventures), 2),
        by_venture=ventures,
        recent_events=list_events(conn, limit=15),
    )
