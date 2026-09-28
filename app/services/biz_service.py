"""Your Business — five divisions, their ventures, and their deal pipeline.

Your Business is the work OS: consulting and automation builds, the electronics
operation, and Blanco's own storefronts. Each is a division with its own
pipeline and its own number.

The one rule that shapes this module: **a division's revenue is derived, never
stored.** Ventures already carry `monthly_actual`, which `money_service`
recalculates from `venture_events` on every write. A division sums its
ventures. That keeps one ledger and one write path — there is no way for a
division total and the money tab to disagree, because they are the same numbers
added up twice.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app import schemas
from app.services import bridge_service, money_service, store

#: Deals in these stages are still live, and their value is "pipeline".
OPEN_DEAL_STAGES: tuple[str, ...] = ("lead", "qualified", "proposal")

#: Columns each PATCH is allowed to write. The keys already come from a
#: Pydantic model, so they are a closed set — but that is an argument about
#: code in another file. Checking here means the query cannot be reshaped by a
#: field someone adds to a schema later, which is what Semgrep is warning about
#: when it sees a SET clause built with an f-string.
DIVISION_COLUMNS = frozenset(
    {"name", "tagline", "stage", "health", "monthly_target", "next_action", "vault_path"}
)
DEAL_COLUMNS = frozenset(
    {"client", "title", "stage", "value", "source", "next_action", "notes", "closed_on"}
)

ANNOTATION_COLUMNS = frozenset(
    {"division_id", "company", "domain", "kind", "account_email", "project",
     "status", "next_action", "signed_up_on", "last_seen", "source", "notes"}
)

#: Fields the mail sweep may fill but never overwrite once they hold a value.
#: Everything here is Blanco's judgement — what the project is, whether it is
#: finished, what happens next. `last_seen` is deliberately absent: it is a
#: fact about the mailbox, so the newest sweep always wins.
#:
#: This is the weaker of the two protections and only covers fields that were
#: never touched; a field Blanco has actually edited is pinned (below) and is
#: protected regardless of what it now holds.
SWEEP_PRESERVED: tuple[str, ...] = ("project", "status", "next_action", "notes", "division_id")


def _pinned(row) -> set[str]:
    """Column names Blanco has edited by hand on this row."""
    raw = row["pinned_fields"] if "pinned_fields" in row.keys() else ""
    return {f for f in (raw or "").split(",") if f}


def _set_clause(fields: dict, allowed: frozenset[str]) -> str:
    """Build a SET clause from a checked column whitelist. Values stay bound."""
    if unknown := set(fields) - allowed:
        raise ValueError(f"not updatable: {', '.join(sorted(unknown))}")
    return ", ".join(f"{column} = ?" for column in fields)


# --------------------------------------------------------------------------
# divisions
# --------------------------------------------------------------------------
def _division(conn: sqlite3.Connection, row: sqlite3.Row) -> schemas.Division:
    ventures = money_service.list_ventures(conn, division_id=row["id"])

    # Rolled up rather than read off the division: see the module docstring.
    actual = round(sum(v.monthly_actual for v in ventures), 2)
    target = row["monthly_target"] or 0

    # The only thing interpolated is a run of '?' placeholders, one per open
    # stage; the stage values themselves are bound.
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    pipe = conn.execute(
        f"""SELECT COALESCE(SUM(value), 0) AS value, COUNT(*) AS n
              FROM biz_deals
             WHERE division_id = ?
               AND stage IN ({','.join('?' * len(OPEN_DEAL_STAGES))})""",
        (row["id"], *OPEN_DEAL_STAGES),
    ).fetchone()

    return schemas.Division(
        id=row["id"],
        name=row["name"],
        emoji=row["emoji"],
        tagline=row["tagline"],
        stage=row["stage"],
        health=row["health"],
        monthly_target=target,
        monthly_actual=actual,
        attainment=round(actual / target, 4) if target else 0.0,
        pipeline_value=round(pipe["value"] or 0, 2),
        open_deals=pipe["n"] or 0,
        next_action=row["next_action"],
        vault_path=row["vault_path"],
        ventures=ventures,
        updated_at=row["updated_at"],
    )


def list_divisions(conn: sqlite3.Connection) -> list[schemas.Division]:
    rows = conn.execute("SELECT * FROM biz_divisions ORDER BY sort_order, name").fetchall()
    return [_division(conn, r) for r in rows]


def get_division(conn: sqlite3.Connection, division_id: str) -> schemas.Division | None:
    row = conn.execute(
        "SELECT * FROM biz_divisions WHERE id = ?", (division_id,)
    ).fetchone()
    return _division(conn, row) if row else None


def update_division(
    conn: sqlite3.Connection, division_id: str, payload: schemas.DivisionUpdate
) -> schemas.Division | None:
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_division(conn, division_id)
    if not conn.execute(
        "SELECT 1 FROM biz_divisions WHERE id = ?", (division_id,)
    ).fetchone():
        return None

    assignments = _set_clause(fields, DIVISION_COLUMNS)
    # assignments is column names checked against DIVISION_COLUMNS above; every
    # value is bound, so nothing here is attacker-shaped.
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    conn.execute(
        f"UPDATE biz_divisions SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), division_id),
    )
    conn.commit()
    store.log(conn, "biz", "update_division", division_id, **fields)
    return get_division(conn, division_id)


# --------------------------------------------------------------------------
# moving ventures between divisions
# --------------------------------------------------------------------------
def attach_venture(conn: sqlite3.Connection, division_id: str, venture_id: str) -> bool:
    """Put a venture under a division. Moving it from another is the same call."""
    if not conn.execute(
        "SELECT 1 FROM biz_divisions WHERE id = ?", (division_id,)
    ).fetchone():
        return False
    cur = conn.execute(
        "UPDATE ventures SET division_id = ?, updated_at = datetime('now') WHERE id = ?",
        (division_id, venture_id),
    )
    conn.commit()
    if not cur.rowcount:
        return False
    store.log(conn, "biz", "attach_venture", f"{venture_id} -> {division_id}")
    return True


def detach_venture(conn: sqlite3.Connection, division_id: str, venture_id: str) -> bool:
    """Unparent a venture. Its ledger is untouched — only the link goes."""
    cur = conn.execute(
        """UPDATE ventures SET division_id = NULL, updated_at = datetime('now')
            WHERE id = ? AND division_id = ?""",
        (venture_id, division_id),
    )
    conn.commit()
    if not cur.rowcount:
        return False
    store.log(conn, "biz", "detach_venture", f"{venture_id} from {division_id}")
    return True


# --------------------------------------------------------------------------
# deals
# --------------------------------------------------------------------------
def _deal(row: sqlite3.Row) -> schemas.Deal:
    return schemas.Deal(
        id=row["id"],
        division_id=row["division_id"],
        client=row["client"],
        title=row["title"],
        stage=row["stage"],
        value=row["value"],
        source=row["source"],
        next_action=row["next_action"],
        opened_on=row["opened_on"],
        closed_on=row["closed_on"],
        notes=row["notes"],
        updated_at=row["updated_at"],
    )


def list_deals(
    conn: sqlite3.Connection,
    division_id: str | None = None,
    stage: str | None = None,
    open_only: bool = False,
    limit: int = 100,
) -> list[schemas.Deal]:
    sql = "SELECT * FROM biz_deals"
    where: list[str] = []
    params: list = []
    if division_id:
        where.append("division_id = ?")
        params.append(division_id)
    if stage:
        where.append("stage = ?")
        params.append(stage)
    if open_only:
        where.append(f"stage IN ({','.join('?' * len(OPEN_DEAL_STAGES))})")
        params.extend(OPEN_DEAL_STAGES)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Open deals first, then newest — the pipeline is what you came to look at.
    sql += " ORDER BY closed_on IS NOT NULL, opened_on DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_deal(r) for r in conn.execute(sql, params).fetchall()]


def get_deal(conn: sqlite3.Connection, deal_id: int) -> schemas.Deal | None:
    row = conn.execute("SELECT * FROM biz_deals WHERE id = ?", (deal_id,)).fetchone()
    return _deal(row) if row else None


def add_deal(conn: sqlite3.Connection, payload: schemas.DealCreate) -> schemas.Deal | None:
    if not conn.execute(
        "SELECT 1 FROM biz_divisions WHERE id = ?", (payload.division_id,)
    ).fetchone():
        return None

    opened = payload.opened_on or date.today().isoformat()
    closed = opened if payload.stage in schemas.CLOSED_DEAL_STAGES else None
    cur = conn.execute(
        """INSERT INTO biz_deals
             (division_id, client, title, stage, value, source, next_action,
              opened_on, closed_on, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload.division_id, payload.client, payload.title, payload.stage,
         payload.value, payload.source, payload.next_action, opened, closed, payload.notes),
    )
    conn.commit()
    store.log(
        conn, "biz", "add_deal",
        f"{payload.division_id}: {payload.client}", amount=payload.value,
    )
    return get_deal(conn, cur.lastrowid)


def update_deal(
    conn: sqlite3.Connection, deal_id: int, payload: schemas.DealUpdate
) -> schemas.Deal | None:
    fields = payload.model_dump(exclude_none=True)
    current = get_deal(conn, deal_id)
    if not current:
        return None
    if not fields:
        return current

    # closed_on tracks the stage rather than being set by hand, so it can never
    # describe a close that has since been undone: moving a deal back out of
    # won/lost clears it.
    if "stage" in fields:
        moving_to_closed = fields["stage"] in schemas.CLOSED_DEAL_STAGES
        if moving_to_closed and not current.closed_on:
            fields["closed_on"] = date.today().isoformat()
        elif not moving_to_closed and current.closed_on:
            fields["closed_on"] = None

    assignments = _set_clause(fields, DEAL_COLUMNS)
    # assignments is column names checked against DEAL_COLUMNS above; every
    # value is bound, so nothing here is attacker-shaped.
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    conn.execute(
        f"UPDATE biz_deals SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), deal_id),
    )
    conn.commit()
    store.log(conn, "biz", "update_deal", f"#{deal_id}", **fields)
    return get_deal(conn, deal_id)


def delete_deal(conn: sqlite3.Connection, deal_id: int) -> bool:
    cur = conn.execute("DELETE FROM biz_deals WHERE id = ?", (deal_id,))
    conn.commit()
    if not cur.rowcount:
        return False
    store.log(conn, "biz", "delete_deal", f"#{deal_id}")
    return True


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------
def overview(conn: sqlite3.Connection) -> schemas.BizOverview:
    month = date.today().strftime("%Y-%m")
    divisions = list_divisions(conn)

    won = conn.execute(
        """SELECT COALESCE(SUM(value), 0) AS value
             FROM biz_deals
            WHERE stage = 'won' AND closed_on LIKE ?""",
        (f"{month}%",),
    ).fetchone()

    target = sum(d.monthly_target for d in divisions)
    actual = sum(d.monthly_actual for d in divisions)

    return schemas.BizOverview(
        month=month,
        total_target=round(target, 2),
        total_actual=round(actual, 2),
        attainment=round(actual / target, 4) if target else 0.0,
        pipeline_value=round(sum(d.pipeline_value for d in divisions), 2),
        open_deals=sum(d.open_deals for d in divisions),
        won_this_month=round(won["value"] or 0, 2),
        divisions=divisions,
        recent_deals=list_deals(conn, limit=10),
        unread_notices=bridge_service.unread_count(conn),
    )


# --------------------------------------------------------------------------
# annotations — the register of who Blanco is signed up with
# --------------------------------------------------------------------------
def _annotation(row: sqlite3.Row) -> schemas.Annotation:
    return schemas.Annotation(
        id=row["id"],
        division_id=row["division_id"],
        company=row["company"],
        domain=row["domain"],
        kind=row["kind"],
        account_email=row["account_email"],
        project=row["project"],
        status=row["status"],
        next_action=row["next_action"],
        signed_up_on=row["signed_up_on"],
        last_seen=row["last_seen"],
        source=row["source"],
        notes=row["notes"],
        updated_at=row["updated_at"],
    )


def list_annotations(
    conn: sqlite3.Connection,
    division_id: str | None = None,
    status: str | None = None,
    kind: str | None = None,
    open_only: bool = False,
    search: str | None = None,
    limit: int = 500,
) -> list[schemas.Annotation]:
    """The register, newest activity first.

    Ordered by last_seen rather than created_at: the useful question is "who
    have I heard from lately", and a row Hermes imported months after the
    signup would otherwise sort as though it were new.
    """
    sql = "SELECT * FROM biz_annotations"
    where: list[str] = []
    params: list = []
    if division_id:
        where.append("division_id = ?")
        params.append(division_id)
    if status:
        where.append("status = ?")
        params.append(status)
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if open_only:
        marks = ", ".join("?" * len(schemas.OPEN_ANNOTATION_STATUSES))
        # `marks` is a run of bound-parameter placeholders, not data.
        # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
        where.append(f"status IN ({marks})")
        params.extend(schemas.OPEN_ANNOTATION_STATUSES)
    if search:
        where.append("(company LIKE ? OR domain LIKE ? OR project LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    if where:
        sql += " WHERE " + " AND ".join(where)
    # NULLs last so a row with no mail date does not outrank a live one.
    sql += " ORDER BY last_seen IS NULL, last_seen DESC, company COLLATE NOCASE LIMIT ?"
    params.append(limit)
    # Every fragment above is a literal; all values are bound.
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    return [_annotation(r) for r in conn.execute(sql, params).fetchall()]


def get_annotation(conn: sqlite3.Connection, annotation_id: int) -> schemas.Annotation | None:
    row = conn.execute(
        "SELECT * FROM biz_annotations WHERE id = ?", (annotation_id,)
    ).fetchone()
    return _annotation(row) if row else None


def _find_annotation(
    conn: sqlite3.Connection, company: str, account_email: str
) -> schemas.Annotation | None:
    """Look a row up by its natural key — what the unique index enforces."""
    row = conn.execute(
        "SELECT * FROM biz_annotations WHERE company = ? AND account_email = ?",
        (company, account_email),
    ).fetchone()
    return _annotation(row) if row else None


def add_annotation(
    conn: sqlite3.Connection, payload: schemas.AnnotationCreate
) -> schemas.Annotation | None:
    """Create one row. Returns None when the division id does not exist.

    A duplicate (company, account_email) is not an error: the caller gets the
    existing row back. Hermes dumps the whole mailbox, so collisions are the
    normal case, not the exceptional one.
    """
    if payload.division_id and not conn.execute(
        "SELECT 1 FROM biz_divisions WHERE id = ?", (payload.division_id,)
    ).fetchone():
        return None

    if existing := _find_annotation(conn, payload.company, payload.account_email):
        return existing

    cur = conn.execute(
        """INSERT INTO biz_annotations
             (division_id, company, domain, kind, account_email, project,
              status, next_action, signed_up_on, last_seen, source, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (payload.division_id, payload.company, payload.domain, payload.kind,
         payload.account_email, payload.project, payload.status, payload.next_action,
         payload.signed_up_on, payload.last_seen, payload.source, payload.notes),
    )
    conn.commit()
    store.log(conn, "biz", "add_annotation", payload.company)
    return get_annotation(conn, cur.lastrowid)


def update_annotation(
    conn: sqlite3.Connection, annotation_id: int, payload: schemas.AnnotationUpdate,
    pin: bool = True,
) -> schemas.Annotation | None:
    """Change one row. `pin=False` marks the change as a sweep's, not Blanco's."""
    fields = payload.model_dump(exclude_none=True)
    current = get_annotation(conn, annotation_id)
    if not current:
        return None
    if not fields:
        return current

    if fields.get("division_id") and not conn.execute(
        "SELECT 1 FROM biz_divisions WHERE id = ?", (fields["division_id"],)
    ).fetchone():
        return None

    assignments = _set_clause(fields, ANNOTATION_COLUMNS)
    # assignments is column names checked against ANNOTATION_COLUMNS above;
    # every value is bound, so nothing here is attacker-shaped.
    # nosemgrep: python.sqlalchemy.security.sqlalchemy-execute-raw-query.sqlalchemy-execute-raw-query
    conn.execute(
        f"UPDATE biz_annotations SET {assignments}, updated_at = datetime('now') "
        "WHERE id = ?",
        (*fields.values(), annotation_id),
    )

    # Every field a human PATCH writes becomes untouchable by later sweeps —
    # including one it writes *blank*, which is the case the old value-based
    # rule could not express. `sweep=True` is how import_annotations reuses
    # this function without pinning what it just inferred.
    if pin:
        row = conn.execute(
            "SELECT pinned_fields FROM biz_annotations WHERE id = ?", (annotation_id,)
        ).fetchone()
        pinned = {f for f in (row["pinned_fields"] or "").split(",") if f} | set(fields)
        conn.execute(
            "UPDATE biz_annotations SET pinned_fields = ? WHERE id = ?",
            (",".join(sorted(pinned)), annotation_id),
        )

    conn.commit()
    store.log(conn, "biz", "update_annotation", current.company)
    return get_annotation(conn, annotation_id)


def delete_annotation(conn: sqlite3.Connection, annotation_id: int) -> bool:
    cur = conn.execute("DELETE FROM biz_annotations WHERE id = ?", (annotation_id,))
    conn.commit()
    if not cur.rowcount:
        return False
    store.log(conn, "biz", "delete_annotation", f"#{annotation_id}")
    return True


def import_annotations(
    conn: sqlite3.Connection, payloads: list[schemas.AnnotationCreate]
) -> schemas.AnnotationImportResult:
    """Bulk upsert — the endpoint Hermes points the mail sweep at.

    Idempotent by design. A second sweep over the same mailbox reports
    everything as `unchanged` rather than doubling the register, and it only
    ever *fills* the fields in SWEEP_PRESERVED: once Blanco has written what
    the project is or marked it finished, no later sweep can undo that.
    """
    created = updated = unchanged = 0
    touched: list[schemas.Annotation] = []

    for payload in payloads:
        existing = _find_annotation(conn, payload.company, payload.account_email)
        if existing is None:
            if row := add_annotation(conn, payload):
                created += 1
                touched.append(row)
            continue

        raw = conn.execute(
            "SELECT * FROM biz_annotations WHERE id = ?", (existing.id,)
        ).fetchone()
        pinned = _pinned(raw)

        changes: dict = {}
        for field, value in payload.model_dump().items():
            if field not in ANNOTATION_COLUMNS or value in (None, ""):
                continue
            current = getattr(existing, field, None)
            if current == value:
                continue
            # A field Blanco has edited is his, whatever it now holds — that
            # includes one he deliberately cleared.
            if field in pinned:
                continue
            # Otherwise the weaker rule still applies: fill a blank, never
            # overwrite a value the sweep itself put there earlier.
            if field in SWEEP_PRESERVED and current not in (None, "", "active"):
                continue
            changes[field] = value

        if not changes:
            unchanged += 1
            touched.append(existing)
            continue

        # pin=False: a sweep's own inference must not become untouchable.
        row = update_annotation(
            conn, existing.id, schemas.AnnotationUpdate(**changes), pin=False
        )
        updated += 1
        touched.append(row or existing)

    return schemas.AnnotationImportResult(
        created=created, updated=updated, unchanged=unchanged, annotations=touched
    )
