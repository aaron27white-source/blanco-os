"""Credit scores, tradelines, disputes, and the staged build plan.

Blanco's file is thin rather than damaged: as of the July 2026 pulls there are
no open tradelines, one hard inquiry, and exactly one derogatory item. That
shapes what this module optimises for. It is not a debt-management tool — it
tracks the two clocks that actually matter on a thin file. The first is the
FCRA's 30-day dispute window, because a bureau that misses it hands you grounds
for deletion. The second is the build plan's calendar, because a file with no
history cannot be hurried, only started earlier.
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from app import schemas
from app.services import store

# The FCRA gives a bureau 30 days to investigate. Blowing the deadline is itself
# a basis for deletion, so the due date is worth computing and alerting on.
FCRA_RESPONSE_DAYS = 30


# --- scores -----------------------------------------------------------------


def _score(row: sqlite3.Row) -> schemas.CreditScore:
    return schemas.CreditScore(
        id=row["id"],
        bureau=row["bureau"],
        score_type=row["score_type"],
        score=row["score"],
        source=row["source"],
        recorded_on=row["recorded_on"],
    )


def list_scores(
    conn: sqlite3.Connection, bureau: str | None = None, limit: int = 100
) -> list[schemas.CreditScore]:
    sql = "SELECT * FROM credit_scores"
    params: list = []
    if bureau:
        sql += " WHERE bureau = ?"
        params.append(bureau)
    sql += " ORDER BY recorded_on DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_score(r) for r in conn.execute(sql, params).fetchall()]


def latest_scores(conn: sqlite3.Connection) -> list[schemas.CreditScore]:
    """Most recent reading per (bureau, score_type).

    Ties on recorded_on break by id, so entering two readings for the same day
    surfaces the one written last rather than an arbitrary row.
    """
    rows = conn.execute(
        """SELECT * FROM credit_scores
           WHERE id IN (
               SELECT id FROM credit_scores cs
               WHERE cs.id = (
                   SELECT id FROM credit_scores
                   WHERE bureau = cs.bureau AND score_type = cs.score_type
                   ORDER BY recorded_on DESC, id DESC LIMIT 1
               )
           )
           ORDER BY bureau, score_type"""
    ).fetchall()
    return [_score(r) for r in rows]


def add_score(conn: sqlite3.Connection, payload: schemas.CreditScoreCreate) -> schemas.CreditScore:
    recorded = payload.recorded_on or date.today().isoformat()
    cur = conn.execute(
        """INSERT INTO credit_scores (bureau, score_type, score, source, recorded_on)
           VALUES (?, ?, ?, ?, ?)""",
        (payload.bureau, payload.score_type, payload.score, payload.source, recorded),
    )
    conn.commit()
    store.log(
        conn, "credit", "score", f"{payload.bureau} {payload.score_type}", score=payload.score
    )
    return _score(conn.execute("SELECT * FROM credit_scores WHERE id = ?", (cur.lastrowid,)).fetchone())


# --- accounts ---------------------------------------------------------------


def _account(row: sqlite3.Row) -> schemas.CreditAccount:
    limit = row["credit_limit"] or 0
    balance = row["balance"] or 0
    return schemas.CreditAccount(
        id=row["id"],
        account_name=row["account_name"],
        account_type=row["account_type"],
        bucket=row["bucket"],
        track=row["track"],
        bureau=row["bureau"],
        credit_limit=limit,
        balance=balance,
        apr=row["apr"] or 0,
        monthly_cost=row["monthly_cost"] or 0,
        deposit=row["deposit"] or 0,
        opened_on=row["opened_on"],
        closed_on=row["closed_on"],
        payment_status=row["payment_status"],
        is_active=bool(row["is_active"]),
        notes=row["notes"],
        # Installment loans and rent tradelines report no limit. Dividing by it
        # would be meaningless, and reporting 100% would drag the aggregate
        # utilisation into a number that is simply wrong.
        utilization=round(balance / limit, 4) if limit else 0.0,
        updated_at=row["updated_at"],
    )


def list_accounts(
    conn: sqlite3.Connection, active_only: bool = False
) -> list[schemas.CreditAccount]:
    sql = "SELECT * FROM credit_accounts"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY is_active DESC, opened_on IS NULL, opened_on, id"
    return [_account(r) for r in conn.execute(sql).fetchall()]


def get_account(conn: sqlite3.Connection, account_id: int) -> schemas.CreditAccount | None:
    row = conn.execute("SELECT * FROM credit_accounts WHERE id = ?", (account_id,)).fetchone()
    return _account(row) if row else None


def add_account(
    conn: sqlite3.Connection, payload: schemas.CreditAccountCreate
) -> schemas.CreditAccount:
    cur = conn.execute(
        """INSERT INTO credit_accounts
             (account_name, account_type, bucket, track, bureau, credit_limit, balance,
              apr, monthly_cost, deposit, opened_on, payment_status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            payload.account_name, payload.account_type, payload.bucket, payload.track,
            payload.bureau, payload.credit_limit, payload.balance, payload.apr,
            payload.monthly_cost, payload.deposit, payload.opened_on,
            payload.payment_status, payload.notes,
        ),
    )
    conn.commit()
    store.log(conn, "credit", "open_account", payload.account_name, bucket=payload.bucket)
    return get_account(conn, cur.lastrowid)


def update_account(
    conn: sqlite3.Connection, account_id: int, payload: schemas.CreditAccountUpdate
) -> schemas.CreditAccount | None:
    fields = payload.model_dump(exclude_none=True)
    if not conn.execute("SELECT 1 FROM credit_accounts WHERE id = ?", (account_id,)).fetchone():
        return None
    if not fields:
        return get_account(conn, account_id)
    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE credit_accounts SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), account_id),
    )
    conn.commit()
    store.log(conn, "credit", "update_account", str(account_id), **fields)
    return get_account(conn, account_id)


# --- disputes ---------------------------------------------------------------


def _dispute(row: sqlite3.Row, today: str | None = None) -> schemas.CreditDispute:
    due = row["response_due"]
    remaining = None
    # Only meaningful while the clock is running: an unsent dispute has no
    # deadline, and a resolved one no longer has anything to wait for.
    if due and row["outcome"] == "pending":
        today = today or date.today().isoformat()
        remaining = (date.fromisoformat(due) - date.fromisoformat(today)).days
    return schemas.CreditDispute(
        id=row["id"],
        bureau=row["bureau"],
        category=row["category"],
        item=row["item"],
        reason=row["reason"],
        sent_on=row["sent_on"],
        response_due=due,
        outcome=row["outcome"],
        resolved_on=row["resolved_on"],
        notes=row["notes"],
        days_remaining=remaining,
    )


def list_disputes(
    conn: sqlite3.Connection, outcome: str | None = None, bureau: str | None = None
) -> list[schemas.CreditDispute]:
    sql = "SELECT * FROM credit_disputes"
    where, params = [], []
    if outcome:
        where.append("outcome = ?")
        params.append(outcome)
    if bureau:
        where.append("bureau = ?")
        params.append(bureau)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Unsent disputes sort last: they have no clock, so they are not the thing
    # to look at when something is overdue.
    sql += " ORDER BY response_due IS NULL, response_due, bureau, id"
    today = date.today().isoformat()
    return [_dispute(r, today) for r in conn.execute(sql, params).fetchall()]


def add_dispute(
    conn: sqlite3.Connection, payload: schemas.CreditDisputeCreate
) -> schemas.CreditDispute:
    due = _due_date(payload.sent_on)
    cur = conn.execute(
        """INSERT INTO credit_disputes (bureau, category, item, reason, sent_on, response_due, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            payload.bureau, payload.category, payload.item, payload.reason,
            payload.sent_on, due, payload.notes,
        ),
    )
    conn.commit()
    store.log(conn, "credit", "dispute", f"{payload.bureau}: {payload.item}")
    row = conn.execute("SELECT * FROM credit_disputes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _dispute(row)


def update_dispute(
    conn: sqlite3.Connection, dispute_id: int, payload: schemas.CreditDisputeUpdate
) -> schemas.CreditDispute | None:
    row = conn.execute("SELECT * FROM credit_disputes WHERE id = ?", (dispute_id,)).fetchone()
    if not row:
        return None

    fields = payload.model_dump(exclude_none=True)
    # Recording the send date is what starts the FCRA clock, so the due date is
    # derived here rather than being a field the caller can set inconsistently.
    if "sent_on" in fields:
        fields["response_due"] = _due_date(fields["sent_on"])
    # Any outcome other than pending closes it out; stamp the resolution date so
    # the history shows when, not just what.
    if fields.get("outcome") and fields["outcome"] != "pending":
        fields["resolved_on"] = date.today().isoformat()

    if fields:
        # response_due and resolved_on are derived above, not sent by the client.
        assignments = store.set_clause(fields, {*type(payload).model_fields, "response_due", "resolved_on"})
        conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
            f"UPDATE credit_disputes SET {assignments} WHERE id = ?",
            (*fields.values(), dispute_id),
        )
        conn.commit()
        store.log(conn, "credit", "update_dispute", row["item"], **fields)

    updated = conn.execute("SELECT * FROM credit_disputes WHERE id = ?", (dispute_id,)).fetchone()
    return _dispute(updated)


def _due_date(sent_on: str | None) -> str | None:
    if not sent_on:
        return None
    return (date.fromisoformat(sent_on) + timedelta(days=FCRA_RESPONSE_DAYS)).isoformat()


def overdue_disputes(conn: sqlite3.Connection) -> list[schemas.CreditDispute]:
    """Sent, still pending, and past the 30-day window."""
    today = date.today().isoformat()
    rows = conn.execute(
        """SELECT * FROM credit_disputes
           WHERE outcome = 'pending' AND response_due IS NOT NULL AND response_due < ?
           ORDER BY response_due""",
        (today,),
    ).fetchall()
    return [_dispute(r, today) for r in rows]


# --- plan -------------------------------------------------------------------


def _step(row: sqlite3.Row) -> schemas.CreditPlanStep:
    return schemas.CreditPlanStep(
        id=row["id"],
        phase=row["phase"],
        sort_order=row["sort_order"],
        track=row["track"],
        title=row["title"],
        detail=row["detail"],
        est_cost=row["est_cost"] or 0,
        done=bool(row["done"]),
        done_on=row["done_on"],
    )


def list_plan(conn: sqlite3.Connection, phase: str | None = None) -> list[schemas.CreditPlanStep]:
    sql = "SELECT * FROM credit_plan_steps"
    params: list = []
    if phase:
        sql += " WHERE phase = ?"
        params.append(phase)
    sql += " ORDER BY sort_order"
    return [_step(r) for r in conn.execute(sql, params).fetchall()]


def next_steps(conn: sqlite3.Connection) -> list[schemas.CreditPlanStep]:
    """Everything still open in the earliest unfinished phase.

    A phase rather than a single step, because the plan's phases are meant to
    run in parallel — Week 1 files disputes, opens two cards and starts the
    business foundation at once. Handing back one item at a time would
    serialise work the plan explicitly wants concurrent.
    """
    row = conn.execute(
        "SELECT phase FROM credit_plan_steps WHERE done = 0 ORDER BY sort_order LIMIT 1"
    ).fetchone()
    if not row:
        return []
    rows = conn.execute(
        "SELECT * FROM credit_plan_steps WHERE phase = ? AND done = 0 ORDER BY sort_order",
        (row["phase"],),
    ).fetchall()
    return [_step(r) for r in rows]


def complete_step(conn: sqlite3.Connection, step_id: int) -> schemas.CreditPlanStep | None:
    if not conn.execute("SELECT 1 FROM credit_plan_steps WHERE id = ?", (step_id,)).fetchone():
        return None
    conn.execute(
        "UPDATE credit_plan_steps SET done = 1, done_on = ? WHERE id = ?",
        (date.today().isoformat(), step_id),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM credit_plan_steps WHERE id = ?", (step_id,)).fetchone()
    store.log(conn, "credit", "plan_step_done", row["title"])
    return _step(row)


def reopen_step(conn: sqlite3.Connection, step_id: int) -> schemas.CreditPlanStep | None:
    if not conn.execute("SELECT 1 FROM credit_plan_steps WHERE id = ?", (step_id,)).fetchone():
        return None
    conn.execute(
        "UPDATE credit_plan_steps SET done = 0, done_on = NULL WHERE id = ?", (step_id,)
    )
    conn.commit()
    row = conn.execute("SELECT * FROM credit_plan_steps WHERE id = ?", (step_id,)).fetchone()
    return _step(row)


# --- overview ---------------------------------------------------------------


def overview(conn: sqlite3.Connection) -> schemas.CreditOverview:
    accounts = list_accounts(conn)
    active = [a for a in accounts if a.is_active]

    # Only revolving lines carry a limit, so only they belong in aggregate
    # utilisation. Installment loans and rent tradelines report none, and
    # folding them in would understate the number that actually gets scored.
    revolving = [a for a in active if a.credit_limit > 0]
    total_limit = sum(a.credit_limit for a in revolving)
    total_balance = sum(a.balance for a in revolving)

    steps = list_plan(conn)
    done = sum(1 for s in steps if s.done)

    return schemas.CreditOverview(
        latest_scores=latest_scores(conn),
        accounts=accounts,
        open_disputes=list_disputes(conn, outcome="pending"),
        overdue_disputes=overdue_disputes(conn),
        next_steps=next_steps(conn),
        total_limit=round(total_limit, 2),
        total_balance=round(total_balance, 2),
        utilization=round(total_balance / total_limit, 4) if total_limit else 0.0,
        monthly_cost=round(sum(a.monthly_cost for a in active), 2),
        deposits_held=round(sum(a.deposit for a in active), 2),
        bucket_b_count=sum(1 for a in active if a.bucket == "B"),
        plan_progress=round(done / len(steps), 4) if steps else 0.0,
    )
