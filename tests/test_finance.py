"""Finance module: debts, payoff math, investments, cash flow, notes, vault sync.

The payoff planner gets the most coverage here because it is the only part of
the module that does real arithmetic — everything else is CRUD, and CRUD that
breaks breaks loudly. A projection that is quietly six months wrong does not.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.services import debt_vault_sync, finance_service


DEBT_TRACKER = """# Debt Tracker

> **Purpose:** Track money Blanco owes and is owed.
> **Created:** 2026-08-02 · **Updated:** 2026-08-02

## 💸 What I Owe

| Creditor | Amount | Date Added | Status | Notes |
|----------|--------|------------|--------|-------|
| Cedar Bank | $5,000.00 | 2026-08-02 | Outstanding | demo loan |
| Birch Loans | $350.00 | 2026-08-02 | Outstanding | |
| Pine Credit | $2,500.00 | 2026-08-02 | Outstanding | |

**Total owed: $7,850.00**

---

## 📥 What I'm Owed

| Debtor | Amount | Date Added | Status | Notes |
|--------|--------|------------|--------|-------|
| — | — | — | — | — |

---

## 📝 Log

- **2026-08-02** — Added: Cedar Bank $5,000 · Birch Loans $350 · Pine Credit $2,500
"""


@pytest.fixture
def tracker(vault):
    """The real Debt-Tracker.md, in the throwaway vault."""
    path = vault / "Personal" / "areas" / "finance" / "Debt-Tracker.md"
    path.write_text(DEBT_TRACKER, encoding="utf-8")
    return path


# --- seeding ----------------------------------------------------------------


def test_migration_seeds_the_three_vault_debts(client):
    debts = client.get("/api/finance/debts").json()
    assert {(d["creditor"], d["balance"]) for d in debts} == {
        ("Cedar Bank", 5000.0), ("Birch Loans", 350.0), ("Pine Credit", 2500.0)
    }
    assert all(d["status"] == "outstanding" for d in debts)
    assert sum(d["balance"] for d in debts) == 7850.0


def test_finance_module_is_registered(client):
    modules = client.get("/api/system").json()["modules"]
    finance = next(m for m in modules if m["id"] == "finance")
    assert finance["api_prefix"] == "/api/finance"
    assert finance["writable"] is True


def test_overview_reports_advisory_only(client):
    body = client.get("/api/finance").json()
    assert body["advisory_only"] is True
    assert body["total_debt"] == 7850.0
    assert body["next_move"]["target_creditor"] == "Birch Loans"


# --- payments ---------------------------------------------------------------


def test_partial_payment_decrements_balance_and_keeps_principal(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")

    updated = client.post(
        f"/api/finance/debts/{bird['id']}/payments", json={"amount": 100}
    ).json()
    assert updated["balance"] == 250.0
    assert updated["amount"] == 350.0, "principal must not move"
    assert updated["paid"] == 100.0
    assert updated["status"] == "partial"
    assert updated["progress"] == pytest.approx(100 / 350, rel=1e-3)


def test_paying_it_off_marks_it_paid(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    client.post(f"/api/finance/debts/{bird['id']}/payments", json={"amount": 200})
    final = client.post(f"/api/finance/debts/{bird['id']}/payments", json={"amount": 150}).json()
    assert final["balance"] == 0
    assert final["status"] == "paid"


def test_overpayment_clamps_at_zero_rather_than_going_negative(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    final = client.post(
        f"/api/finance/debts/{bird['id']}/payments", json={"amount": 400}
    ).json()
    assert final["balance"] == 0
    assert final["status"] == "paid"
    # The payment is recorded at what he actually paid, not at what was owed.
    payments = client.get(f"/api/finance/debts/{bird['id']}/payments").json()
    assert payments[0]["amount"] == 400


def test_mark_paid_settles_the_remainder_as_a_payment(client):
    p = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Pine Credit")
    client.post(f"/api/finance/debts/{p['id']}/payments", json={"amount": 500})
    final = client.post(f"/api/finance/debts/{p['id']}/paid").json()
    assert final["status"] == "paid"
    assert [x["amount"] for x in client.get(f"/api/finance/debts/{p['id']}/payments").json()] == [
        2000.0, 500.0
    ]


def test_patch_cannot_move_the_balance(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    updated = client.patch(
        f"/api/finance/debts/{bird['id']}", json={"balance": 1, "notes": "owed since June"}
    ).json()
    assert updated["balance"] == 350.0
    assert updated["notes"] == "owed since June"


def test_unknown_debt_is_404(client):
    assert client.get("/api/finance/debts/9999").status_code == 404
    assert client.post("/api/finance/debts/9999/payments", json={"amount": 5}).status_code == 404
    assert client.post("/api/finance/debts/9999/paid").status_code == 404


# --- payoff planner ---------------------------------------------------------


def test_payoff_orders_snowball_by_balance_and_avalanche_by_rate(client):
    for creditor, rate in (("Cedar Bank", 0), ("Birch Loans", 2), ("Pine Credit", 9)):
        debt = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == creditor)
        client.patch(f"/api/finance/debts/{debt['id']}", json={"interest_rate": rate})

    plan = client.get("/api/finance/debts/payoff-plan").json()
    assert [s["creditor"] for s in plan["snowball"]["order"]] == ["Birch Loans", "Pine Credit", "Cedar Bank"]
    assert [s["creditor"] for s in plan["avalanche"]["order"]] == ["Pine Credit", "Birch Loans", "Cedar Bank"]
    assert plan["total_debt"] == 7850.0
    assert plan["debt_count"] == 3


def test_payoff_without_a_payment_gives_order_but_no_dates(client):
    plan = client.get("/api/finance/debts/payoff-plan").json()
    assert plan["monthly_payment"] is None
    assert plan["snowball"]["debt_free_date"] is None
    assert all(s["projected_payoff_date"] is None for s in plan["snowball"]["order"])
    # Ordering is still the useful half.
    assert [s["order"] for s in plan["snowball"]["order"]] == [1, 2, 3]


def test_payoff_projects_dates_and_rolls_the_payment_forward(client):
    plan = client.get(
        "/api/finance/debts/payoff-plan", params={"monthly_payment": 1000}
    ).json()
    snowball = plan["snowball"]

    # $1,000/mo against $7,850 with no interest: 8 months, and Birch Loans ($350) is
    # gone in the first — the rollover is what puts P at month 3, not month 4.
    assert snowball["months_to_debt_free"] == 8
    by_creditor = {s["creditor"]: s["months_to_clear"] for s in snowball["order"]}
    assert by_creditor == {"Birch Loans": 1, "Pine Credit": 3, "Cedar Bank": 8}
    assert snowball["total_interest"] == 0
    assert snowball["debt_free_date"] > date.today().isoformat()


def test_zero_interest_recommends_snowball_for_the_momentum(client):
    plan = client.get(
        "/api/finance/debts/payoff-plan", params={"monthly_payment": 500}
    ).json()
    rec = plan["recommendation"]
    assert rec["strategy"] == "snowball"
    assert rec["target_creditor"] == "Birch Loans"
    assert "350" in rec["headline"]
    assert "nothing for avalanche to optimise" in rec["why"]


def test_a_big_interest_gap_flips_the_recommendation_to_avalanche(client):
    # A $1,800 interest-free card sits ahead of a 29% balance in the snowball
    # queue, so momentum costs ~$600 in interest. That is when the trade is
    # worth making, and the recommendation has to notice.
    for creditor, rate in (("Cedar Bank", 0), ("Birch Loans", 0), ("Pine Credit", 29)):
        debt = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == creditor)
        client.patch(f"/api/finance/debts/{debt['id']}", json={"interest_rate": rate})
    client.post(
        "/api/finance/debts", json={"creditor": "Card", "amount": 1800, "interest_rate": 0}
    )

    plan = client.get(
        "/api/finance/debts/payoff-plan", params={"monthly_payment": 300}
    ).json()
    saved = plan["snowball"]["total_interest"] - plan["avalanche"]["total_interest"]
    assert saved > 100, "the scenario must clear the threshold, or this proves nothing"
    assert plan["recommendation"]["strategy"] == "avalanche"
    assert plan["recommendation"]["target_creditor"] == "Pine Credit"
    assert "saves $605" in plan["recommendation"]["why"]


def test_a_small_interest_gap_keeps_the_recommendation_on_snowball(client):
    # Same shape, no interest-free debt in the way: avalanche only saves ~$89,
    # which is not worth giving up the first win for.
    for creditor, rate in (("Cedar Bank", 0), ("Birch Loans", 0), ("Pine Credit", 29)):
        debt = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == creditor)
        client.patch(f"/api/finance/debts/{debt['id']}", json={"interest_rate": rate})

    plan = client.get(
        "/api/finance/debts/payoff-plan", params={"monthly_payment": 300}
    ).json()
    saved = plan["snowball"]["total_interest"] - plan["avalanche"]["total_interest"]
    assert 0 < saved < 100
    assert plan["recommendation"]["strategy"] == "snowball"
    assert plan["recommendation"]["target_creditor"] == "Birch Loans"
    assert "not worth giving up the first win" in plan["recommendation"]["why"]


def test_a_payment_that_never_outruns_interest_reports_no_payoff_date(client):
    p = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Pine Credit")
    client.patch(f"/api/finance/debts/{p['id']}", json={"interest_rate": 99})

    plan = client.get(
        "/api/finance/debts/payoff-plan", params={"monthly_payment": 1}
    ).json()
    assert plan["snowball"]["months_to_debt_free"] is None
    assert plan["snowball"]["debt_free_date"] is None


def test_payoff_plan_with_no_debt_says_so(client):
    for debt in client.get("/api/finance/debts").json():
        client.post(f"/api/finance/debts/{debt['id']}/paid")

    plan = client.get("/api/finance/debts/payoff-plan").json()
    assert plan["total_debt"] == 0
    assert plan["debt_count"] == 0
    assert plan["recommendation"]["target_debt_id"] is None
    assert "No debt" in plan["recommendation"]["headline"]


def test_payoff_plan_path_is_not_read_as_a_debt_id(client):
    assert client.get("/api/finance/debts/payoff-plan").status_code == 200
    assert client.get("/api/finance/debts/overdue").status_code == 200


# --- overdue ----------------------------------------------------------------


def test_overdue_debt_surfaces_on_the_deck(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/finance/debts/{bird['id']}", json={"due_date": past})

    overdue = client.get("/api/finance/debts/overdue").json()
    assert [d["creditor"] for d in overdue] == ["Birch Loans"]
    assert overdue[0]["days_overdue"] == 10

    alerts = client.get("/api/command/brief").json()["alerts"]
    assert any(a["source"] == "finance" and "Birch Loans" in a["title"] for a in alerts)


def test_paying_an_overdue_debt_clears_it_from_overdue(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    past = (date.today() - timedelta(days=10)).isoformat()
    client.patch(f"/api/finance/debts/{bird['id']}", json={"due_date": past})
    client.post(f"/api/finance/debts/{bird['id']}/paid")

    assert client.get("/api/finance/debts/overdue").json() == []


# --- investments ------------------------------------------------------------


def test_investment_account_crud_and_gain(client):
    created = client.post(
        "/api/finance/investments",
        json={"name": "Fidelity", "type": "brokerage", "balance": 1200,
              "contributions_to_date": 1000},
    ).json()
    assert created["gain"] == 200.0

    updated = client.patch(
        f"/api/finance/investments/{created['id']}", json={"balance": 900}
    ).json()
    assert updated["gain"] == -100.0

    assert client.delete(f"/api/finance/investments/{created['id']}").status_code == 200
    assert client.get("/api/finance/investments").json() == []


def test_investment_goal_projects_months_and_on_track(client):
    goal = client.post(
        "/api/finance/goals",
        json={"name": "Emergency fund", "target_amount": 3000,
              "monthly_contribution": 500, "current_value": 500,
              "target_date": (date.today() + timedelta(days=365)).isoformat()},
    ).json()
    # $2,500 to go at $500/mo = 5 months, inside a 12-month target.
    assert goal["months_at_current_rate"] == 5
    assert goal["on_track"] is True
    assert goal["progress"] == pytest.approx(500 / 3000, rel=1e-3)


def test_a_goal_with_no_contribution_has_no_projection(client):
    goal = client.post(
        "/api/finance/goals",
        json={"name": "House", "target_amount": 20000, "monthly_contribution": 0},
    ).json()
    assert goal["months_at_current_rate"] is None
    assert goal["on_track"] is None


def test_unknown_investment_and_goal_are_404(client):
    assert client.patch("/api/finance/investments/999", json={"balance": 1}).status_code == 404
    assert client.delete("/api/finance/goals/999").status_code == 404


# --- cash flow --------------------------------------------------------------


def test_monthly_summary_separates_debt_payments_from_expenses(client):
    month = date.today().strftime("%Y-%m")
    for payload in (
        {"amount": 3000, "kind": "income", "category": "AV gig"},
        {"amount": 400, "kind": "expense", "category": "food"},
        {"amount": 350, "kind": "debt_payment", "category": "Birch Loans"},
    ):
        assert client.post("/api/finance/cashflow/transactions", json=payload).status_code == 201

    summary = client.get("/api/finance/cashflow").json()
    assert summary["month"] == month
    assert summary["income"] == 3000.0
    assert summary["expenses"] == 400.0
    assert summary["debt_payments"] == 350.0
    assert summary["net"] == 2250.0
    assert summary["savings_rate"] == pytest.approx(2250 / 3000, rel=1e-3)


def test_budget_variance_flags_overspend(client):
    client.post("/api/finance/cashflow/budget", json={"name": "food", "monthly_budget": 300})
    client.post(
        "/api/finance/cashflow/transactions",
        json={"amount": 420, "kind": "expense", "category": "food"},
    )

    food = client.get("/api/finance/cashflow/budget").json()[0]
    assert food["spent"] == 420.0
    assert food["variance"] == -120.0
    assert food["remaining"] == 0
    assert food["over_budget"] is True


def test_spend_outside_any_budget_is_reported_not_hidden(client):
    client.post("/api/finance/cashflow/budget", json={"name": "food", "monthly_budget": 300})
    client.post(
        "/api/finance/cashflow/transactions",
        json={"amount": 90, "kind": "expense", "category": "studio time"},
    )
    assert client.get("/api/finance/cashflow").json()["uncategorized_spend"] == 90.0


def test_duplicate_budget_category_is_409(client):
    client.post("/api/finance/cashflow/budget", json={"name": "food", "monthly_budget": 300})
    assert client.post(
        "/api/finance/cashflow/budget", json={"name": "food", "monthly_budget": 400}
    ).status_code == 409


def test_a_last_month_transaction_stays_out_of_this_month(client):
    last_month = (date.today().replace(day=1) - timedelta(days=1)).isoformat()
    client.post(
        "/api/finance/cashflow/transactions",
        json={"amount": 999, "kind": "expense", "category": "food", "occurred_on": last_month},
    )
    assert client.get("/api/finance/cashflow").json()["expenses"] == 0


def test_negative_amounts_are_rejected(client):
    assert client.post(
        "/api/finance/cashflow/transactions", json={"amount": -50, "kind": "expense"}
    ).status_code == 422


# --- net worth --------------------------------------------------------------


def test_net_worth_is_assets_minus_debts_and_ignores_ventures(client):
    client.post(
        "/api/finance/investments",
        json={"name": "Cash", "type": "cash", "balance": 2000},
    )
    client.post(
        "/api/finance/investments",
        json={"name": "Coinbase", "type": "crypto", "balance": 500},
    )
    # A venture earning money must not inflate personal net worth.
    ventures = client.get("/api/ventures").json()
    if ventures:
        client.post(
            f"/api/ventures/{ventures[0]['id']}/events",
            json={"kind": "revenue", "amount": 5000, "label": "big sale"},
        )

    nw = client.get("/api/finance/net-worth").json()
    assert nw["assets"] == 2500.0
    assert nw["liabilities"] == 7850.0
    assert nw["net_worth"] == -5350.0
    assert nw["by_account_type"] == {"cash": 2000.0, "crypto": 500.0}


# --- notes ------------------------------------------------------------------


def test_note_records_an_opened_account(client):
    note = client.post(
        "/api/finance/notes",
        json={"title": "Discover It secured", "category": "card",
              "institution": "Discover", "last4": "4821",
              "opened_on": "2026-08-01", "body": "$200 deposit, gas only, autopay on"},
    ).json()
    assert note["category"] == "card"
    assert note["last4"] == "4821"
    assert client.get(f"/api/finance/notes/{note['id']}").json()["body"].startswith("$200 deposit")


def test_notes_cannot_hold_more_than_the_last_four_digits(client):
    assert client.post(
        "/api/finance/notes", json={"title": "Card", "last4": "4111111111111111"}
    ).status_code == 422
    assert client.post(
        "/api/finance/notes", json={"title": "Card", "last4": "abcd"}
    ).status_code == 422


def test_pinned_notes_sort_first_and_reach_the_overview(client):
    client.post("/api/finance/notes", json={"title": "Old savings account"})
    pinned = client.post(
        "/api/finance/notes", json={"title": "Rent comes out of this one", "pinned": True}
    ).json()

    assert client.get("/api/finance/notes").json()[0]["id"] == pinned["id"]
    assert [n["id"] for n in client.get("/api/finance").json()["pinned_notes"]] == [pinned["id"]]


def test_notes_search_and_archive(client):
    client.post(
        "/api/finance/notes",
        json={"title": "Chime spending", "institution": "Chime", "category": "account"},
    )
    other = client.post("/api/finance/notes", json={"title": "Car loan"}).json()

    assert len(client.get("/api/finance/notes", params={"q": "chime"}).json()) == 1
    assert len(client.get("/api/finance/notes", params={"category": "account"}).json()) == 1

    client.patch(f"/api/finance/notes/{other['id']}", json={"archived": True})
    assert [n["title"] for n in client.get("/api/finance/notes").json()] == ["Chime spending"]
    assert len(
        client.get("/api/finance/notes", params={"include_archived": True}).json()
    ) == 2


def test_note_delete_and_404s(client):
    note = client.post("/api/finance/notes", json={"title": "Temp"}).json()
    assert client.delete(f"/api/finance/notes/{note['id']}").status_code == 200
    assert client.get(f"/api/finance/notes/{note['id']}").status_code == 404
    assert client.patch("/api/finance/notes/999", json={"title": "x"}).status_code == 404


# --- vault sync -------------------------------------------------------------


def test_import_reads_the_three_debts_from_the_tracker(client, tracker):
    parsed = debt_vault_sync.parse_debts(tracker.read_text())
    assert [(d["creditor"], d["amount"]) for d in parsed] == [
        ("Cedar Bank", 5000.0), ("Birch Loans", 350.0), ("Pine Credit", 2500.0)
    ]
    # The "What I'm Owed" filler row must not be read as a debt.
    assert all(d["creditor"] != "—" for d in parsed)


def test_import_is_a_no_op_once_the_table_has_rows(client, tracker):
    body = client.post("/api/finance/vault/import").json()
    assert body["debts_written"] == 0
    assert len(client.get("/api/finance/debts").json()) == 3


def test_import_restores_an_empty_table_from_the_vault(client, tracker):
    from app.deps import get_db

    conn = get_db()
    conn.execute("DELETE FROM debts")
    conn.commit()

    assert debt_vault_sync.import_debts(conn) == 3
    assert sum(d["balance"] for d in client.get("/api/finance/debts").json()) == 7850.0


def test_paying_a_debt_writes_the_tracker_back(client, tracker):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    client.post(f"/api/finance/debts/{bird['id']}/paid")

    md = tracker.read_text()
    assert "**Total owed: $7,500.00**" in md
    assert "✅ Paid" in md
    assert f"**Updated:** {date.today().isoformat()}" in md


def test_the_sync_leaves_the_hand_curated_sections_alone(client, tracker):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    client.post(f"/api/finance/debts/{bird['id']}/payments", json={"amount": 50})

    md = tracker.read_text()
    assert "## 📥 What I'm Owed" in md
    assert "## 📝 Log" in md
    assert "- **2026-08-02** — Added: Cedar Bank $5,000 · Birch Loans $350 · Pine Credit $2,500" in md
    assert "| Debtor | Amount | Date Added | Status | Notes |" in md
    assert "| Birch Loans | $300.00 |" in md


def test_a_missing_section_marker_leaves_the_file_untouched(client, tracker):
    tracker.write_text("# Debt Tracker\n\nNothing structured in here.\n")
    before = tracker.read_text()

    body = client.post("/api/finance/vault/sync").json()
    assert body["ok"] is False
    assert "not found" in body["detail"]
    assert tracker.read_text() == before


def test_a_missing_tracker_file_is_reported_not_raised(client, vault):
    body = client.post("/api/finance/vault/sync").json()
    assert body["ok"] is False
    assert "not found" in body["detail"]


def test_a_pipe_in_a_creditor_name_cannot_break_the_table(client, tracker):
    client.post("/api/finance/debts", json={"creditor": "Ray | Mike", "amount": 100})
    assert "| Ray \\| Mike |" in tracker.read_text()


# --- log --------------------------------------------------------------------


def test_every_write_lands_in_the_finance_log(client):
    bird = next(d for d in client.get("/api/finance/debts").json() if d["creditor"] == "Birch Loans")
    client.post(f"/api/finance/debts/{bird['id']}/payments", json={"amount": 50})
    client.post("/api/finance/investments", json={"name": "Fidelity", "balance": 100})
    client.post(
        "/api/finance/cashflow/transactions", json={"amount": 20, "kind": "expense"}
    )
    client.post("/api/finance/notes", json={"title": "Discover It"})

    log = client.get("/api/finance/log").json()
    verbs = [entry["verb"] for entry in log]
    assert "pay_debt" in verbs
    assert "add_account" in verbs
    assert "expense" in verbs
    assert "add_note" in verbs
    assert all(entry["module"] == "finance" for entry in log)

    payment = next(e for e in log if e["verb"] == "pay_debt")
    assert payment["subject"] == "Birch Loans"
    assert payment["meta"]["balance"] == 300.0


def test_the_log_does_not_copy_note_bodies(client):
    client.post(
        "/api/finance/notes",
        json={"title": "Discover It", "body": "something he may not want duplicated"},
    )
    entry = next(e for e in client.get("/api/finance/log").json() if e["verb"] == "add_note")
    assert "something he may not want" not in str(entry["meta"])


# --- command deck -----------------------------------------------------------


def test_the_deck_carries_a_debt_tile_and_the_one_next_move(client):
    brief = client.get("/api/command/brief").json()

    tile = next(m for m in brief["metrics"] if m["key"] == "debt_total")
    assert tile["value"] == 7850.0
    assert tile["health"] == "yellow"
    assert "Birch Loans" in tile["hint"]

    debt_items = [i for i in brief["now_next"] if i["kind"] == "debt"]
    assert len(debt_items) == 1
    assert "Birch Loans" in debt_items[0]["title"]


def test_clearing_every_debt_turns_the_tile_green(client):
    for debt in client.get("/api/finance/debts").json():
        client.post(f"/api/finance/debts/{debt['id']}/paid")

    brief = client.get("/api/command/brief").json()
    tile = next(m for m in brief["metrics"] if m["key"] == "debt_total")
    assert tile["value"] == 0
    assert tile["health"] == "green"
    assert [i for i in brief["now_next"] if i["kind"] == "debt"] == []


# --- guardrails -------------------------------------------------------------


def test_the_module_makes_no_outbound_calls(client, monkeypatch):
    """v1 is local-only: no broker, no market data, no plaid."""
    import socket

    def blocked(*args, **kwargs):
        raise AssertionError("finance made a network call")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)

    assert client.get("/api/finance").status_code == 200
    assert client.get(
        "/api/finance/debts/payoff-plan", params={"monthly_payment": 500}
    ).status_code == 200


def test_service_layer_is_reachable_without_the_api(client):
    """The agent reads through the service, so it must work on a bare handle."""
    from app.deps import get_db

    plan = finance_service.payoff_plan(get_db(), monthly_payment=350)
    assert plan.recommendation.target_creditor == "Birch Loans"
    assert plan.total_debt == 7850.0
