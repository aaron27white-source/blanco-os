"""What the AI stack costs.

Blanco runs models across five providers plus whatever tooling sits on top. A
subscription is easy to see; four metered APIs each billing eight dollars are
not, and that is the number this module exists to make visible.

The one thing it refuses to do is guess. A service with no cost entered is
reported in `unpriced` rather than being treated as free, because a total that
silently omits three providers is worse than no total at all.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app import schemas
from app.services import store


def _monthly(cost: float, billing: str) -> float:
    """One service's cost expressed per month."""
    if billing == "annual":
        return cost / 12
    if billing == "free":
        return 0.0
    # 'monthly' and 'usage' are both already a month's worth — usage is whatever
    # was last recorded for the current period.
    return cost


def _service(row: sqlite3.Row) -> schemas.AiService:
    cost = row["cost"] or 0
    return schemas.AiService(
        id=row["id"],
        name=row["name"],
        provider=row["provider"],
        kind=row["kind"],
        billing=row["billing"],
        cost=cost,
        currency=row["currency"],
        is_active=bool(row["is_active"]),
        billing_email=row["billing_email"],
        notes=row["notes"],
        monthly_cost=round(_monthly(cost, row["billing"]), 2),
        updated_at=row["updated_at"],
    )


def list_services(
    conn: sqlite3.Connection, active_only: bool = False
) -> list[schemas.AiService]:
    sql = "SELECT * FROM ai_services"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY is_active DESC, cost DESC, name"
    return [_service(r) for r in conn.execute(sql).fetchall()]


def get_service(conn: sqlite3.Connection, service_id: int) -> schemas.AiService | None:
    row = conn.execute("SELECT * FROM ai_services WHERE id = ?", (service_id,)).fetchone()
    return _service(row) if row else None


def add_service(
    conn: sqlite3.Connection, payload: schemas.AiServiceCreate
) -> schemas.AiService | None:
    try:
        cur = conn.execute(
            """INSERT INTO ai_services (name, provider, kind, billing, cost, billing_email, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payload.name, payload.provider, payload.kind, payload.billing,
             payload.cost, payload.billing_email, payload.notes),
        )
    except sqlite3.IntegrityError:
        # name is UNIQUE — adding "OpenAI API" twice is a mistake, not a second
        # service, and silently creating a duplicate would double the total.
        return None
    conn.commit()
    store.log(conn, "money", "ai_service_add", payload.name, cost=payload.cost)
    return get_service(conn, cur.lastrowid)


def update_service(
    conn: sqlite3.Connection, service_id: int, payload: schemas.AiServiceUpdate
) -> schemas.AiService | None:
    fields = payload.model_dump(exclude_none=True)
    if not conn.execute("SELECT 1 FROM ai_services WHERE id = ?", (service_id,)).fetchone():
        return None
    if not fields:
        return get_service(conn, service_id)

    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE ai_services SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), service_id),
    )
    conn.commit()

    # Recording a cost also files it against this month, so the trend builds
    # itself instead of depending on him remembering to log history separately.
    if "cost" in fields:
        _record_period(conn, service_id, float(fields["cost"]))

    store.log(conn, "money", "ai_service_update", str(service_id), **fields)
    return get_service(conn, service_id)


def delete_service(conn: sqlite3.Connection, service_id: int) -> bool:
    cur = conn.execute("DELETE FROM ai_services WHERE id = ?", (service_id,))
    conn.commit()
    return bool(cur.rowcount)


def _record_period(conn: sqlite3.Connection, service_id: int, amount: float) -> None:
    period = date.today().strftime("%Y-%m")
    conn.execute(
        """INSERT INTO ai_spend_history (service_id, period, amount) VALUES (?, ?, ?)
           ON CONFLICT (service_id, period) DO UPDATE SET amount = excluded.amount""",
        (service_id, period, amount),
    )
    conn.commit()


def history(conn: sqlite3.Connection, months: int = 12) -> list[schemas.AiSpendPoint]:
    rows = conn.execute(
        """SELECT period, SUM(amount) AS total FROM ai_spend_history
           GROUP BY period ORDER BY period DESC LIMIT ?""",
        (months,),
    ).fetchall()
    return [
        schemas.AiSpendPoint(period=r["period"], amount=round(r["total"] or 0, 2))
        for r in reversed(rows)
    ]


def overview(conn: sqlite3.Connection) -> schemas.AiSpendOverview:
    services = list_services(conn)
    active = [s for s in services if s.is_active]

    monthly = sum(s.monthly_cost for s in active)
    fixed = sum(s.monthly_cost for s in active if s.billing in ("monthly", "annual"))
    usage = sum(s.monthly_cost for s in active if s.billing == "usage")

    by_kind: dict[str, float] = {}
    for s in active:
        by_kind[s.kind] = round(by_kind.get(s.kind, 0) + s.monthly_cost, 2)

    # A free tier is genuinely $0; a service nobody has priced is an unknown.
    # Conflating them is how a stack total quietly drifts from reality.
    unpriced = [s.name for s in active if s.cost == 0 and s.billing != "free"]

    target = conn.execute(
        "SELECT COALESCE(SUM(monthly_target), 0) AS t FROM ventures"
    ).fetchone()["t"] or 0

    return schemas.AiSpendOverview(
        monthly_total=round(monthly, 2),
        annual_total=round(monthly * 12, 2),
        fixed_monthly=round(fixed, 2),
        usage_monthly=round(usage, 2),
        services=services,
        by_kind=by_kind,
        unpriced=unpriced,
        history=history(conn),
        share_of_income=round(monthly / target, 4) if target else 0.0,
    )
