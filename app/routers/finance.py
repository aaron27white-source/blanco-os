"""Debts, investments, cash flow, and the payoff planner.

Advisory only — every endpoint here reads or records a number. None of them
moves money, and none makes an outbound call.
"""

from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query

from app import schemas
from app.deps import get_db
from app.services import debt_vault_sync, finance_service

router = APIRouter(prefix="/api/finance", tags=["finance"])


@router.get("", response_model=schemas.FinanceOverview, summary="The whole finance tab in one call")
def overview(db: sqlite3.Connection = Depends(get_db)):
    return finance_service.overview(db)


@router.get("/net-worth", response_model=schemas.NetWorth,
            summary="Assets minus liabilities. Ventures excluded — see the service docstring.")
def net_worth(db: sqlite3.Connection = Depends(get_db)):
    return finance_service.net_worth(db)


@router.get("/log", response_model=list[schemas.ActivityItem],
            summary="Append-only audit trail of every finance write")
def activity(
    limit: int = Query(100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.activity(db, limit=limit)


# --- debts ------------------------------------------------------------------
# The literal paths are declared before /{debt_id} so "payoff-plan" is never
# read as a debt id.


@router.get("/debts/payoff-plan", response_model=schemas.PayoffPlanResponse,
            summary="Avalanche and snowball, with a recommendation")
def payoff_plan(
    monthly_payment: float | None = Query(
        None, gt=0,
        description="What he can put at debt each month. Omit for ordering without dates.",
    ),
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.payoff_plan(db, monthly_payment=monthly_payment)


@router.get("/debts/overdue", response_model=list[schemas.Debt],
            summary="Still owing and past the due date")
def overdue_debts(db: sqlite3.Connection = Depends(get_db)):
    return finance_service.overdue_debts(db)


@router.get("/debts", response_model=list[schemas.Debt])
def list_debts(
    status: schemas.DebtStatus | None = None,
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.list_debts(db, status=status)


@router.post("/debts", response_model=schemas.Debt, status_code=201)
def add_debt(payload: schemas.DebtCreate, db: sqlite3.Connection = Depends(get_db)):
    debt = finance_service.add_debt(db, payload)
    debt_vault_sync.export_debts(db)
    return debt


@router.get("/debts/{debt_id}", response_model=schemas.Debt)
def get_debt(debt_id: int, db: sqlite3.Connection = Depends(get_db)):
    debt = finance_service.get_debt(db, debt_id)
    if not debt:
        raise HTTPException(status_code=404, detail="debt not found")
    return debt


@router.patch("/debts/{debt_id}", response_model=schemas.Debt,
              summary="Edit the terms. Balance moves only through a payment.")
def update_debt(
    debt_id: int,
    payload: schemas.DebtUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    debt = finance_service.update_debt(db, debt_id, payload)
    if not debt:
        raise HTTPException(status_code=404, detail="debt not found")
    debt_vault_sync.export_debts(db)
    return debt


@router.post("/debts/{debt_id}/payments", response_model=schemas.Debt, status_code=201,
             summary="Record a payment and decrement the balance")
def pay_debt(
    debt_id: int,
    payload: schemas.DebtPaymentCreate,
    db: sqlite3.Connection = Depends(get_db),
):
    result = finance_service.pay_debt(db, debt_id, payload)
    if not result:
        raise HTTPException(status_code=404, detail="debt not found")
    debt_vault_sync.export_debts(db)
    return result[0]


@router.get("/debts/{debt_id}/payments", response_model=list[schemas.DebtPayment])
def list_payments(
    debt_id: int,
    limit: int = Query(100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    if not finance_service.get_debt(db, debt_id):
        raise HTTPException(status_code=404, detail="debt not found")
    return finance_service.list_payments(db, debt_id=debt_id, limit=limit)


@router.post("/debts/{debt_id}/paid", response_model=schemas.Debt,
             summary="Settle the remaining balance in one move")
def mark_paid(debt_id: int, db: sqlite3.Connection = Depends(get_db)):
    debt = finance_service.mark_paid(db, debt_id)
    if not debt:
        raise HTTPException(status_code=404, detail="debt not found")
    debt_vault_sync.export_debts(db)
    return debt


# --- investments ------------------------------------------------------------


@router.get("/investments", response_model=list[schemas.InvestmentAccount],
            summary="Accounts. Manual balances — v1 fetches no market data.")
def list_accounts(db: sqlite3.Connection = Depends(get_db)):
    return finance_service.list_accounts(db)


@router.post("/investments", response_model=schemas.InvestmentAccount, status_code=201)
def add_account(
    payload: schemas.InvestmentAccountCreate, db: sqlite3.Connection = Depends(get_db)
):
    return finance_service.add_account(db, payload)


@router.patch("/investments/{account_id}", response_model=schemas.InvestmentAccount)
def update_account(
    account_id: int,
    payload: schemas.InvestmentAccountUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    account = finance_service.update_account(db, account_id, payload)
    if not account:
        raise HTTPException(status_code=404, detail="account not found")
    return account


@router.delete("/investments/{account_id}", response_model=schemas.Ack)
def delete_account(account_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not finance_service.delete_account(db, account_id):
        raise HTTPException(status_code=404, detail="account not found")
    return schemas.Ack(message="deleted")


@router.get("/goals", response_model=list[schemas.InvestmentGoal])
def list_goals(db: sqlite3.Connection = Depends(get_db)):
    return finance_service.list_goals(db)


@router.post("/goals", response_model=schemas.InvestmentGoal, status_code=201)
def add_goal(payload: schemas.InvestmentGoalCreate, db: sqlite3.Connection = Depends(get_db)):
    return finance_service.add_goal(db, payload)


@router.patch("/goals/{goal_id}", response_model=schemas.InvestmentGoal)
def update_goal(
    goal_id: int,
    payload: schemas.InvestmentGoalUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    goal = finance_service.update_goal(db, goal_id, payload)
    if not goal:
        raise HTTPException(status_code=404, detail="goal not found")
    return goal


@router.delete("/goals/{goal_id}", response_model=schemas.Ack)
def delete_goal(goal_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not finance_service.delete_goal(db, goal_id):
        raise HTTPException(status_code=404, detail="goal not found")
    return schemas.Ack(message="deleted")


# --- cash flow --------------------------------------------------------------


@router.get("/cashflow", response_model=schemas.MonthlySummary,
            summary="Income vs expenses vs debt payments, with budget variance")
def monthly_summary(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$", description="YYYY-MM. Defaults to this month."),
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.monthly_summary(db, month=month)


@router.get("/cashflow/transactions", response_model=list[schemas.Transaction])
def list_transactions(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    kind: schemas.TransactionKind | None = None,
    limit: int = Query(100, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.list_transactions(db, month=month, kind=kind, limit=limit)


@router.post("/cashflow/transactions", response_model=schemas.Transaction, status_code=201)
def add_transaction(
    payload: schemas.TransactionCreate, db: sqlite3.Connection = Depends(get_db)
):
    return finance_service.add_transaction(db, payload)


@router.delete("/cashflow/transactions/{transaction_id}", response_model=schemas.Ack)
def delete_transaction(transaction_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not finance_service.delete_transaction(db, transaction_id):
        raise HTTPException(status_code=404, detail="transaction not found")
    return schemas.Ack(message="deleted")


@router.get("/cashflow/budget", response_model=list[schemas.BudgetCategory])
def list_budget(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.list_budget(db, month=month)


@router.post("/cashflow/budget", response_model=schemas.BudgetCategory, status_code=201)
def add_budget_category(
    payload: schemas.BudgetCategoryCreate, db: sqlite3.Connection = Depends(get_db)
):
    category = finance_service.add_budget_category(db, payload)
    if not category:
        raise HTTPException(status_code=409, detail="that category is already budgeted")
    return category


@router.patch("/cashflow/budget/{category_id}", response_model=schemas.BudgetCategory)
def update_budget_category(
    category_id: int,
    payload: schemas.BudgetCategoryUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    category = finance_service.update_budget_category(db, category_id, payload)
    if not category:
        raise HTTPException(status_code=404, detail="budget category not found")
    return category


@router.delete("/cashflow/budget/{category_id}", response_model=schemas.Ack)
def delete_budget_category(category_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not finance_service.delete_budget_category(db, category_id):
        raise HTTPException(status_code=404, detail="budget category not found")
    return schemas.Ack(message="deleted")


# --- notes ------------------------------------------------------------------


@router.get("/notes", response_model=list[schemas.FinanceNote],
            summary="Accounts opened, cards applied for, terms worth remembering")
def list_notes(
    category: schemas.NoteCategory | None = None,
    include_archived: bool = False,
    q: str | None = Query(None, description="Match against title, body, or institution"),
    limit: int = Query(200, ge=1, le=500),
    db: sqlite3.Connection = Depends(get_db),
):
    return finance_service.list_notes(
        db, category=category, include_archived=include_archived, search=q, limit=limit
    )


@router.post("/notes", response_model=schemas.FinanceNote, status_code=201,
             summary="Write one down. Never store passwords, PINs, or full account numbers here.")
def add_note(payload: schemas.FinanceNoteCreate, db: sqlite3.Connection = Depends(get_db)):
    return finance_service.add_note(db, payload)


@router.get("/notes/{note_id}", response_model=schemas.FinanceNote)
def get_note(note_id: int, db: sqlite3.Connection = Depends(get_db)):
    note = finance_service.get_note(db, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.patch("/notes/{note_id}", response_model=schemas.FinanceNote,
              summary="Edit, pin, or archive")
def update_note(
    note_id: int,
    payload: schemas.FinanceNoteUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    note = finance_service.update_note(db, note_id, payload)
    if not note:
        raise HTTPException(status_code=404, detail="note not found")
    return note


@router.delete("/notes/{note_id}", response_model=schemas.Ack)
def delete_note(note_id: int, db: sqlite3.Connection = Depends(get_db)):
    if not finance_service.delete_note(db, note_id):
        raise HTTPException(status_code=404, detail="note not found")
    return schemas.Ack(message="deleted")


# --- vault ------------------------------------------------------------------


@router.post("/vault/sync", response_model=schemas.VaultSyncResult,
             summary="Rebuild Debt-Tracker.md from the database")
def sync_vault(db: sqlite3.Connection = Depends(get_db)):
    ok, detail, written = debt_vault_sync.export_debts(db)
    return schemas.VaultSyncResult(ok=ok, detail=detail, debts_written=written)


@router.post("/vault/import", response_model=schemas.VaultSyncResult,
             summary="Seed debts from Debt-Tracker.md. No-op once any debt exists.")
def import_vault(db: sqlite3.Connection = Depends(get_db)):
    inserted = debt_vault_sync.import_debts(db)
    return schemas.VaultSyncResult(
        ok=True,
        detail=f"{inserted} debt(s) imported" if inserted else "nothing to import",
        debts_written=inserted,
    )
