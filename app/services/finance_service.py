"""Debts, investments, and personal cash flow.

**Advisory only.** This module records numbers and does arithmetic on them. It
places no trades, makes no payments, holds no broker credentials, and makes no
outbound calls of any kind. Every balance in here is one a human typed.

The line against the money module is personal vs business: `ventures` and
`venture_events` track what an income stream earned, this tracks what Blanco
personally owes, owns, and spends. Net worth pulls from here alone, because a
venture's gross revenue is not an asset he holds.

The one piece of real judgement is the payoff planner. Avalanche (highest rate
first) minimises interest; snowball (smallest balance first) minimises the time
to the first win. On a file like Blanco's — three debts, no recorded rates, the
smallest a fortnight of side work — the arithmetic difference is noise and the
momentum is the whole point, so the recommendation is allowed to say so rather
than defaulting to the mathematically-optimal answer nobody sticks to.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date

from app import schemas
from app.services import store

# A projection that never terminates is a bug, not an answer. 50 years is well
# past any plan worth making, so hitting it means the payment does not cover
# the interest — reported as "no payoff date" rather than looping forever.
MAX_PROJECTION_MONTHS = 600

# Below this, the interest avalanche saves over snowball is not worth giving up
# the first win for. Deliberately a flat dollar amount: a percentage of the
# balance would scale the threshold with exactly the debt that makes momentum
# matter most.
AVALANCHE_EDGE_USD = 100.0


# --- debts ------------------------------------------------------------------


def _debt(row: sqlite3.Row, today: str | None = None) -> schemas.Debt:
    amount = row["amount"] or 0
    balance = row["balance"] or 0
    overdue = None
    # Only meaningful while money is still owed: a paid debt cannot be late.
    if row["due_date"] and balance > 0:
        today = today or date.today().isoformat()
        days = (date.fromisoformat(today) - date.fromisoformat(row["due_date"])).days
        overdue = days if days > 0 else None
    return schemas.Debt(
        id=row["id"],
        creditor=row["creditor"],
        amount=round(amount, 2),
        balance=round(balance, 2),
        paid=round(amount - balance, 2),
        progress=round((amount - balance) / amount, 4) if amount else 0.0,
        interest_rate=row["interest_rate"],
        due_date=row["due_date"],
        # The vault sync writes debts in more than one statement, so a read
        # landing mid-write can see a half-populated row. Fall back rather
        # than 500 the whole finance page over one transient record.
        status=row["status"] or "outstanding",
        notes=row["notes"] or "",
        days_overdue=overdue,
        added_at=row["added_at"] or "",
        updated_at=row["updated_at"] or "",
    )


def list_debts(
    conn: sqlite3.Connection, status: str | None = None
) -> list[schemas.Debt]:
    sql = "SELECT * FROM debts"
    params: list = []
    if status:
        sql += " WHERE status = ?"
        params.append(status)
    # Paid debts sort last — they are history, not the thing to look at.
    sql += " ORDER BY status = 'paid', balance DESC, id"
    today = date.today().isoformat()
    return [_debt(r, today) for r in conn.execute(sql, params).fetchall()]


def get_debt(conn: sqlite3.Connection, debt_id: int) -> schemas.Debt | None:
    row = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    return _debt(row) if row else None


def add_debt(conn: sqlite3.Connection, payload: schemas.DebtCreate) -> schemas.Debt:
    cur = conn.execute(
        # added_at is passed rather than left to the column default: SQLite's
        # date('now') is UTC, which dates an evening entry tomorrow in Houston.
        """INSERT INTO debts (creditor, amount, balance, interest_rate, due_date, notes, added_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            payload.creditor, payload.amount, payload.amount,
            payload.interest_rate, payload.due_date, payload.notes, date.today().isoformat(),
        ),
    )
    conn.commit()
    store.log(conn, "finance", "add_debt", payload.creditor, amount=payload.amount)
    return get_debt(conn, cur.lastrowid)


def update_debt(
    conn: sqlite3.Connection, debt_id: int, payload: schemas.DebtUpdate
) -> schemas.Debt | None:
    if not conn.execute("SELECT 1 FROM debts WHERE id = ?", (debt_id,)).fetchone():
        return None
    # Balance is not settable here on purpose: it moves only through a recorded
    # payment, so the payment history and the balance can never disagree.
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_debt(conn, debt_id)
    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE debts SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), debt_id),
    )
    conn.commit()
    store.log(conn, "finance", "update_debt", str(debt_id), **fields)
    return get_debt(conn, debt_id)


def pay_debt(
    conn: sqlite3.Connection, debt_id: int, payload: schemas.DebtPaymentCreate
) -> tuple[schemas.Debt, schemas.DebtPayment] | None:
    """Record a payment and decrement the balance.

    Overpayment clamps the balance at zero rather than rejecting: handing back a
    422 because he rounded up to a clean $400 would be the wrong answer, and a
    negative balance would quietly corrupt every total that sums this column.
    """
    row = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    if not row:
        return None

    paid_on = payload.paid_on or date.today().isoformat()
    applied = min(payload.amount, row["balance"])
    new_balance = round(row["balance"] - applied, 2)
    status = "paid" if new_balance <= 0 else "partial"

    cur = conn.execute(
        "INSERT INTO debt_payments (debt_id, amount, paid_on, note) VALUES (?, ?, ?, ?)",
        (debt_id, payload.amount, paid_on, payload.note),
    )
    conn.execute(
        "UPDATE debts SET balance = ?, status = ?, updated_at = datetime('now') WHERE id = ?",
        (new_balance, status, debt_id),
    )
    conn.commit()
    store.log(
        conn, "finance", "pay_debt", row["creditor"],
        amount=payload.amount, applied=applied, balance=new_balance, status=status,
    )

    payment = conn.execute(
        "SELECT * FROM debt_payments WHERE id = ?", (cur.lastrowid,)
    ).fetchone()
    return get_debt(conn, debt_id), schemas.DebtPayment(
        id=payment["id"],
        debt_id=payment["debt_id"],
        amount=payment["amount"],
        paid_on=payment["paid_on"],
        note=payment["note"],
    )


def mark_paid(conn: sqlite3.Connection, debt_id: int) -> schemas.Debt | None:
    """Settle whatever is left in one move, recorded as a payment like any other."""
    row = conn.execute("SELECT * FROM debts WHERE id = ?", (debt_id,)).fetchone()
    if not row:
        return None
    if row["balance"] <= 0:
        return _debt(row)
    result = pay_debt(
        conn, debt_id,
        schemas.DebtPaymentCreate(amount=row["balance"], note="marked paid in full"),
    )
    return result[0] if result else None


def list_payments(
    conn: sqlite3.Connection, debt_id: int | None = None, limit: int = 100
) -> list[schemas.DebtPayment]:
    sql = "SELECT * FROM debt_payments"
    params: list = []
    if debt_id is not None:
        sql += " WHERE debt_id = ?"
        params.append(debt_id)
    sql += " ORDER BY paid_on DESC, id DESC LIMIT ?"
    params.append(limit)
    return [
        schemas.DebtPayment(
            id=r["id"], debt_id=r["debt_id"], amount=r["amount"],
            paid_on=r["paid_on"], note=r["note"],
        )
        for r in conn.execute(sql, params).fetchall()
    ]


def overdue_debts(conn: sqlite3.Connection) -> list[schemas.Debt]:
    """Still owing and past the recorded due date."""
    today = date.today().isoformat()
    rows = conn.execute(
        """SELECT * FROM debts
           WHERE balance > 0 AND due_date IS NOT NULL AND due_date < ?
           ORDER BY due_date""",
        (today,),
    ).fetchall()
    return [_debt(r, today) for r in rows]


# --- payoff planner ---------------------------------------------------------


def _add_months(start: date, months: int) -> date:
    """Same day-of-month `months` later, clamped to the month's length."""
    total = start.month - 1 + months
    year = start.year + total // 12
    month = total % 12 + 1
    # 31 Jan + 1 month is 28/29 Feb. Walking back is the convention every
    # amortisation schedule uses, and it keeps the date valid.
    day = start.day
    while day > 1:
        try:
            return date(year, month, day)
        except ValueError:
            day -= 1
    return date(year, month, 1)


def _simulate(
    debts: list[schemas.Debt], monthly_payment: float | None, today: date
) -> tuple[list[schemas.PayoffStep], int | None, str | None, float]:
    """Roll one payment down an ordered list of debts, month by month.

    The whole payment goes at the first debt still standing and the remainder
    cascades to the next — that rollover is the entire mechanic both strategies
    share, and simulating it is what makes the projected dates real rather than
    balance ÷ payment.
    """
    steps = [
        schemas.PayoffStep(
            debt_id=d.id,
            creditor=d.creditor,
            balance=d.balance,
            interest_rate=d.interest_rate,
            order=i + 1,
        )
        for i, d in enumerate(debts)
    ]
    if not monthly_payment or monthly_payment <= 0 or not debts:
        return steps, None, None, 0.0

    balances = [d.balance for d in debts]
    # An unknown rate is modelled as 0 rather than guessed at. Stated plainly in
    # the recommendation so nobody reads the projection as accounting for it.
    rates = [(d.interest_rate or 0) / 100 / 12 for d in debts]
    total_interest = 0.0
    month = 0

    while any(b > 0 for b in balances) and month < MAX_PROJECTION_MONTHS:
        month += 1
        for i, balance in enumerate(balances):
            if balance > 0 and rates[i]:
                interest = balance * rates[i]
                balances[i] = balance + interest
                total_interest += interest

        remaining = monthly_payment
        for i, balance in enumerate(balances):
            if remaining <= 0:
                break
            if balance <= 0:
                continue
            applied = min(remaining, balance)
            balances[i] = round(balance - applied, 6)
            remaining -= applied
            if balances[i] <= 0.005:
                balances[i] = 0
                if steps[i].months_to_clear is None:
                    steps[i].months_to_clear = month
                    steps[i].projected_payoff_date = _add_months(today, month).isoformat()

    if any(b > 0 for b in balances):
        # The payment never outran the interest. Orders and per-debt dates that
        # did land stay useful; the debt-free date honestly does not exist.
        return steps, None, None, round(total_interest, 2)

    return steps, month, _add_months(today, month).isoformat(), round(total_interest, 2)


def _plan(
    strategy: str, debts: list[schemas.Debt], monthly_payment: float | None, today: date
) -> schemas.PayoffPlan:
    if strategy == "avalanche":
        # Unknown rate sorts as 0 — an unasked-for rate should not jump the
        # queue ahead of one Blanco actually recorded. Balance breaks ties so
        # two 0% debts still order deterministically.
        ordered = sorted(debts, key=lambda d: (-(d.interest_rate or 0), d.balance, d.id))
    else:
        ordered = sorted(debts, key=lambda d: (d.balance, d.id))

    steps, months, payoff_date, interest = _simulate(ordered, monthly_payment, today)
    return schemas.PayoffPlan(
        strategy=strategy,
        order=steps,
        months_to_debt_free=months,
        debt_free_date=payoff_date,
        total_interest=interest,
    )


def _recommend(
    avalanche: schemas.PayoffPlan, snowball: schemas.PayoffPlan, has_rates: bool
) -> schemas.PayoffRecommendation:
    if not avalanche.order:
        return schemas.PayoffRecommendation(
            strategy="snowball",
            target_debt_id=None,
            target_creditor=None,
            headline="No debt on the books. Nothing to attack.",
            why="Every recorded debt is paid off.",
        )

    saved = round(avalanche.total_interest - snowball.total_interest, 2)
    # Negative "saved" means avalanche costs less, which is the whole point of
    # it. Only worth the trade when the gap is real money.
    edge = -saved

    if not has_rates:
        strategy, plan = "snowball", snowball
        why = (
            "No interest rates are recorded on any of these, so there is nothing for "
            "avalanche to optimise. Smallest balance first is the strategy that actually "
            "gets finished."
        )
    elif edge > AVALANCHE_EDGE_USD:
        strategy, plan = "avalanche", avalanche
        why = (
            f"Highest rate first saves ${edge:,.0f} in interest over snowball — "
            "that is real money, worth waiting longer for the first win."
        )
    else:
        strategy, plan = "snowball", snowball
        why = (
            f"Avalanche only saves ${max(edge, 0):,.0f} in interest here. That is not worth "
            "giving up the first win for — kill the small one and roll the payment forward."
        )

    target = plan.order[0]
    date_clause = (
        f" You'd clear it by {target.projected_payoff_date}."
        if target.projected_payoff_date
        else ""
    )
    return schemas.PayoffRecommendation(
        strategy=strategy,
        target_debt_id=target.debt_id,
        target_creditor=target.creditor,
        headline=(
            f"Attack {target.creditor} — ${target.balance:,.2f}. "
            f"Everything spare goes there until it is gone, then roll that same payment "
            f"into the next one.{date_clause}"
        ),
        why=why,
    )


def payoff_plan(
    conn: sqlite3.Connection, monthly_payment: float | None = None
) -> schemas.PayoffPlanResponse:
    debts = [d for d in list_debts(conn) if d.balance > 0]
    today = date.today()
    has_rates = any((d.interest_rate or 0) > 0 for d in debts)

    avalanche = _plan("avalanche", debts, monthly_payment, today)
    snowball = _plan("snowball", debts, monthly_payment, today)

    return schemas.PayoffPlanResponse(
        total_debt=round(sum(d.balance for d in debts), 2),
        debt_count=len(debts),
        monthly_payment=monthly_payment,
        avalanche=avalanche,
        snowball=snowball,
        recommendation=_recommend(avalanche, snowball, has_rates),
    )


# --- investments ------------------------------------------------------------


def _account(row: sqlite3.Row) -> schemas.InvestmentAccount:
    balance = row["balance"] or 0
    contributed = row["contributions_to_date"] or 0
    return schemas.InvestmentAccount(
        id=row["id"],
        name=row["name"],
        type=row["type"],
        balance=round(balance, 2),
        contributions_to_date=round(contributed, 2),
        gain=round(balance - contributed, 2),
        notes=row["notes"],
        updated_at=row["updated_at"],
    )


def list_accounts(conn: sqlite3.Connection) -> list[schemas.InvestmentAccount]:
    rows = conn.execute(
        "SELECT * FROM investment_accounts ORDER BY balance DESC, id"
    ).fetchall()
    return [_account(r) for r in rows]


def get_account(conn: sqlite3.Connection, account_id: int) -> schemas.InvestmentAccount | None:
    row = conn.execute(
        "SELECT * FROM investment_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    return _account(row) if row else None


def add_account(
    conn: sqlite3.Connection, payload: schemas.InvestmentAccountCreate
) -> schemas.InvestmentAccount:
    cur = conn.execute(
        """INSERT INTO investment_accounts (name, type, balance, contributions_to_date, notes)
           VALUES (?, ?, ?, ?, ?)""",
        (payload.name, payload.type, payload.balance, payload.contributions_to_date, payload.notes),
    )
    conn.commit()
    store.log(conn, "finance", "add_account", payload.name, balance=payload.balance)
    return get_account(conn, cur.lastrowid)


def update_account(
    conn: sqlite3.Connection, account_id: int, payload: schemas.InvestmentAccountUpdate
) -> schemas.InvestmentAccount | None:
    if not conn.execute(
        "SELECT 1 FROM investment_accounts WHERE id = ?", (account_id,)
    ).fetchone():
        return None
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_account(conn, account_id)
    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE investment_accounts SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), account_id),
    )
    conn.commit()
    store.log(conn, "finance", "update_account", str(account_id), **fields)
    return get_account(conn, account_id)


def delete_account(conn: sqlite3.Connection, account_id: int) -> bool:
    row = conn.execute(
        "SELECT name FROM investment_accounts WHERE id = ?", (account_id,)
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM investment_accounts WHERE id = ?", (account_id,))
    conn.commit()
    store.log(conn, "finance", "delete_account", row["name"])
    return True


def _goal(row: sqlite3.Row) -> schemas.InvestmentGoal:
    target = row["target_amount"] or 0
    current = row["current_value"] or 0
    monthly = row["monthly_contribution"] or 0
    gap = max(target - current, 0)

    months = None
    if monthly > 0:
        # Contributions only. Projecting growth would mean picking a return
        # rate, and a made-up rate in a goal tracker reads as a promise.
        months = 0 if gap <= 0 else int(-(-gap // monthly))

    on_track = None
    if row["target_date"] and months is not None:
        months_available = _months_between(date.today(), date.fromisoformat(row["target_date"]))
        on_track = months <= months_available

    return schemas.InvestmentGoal(
        id=row["id"],
        name=row["name"],
        target_amount=round(target, 2),
        monthly_contribution=round(monthly, 2),
        target_date=row["target_date"],
        current_value=round(current, 2),
        progress=round(min(current / target, 1.0), 4) if target else 0.0,
        months_at_current_rate=months,
        on_track=on_track,
        notes=row["notes"],
        updated_at=row["updated_at"],
    )


def _months_between(start: date, end: date) -> int:
    months = (end.year - start.year) * 12 + (end.month - start.month)
    if end.day < start.day:
        months -= 1
    return months


def list_goals(conn: sqlite3.Connection) -> list[schemas.InvestmentGoal]:
    rows = conn.execute(
        "SELECT * FROM investment_goals ORDER BY target_date IS NULL, target_date, id"
    ).fetchall()
    return [_goal(r) for r in rows]


def get_goal(conn: sqlite3.Connection, goal_id: int) -> schemas.InvestmentGoal | None:
    row = conn.execute("SELECT * FROM investment_goals WHERE id = ?", (goal_id,)).fetchone()
    return _goal(row) if row else None


def add_goal(
    conn: sqlite3.Connection, payload: schemas.InvestmentGoalCreate
) -> schemas.InvestmentGoal:
    cur = conn.execute(
        """INSERT INTO investment_goals
             (name, target_amount, monthly_contribution, target_date, current_value, notes)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            payload.name, payload.target_amount, payload.monthly_contribution,
            payload.target_date, payload.current_value, payload.notes,
        ),
    )
    conn.commit()
    store.log(conn, "finance", "add_goal", payload.name, target=payload.target_amount)
    return get_goal(conn, cur.lastrowid)


def update_goal(
    conn: sqlite3.Connection, goal_id: int, payload: schemas.InvestmentGoalUpdate
) -> schemas.InvestmentGoal | None:
    if not conn.execute("SELECT 1 FROM investment_goals WHERE id = ?", (goal_id,)).fetchone():
        return None
    fields = payload.model_dump(exclude_none=True)
    if not fields:
        return get_goal(conn, goal_id)
    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE investment_goals SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), goal_id),
    )
    conn.commit()
    store.log(conn, "finance", "update_goal", str(goal_id), **fields)
    return get_goal(conn, goal_id)


def delete_goal(conn: sqlite3.Connection, goal_id: int) -> bool:
    row = conn.execute("SELECT name FROM investment_goals WHERE id = ?", (goal_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM investment_goals WHERE id = ?", (goal_id,))
    conn.commit()
    store.log(conn, "finance", "delete_goal", row["name"])
    return True


# --- cash flow --------------------------------------------------------------


def _transaction(row: sqlite3.Row) -> schemas.Transaction:
    return schemas.Transaction(
        id=row["id"],
        occurred_on=row["occurred_on"],
        category=row["category"],
        amount=round(row["amount"], 2),
        kind=row["kind"],
        note=row["note"],
    )


def list_transactions(
    conn: sqlite3.Connection,
    month: str | None = None,
    kind: str | None = None,
    limit: int = 100,
) -> list[schemas.Transaction]:
    sql = "SELECT * FROM transactions"
    where, params = [], []
    if month:
        where.append("occurred_on LIKE ?")
        params.append(f"{month}%")
    if kind:
        where.append("kind = ?")
        params.append(kind)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY occurred_on DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_transaction(r) for r in conn.execute(sql, params).fetchall()]


def add_transaction(
    conn: sqlite3.Connection, payload: schemas.TransactionCreate
) -> schemas.Transaction:
    occurred = payload.occurred_on or date.today().isoformat()
    cur = conn.execute(
        """INSERT INTO transactions (occurred_on, category, amount, kind, note)
           VALUES (?, ?, ?, ?, ?)""",
        (occurred, payload.category, payload.amount, payload.kind, payload.note),
    )
    conn.commit()
    store.log(
        conn, "finance", payload.kind, payload.category,
        amount=payload.amount, occurred_on=occurred,
    )
    row = conn.execute("SELECT * FROM transactions WHERE id = ?", (cur.lastrowid,)).fetchone()
    return _transaction(row)


def delete_transaction(conn: sqlite3.Connection, transaction_id: int) -> bool:
    row = conn.execute(
        "SELECT category, amount FROM transactions WHERE id = ?", (transaction_id,)
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM transactions WHERE id = ?", (transaction_id,))
    conn.commit()
    store.log(conn, "finance", "delete_transaction", row["category"], amount=row["amount"])
    return True


def list_budget(conn: sqlite3.Connection, month: str | None = None) -> list[schemas.BudgetCategory]:
    """Budget per category with the month's actual spend against it."""
    month = month or date.today().strftime("%Y-%m")
    spend = {
        r["category"]: r["total"]
        for r in conn.execute(
            """SELECT category, SUM(amount) AS total FROM transactions
               WHERE kind = 'expense' AND occurred_on LIKE ? GROUP BY category""",
            (f"{month}%",),
        ).fetchall()
    }
    out = []
    for row in conn.execute("SELECT * FROM budget_categories ORDER BY name").fetchall():
        budget = row["monthly_budget"] or 0
        spent = spend.get(row["name"], 0)
        out.append(
            schemas.BudgetCategory(
                id=row["id"],
                name=row["name"],
                monthly_budget=round(budget, 2),
                spent=round(spent, 2),
                remaining=round(max(budget - spent, 0), 2),
                variance=round(budget - spent, 2),
                over_budget=spent > budget,
                notes=row["notes"],
            )
        )
    return out


def add_budget_category(
    conn: sqlite3.Connection, payload: schemas.BudgetCategoryCreate
) -> schemas.BudgetCategory | None:
    """None when the category already exists — the name is the identity."""
    if conn.execute(
        "SELECT 1 FROM budget_categories WHERE name = ?", (payload.name,)
    ).fetchone():
        return None
    conn.execute(
        "INSERT INTO budget_categories (name, monthly_budget, notes) VALUES (?, ?, ?)",
        (payload.name, payload.monthly_budget, payload.notes),
    )
    conn.commit()
    store.log(conn, "finance", "add_budget", payload.name, budget=payload.monthly_budget)
    return next((c for c in list_budget(conn) if c.name == payload.name), None)


def update_budget_category(
    conn: sqlite3.Connection, category_id: int, payload: schemas.BudgetCategoryUpdate
) -> schemas.BudgetCategory | None:
    row = conn.execute(
        "SELECT name FROM budget_categories WHERE id = ?", (category_id,)
    ).fetchone()
    if not row:
        return None
    fields = payload.model_dump(exclude_none=True)
    if fields:
        assignments = store.set_clause(fields, type(payload).model_fields)
        conn.execute(
            f"UPDATE budget_categories SET {assignments}, updated_at = datetime('now') "
            "WHERE id = ?",
            (*fields.values(), category_id),
        )
        conn.commit()
        store.log(conn, "finance", "update_budget", row["name"], **fields)
    return next((c for c in list_budget(conn) if c.id == category_id), None)


def delete_budget_category(conn: sqlite3.Connection, category_id: int) -> bool:
    row = conn.execute(
        "SELECT name FROM budget_categories WHERE id = ?", (category_id,)
    ).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM budget_categories WHERE id = ?", (category_id,))
    conn.commit()
    store.log(conn, "finance", "delete_budget", row["name"])
    return True


def monthly_summary(conn: sqlite3.Connection, month: str | None = None) -> schemas.MonthlySummary:
    month = month or date.today().strftime("%Y-%m")
    totals = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN kind = 'income' THEN amount END), 0) AS income,
             COALESCE(SUM(CASE WHEN kind = 'expense' THEN amount END), 0) AS expenses,
             COALESCE(SUM(CASE WHEN kind = 'debt_payment' THEN amount END), 0) AS debt
           FROM transactions WHERE occurred_on LIKE ?""",
        (f"{month}%",),
    ).fetchone()

    income = round(totals["income"], 2)
    expenses = round(totals["expenses"], 2)
    debt_payments = round(totals["debt"], 2)
    net = round(income - expenses - debt_payments, 2)

    budget = list_budget(conn, month)
    budgeted_names = {c.name for c in budget}
    uncategorized = sum(
        r["total"]
        for r in conn.execute(
            """SELECT category, SUM(amount) AS total FROM transactions
               WHERE kind = 'expense' AND occurred_on LIKE ? GROUP BY category""",
            (f"{month}%",),
        ).fetchall()
        if r["category"] not in budgeted_names
    )

    return schemas.MonthlySummary(
        month=month,
        income=income,
        expenses=expenses,
        debt_payments=debt_payments,
        net=net,
        savings_rate=round(net / income, 4) if income else 0.0,
        budget=budget,
        total_budgeted=round(sum(c.monthly_budget for c in budget), 2),
        uncategorized_spend=round(uncategorized, 2),
    )


def net_worth(conn: sqlite3.Connection) -> schemas.NetWorth:
    """Assets minus liabilities.

    Ventures are deliberately excluded. A business's monthly revenue is not a
    balance Blanco holds, and capital already deployed into one is not cash —
    counting either would inflate this into a number he cannot spend.
    """
    accounts = list_accounts(conn)
    debts = [d for d in list_debts(conn) if d.balance > 0]

    by_type: dict[str, float] = {}
    for account in accounts:
        by_type[account.type] = round(by_type.get(account.type, 0) + account.balance, 2)

    assets = round(sum(a.balance for a in accounts), 2)
    liabilities = round(sum(d.balance for d in debts), 2)

    return schemas.NetWorth(
        assets=assets,
        liabilities=liabilities,
        net_worth=round(assets - liabilities, 2),
        by_account_type=by_type,
        debt_total=liabilities,
        debt_count=len(debts),
        as_of=date.today().isoformat(),
    )


# --- notes ------------------------------------------------------------------
# Where Blanco writes down what he opened and when, so the details are not
# living in his head or in a text thread six months from now.
#
# This is not a password manager and must not become one. The store is an
# unencrypted SQLite file on a workstation, so `last4` is capped at four digits
# at the schema level and nothing here should ever hold a credential.


def _note(row: sqlite3.Row) -> schemas.FinanceNote:
    return schemas.FinanceNote(
        id=row["id"],
        title=row["title"],
        category=row["category"],
        body=row["body"],
        institution=row["institution"],
        last4=row["last4"],
        opened_on=row["opened_on"],
        pinned=bool(row["pinned"]),
        archived=bool(row["archived"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def list_notes(
    conn: sqlite3.Connection,
    category: str | None = None,
    include_archived: bool = False,
    search: str | None = None,
    limit: int = 200,
) -> list[schemas.FinanceNote]:
    sql = "SELECT * FROM finance_notes"
    where, params = [], []
    if not include_archived:
        where.append("archived = 0")
    if category:
        where.append("category = ?")
        params.append(category)
    if search:
        # Title, body and institution together — he is as likely to remember
        # "Chime" or "the one with the $200 deposit" as whatever he titled it.
        where.append("(title LIKE ? OR body LIKE ? OR institution LIKE ?)")
        params.extend([f"%{search}%"] * 3)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY pinned DESC, COALESCE(opened_on, created_at) DESC, id DESC LIMIT ?"
    params.append(limit)
    return [_note(r) for r in conn.execute(sql, params).fetchall()]


def get_note(conn: sqlite3.Connection, note_id: int) -> schemas.FinanceNote | None:
    row = conn.execute("SELECT * FROM finance_notes WHERE id = ?", (note_id,)).fetchone()
    return _note(row) if row else None


def add_note(
    conn: sqlite3.Connection, payload: schemas.FinanceNoteCreate
) -> schemas.FinanceNote:
    cur = conn.execute(
        """INSERT INTO finance_notes
             (title, category, body, institution, last4, opened_on, pinned)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (
            payload.title, payload.category, payload.body, payload.institution,
            payload.last4, payload.opened_on, int(payload.pinned),
        ),
    )
    conn.commit()
    # The body is not logged. The audit trail is for *what changed*, and
    # copying free text into it doubles every place the note has to be deleted
    # from if he ever writes something in there he did not mean to keep.
    store.log(conn, "finance", "add_note", payload.title, category=payload.category)
    return get_note(conn, cur.lastrowid)


def update_note(
    conn: sqlite3.Connection, note_id: int, payload: schemas.FinanceNoteUpdate
) -> schemas.FinanceNote | None:
    row = conn.execute("SELECT title FROM finance_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return None
    fields = payload.model_dump(exclude_none=True)
    for flag in ("pinned", "archived"):
        if flag in fields:
            fields[flag] = int(fields[flag])
    if not fields:
        return get_note(conn, note_id)
    assignments = store.set_clause(fields, type(payload).model_fields)
    conn.execute(  # nosemgrep: sqlalchemy-execute-raw-query -- columns whitelisted by store.set_clause, values bound
        f"UPDATE finance_notes SET {assignments}, updated_at = datetime('now') WHERE id = ?",
        (*fields.values(), note_id),
    )
    conn.commit()
    store.log(conn, "finance", "update_note", row["title"], fields=sorted(fields))
    return get_note(conn, note_id)


def delete_note(conn: sqlite3.Connection, note_id: int) -> bool:
    row = conn.execute("SELECT title FROM finance_notes WHERE id = ?", (note_id,)).fetchone()
    if not row:
        return False
    conn.execute("DELETE FROM finance_notes WHERE id = ?", (note_id,))
    conn.commit()
    store.log(conn, "finance", "delete_note", row["title"])
    return True


# --- overview + log ---------------------------------------------------------


def overview(conn: sqlite3.Connection) -> schemas.FinanceOverview:
    debts = list_debts(conn)
    plan = payoff_plan(conn)
    return schemas.FinanceOverview(
        debts=debts,
        total_debt=round(sum(d.balance for d in debts), 2),
        overdue_debts=overdue_debts(conn),
        next_move=plan.recommendation,
        investment_accounts=list_accounts(conn),
        investment_goals=list_goals(conn),
        net_worth=net_worth(conn),
        month=monthly_summary(conn),
        pinned_notes=[n for n in list_notes(conn) if n.pinned],
    )


def activity(conn: sqlite3.Connection, limit: int = 100) -> list[schemas.ActivityItem]:
    """The finance module's slice of the shared append-only audit trail."""
    rows = conn.execute(
        "SELECT * FROM activity_log WHERE module = 'finance' ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        schemas.ActivityItem(
            id=r["id"],
            module=r["module"],
            verb=r["verb"],
            subject=r["subject"],
            meta=json.loads(r["meta_json"] or "{}"),
            created_at=r["created_at"],
        )
        for r in rows
    ]
