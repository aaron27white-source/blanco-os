"""End-to-end coverage of every module the UI is designed against."""

from __future__ import annotations

import io
import json
import threading
from datetime import date
from types import SimpleNamespace


# --- meta -------------------------------------------------------------------


def test_health_and_schema_version(client):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["schema_version"] == 23  # + job search


def test_system_info_lists_every_module(client):
    body = client.get("/api/system").json()
    assert body["name"] == "Blanco OS"
    ids = {m["id"] for m in body["modules"]}
    assert ids == {
        "command", "tasks", "money", "finance", "credit", "certs", "journal", "email",
        "timer", "notepad", "agents", "systems", "knowledge", "bridge", "chat",
    }


def test_openapi_is_generated(client):
    spec = client.get("/openapi.json").json()
    assert "/api/command/brief" in spec["paths"]
    assert "DailyBrief" in spec["components"]["schemas"]


# --- notepad ----------------------------------------------------------------


def test_notepad_seeds_one_note(client):
    body = client.get("/api/notepad").json()
    assert body["count"] == 1
    assert body["notes"][0]["title"] == "Scratch"
    # The rail draws from `preview`, not from the full body.
    assert body["notes"][0]["preview"].startswith("Anything half-formed")


def test_notepad_create_edit_delete(client):
    note = client.post("/api/notepad", json={"title": "Freight", "body": "call ITADs"}).json()
    assert note["chars"] == len("call ITADs")

    edited = client.patch(f"/api/notepad/{note['id']}", json={"body": "# heading\ncall ITADs back"})
    assert edited.status_code == 200
    # Markdown furniture is stripped out of the preview line.
    assert edited.json()["preview"] == "heading"
    assert edited.json()["title"] == "Freight"

    assert client.delete(f"/api/notepad/{note['id']}").status_code == 204
    assert client.get(f"/api/notepad/{note['id']}").status_code == 404
    assert client.delete(f"/api/notepad/{note['id']}").status_code == 404


def test_notepad_pinned_sort_first(client):
    older = client.post("/api/notepad", json={"title": "older"}).json()
    client.post("/api/notepad", json={"title": "newer"})
    client.patch(f"/api/notepad/{older['id']}", json={"pinned": True})

    titles = [n["title"] for n in client.get("/api/notepad").json()["notes"]]
    assert titles[0] == "older"


def test_notepad_rejects_a_stale_write(client):
    """The deck panel and the detached window must not overwrite each other."""
    note = client.post("/api/notepad", json={"body": "first"}).json()
    fresh = client.patch(f"/api/notepad/{note['id']}",
                         json={"body": "window A", "base_updated_at": note["updated_at"]}).json()

    stale = client.patch(f"/api/notepad/{note['id']}",
                         json={"body": "window B", "base_updated_at": note["updated_at"]})
    assert stale.status_code == 409
    assert client.get(f"/api/notepad/{note['id']}").json()["body"] == "window A"

    # No base timestamp = a deliberate overwrite, which still goes through.
    forced = client.patch(f"/api/notepad/{note['id']}", json={"body": "window B"})
    assert forced.status_code == 200
    assert forced.json()["body"] == "window B"
    assert fresh["body"] == "window A"


def test_notepad_clamps_an_oversized_paste(client):
    note = client.post("/api/notepad", json={"body": "x" * 300_000}).json()
    assert note["chars"] == 200_000


# --- tasks ------------------------------------------------------------------


def test_board_counts_and_ordering(client):
    board = client.get("/api/tasks").json()
    assert board["counts"]["total"] == 2
    assert board["counts"]["done"] == 1
    assert board["counts"]["overdue"] == 1  # bt-1 is due 2020-01-01
    assert board["tasks"][0]["id"] == "bt-1"  # open + overdue sorts first


def test_task_crud_round_trip(client, vault):
    created = client.post(
        "/api/tasks",
        json={"title": "Call Houston ITAD", "priority": "high", "due_date": "2026-08-01"},
    ).json()
    assert created["status"] == "todo"

    updated = client.patch(f"/api/tasks/{created['id']}", json={"status": "in_progress"}).json()
    assert updated["status"] == "in_progress"
    assert updated["title"] == "Call Houston ITAD"

    # The write really landed in the vault file, in the schema the HTML UI expects.
    raw = json.loads((vault / "02-areas" / "todo-data.json").read_text())
    stored = next(t for t in raw["boardTasks"] if t["id"] == created["id"])
    assert stored["dueDate"] == "2026-08-01"
    assert stored["status"] == "in_progress"

    assert client.delete(f"/api/tasks/{created['id']}").json()["ok"] is True
    assert client.delete(f"/api/tasks/{created['id']}").status_code == 404


def test_dragging_a_task_onto_the_calendar_sets_and_clears_its_due_date(client, vault):
    """The month grid moves a task by PATCHing due_date, and unschedules it by
    sending an explicit null — the one field a null is allowed to clear."""
    created = client.post("/api/tasks", json={"title": "Pick up the switch"}).json()
    assert created["due_date"] is None

    moved = client.patch(f"/api/tasks/{created['id']}", json={"due_date": "2026-08-14"}).json()
    assert moved["due_date"] == "2026-08-14"
    assert moved["status"] == "todo", "a date change must not touch anything else"

    cleared = client.patch(f"/api/tasks/{created['id']}", json={"due_date": None}).json()
    assert cleared["due_date"] is None
    assert cleared["title"] == "Pick up the switch"

    raw = json.loads((vault / "02-areas" / "todo-data.json").read_text())
    stored = next(t for t in raw["boardTasks"] if t["id"] == created["id"])
    assert stored["dueDate"] == ""


def test_a_null_cannot_clear_anything_but_the_due_date(client):
    created = client.post("/api/tasks", json={"title": "Keep my notes", "notes": "vendor: ITAD"}).json()
    updated = client.patch(f"/api/tasks/{created['id']}", json={"notes": None, "status": None}).json()
    assert updated["notes"] == "vendor: ITAD"
    assert updated["status"] == "todo"


def test_subtasks_round_trip_into_the_vault(client, vault):
    """A big task carries its steps. "Set up Fiverr profile" is one card with
    a checklist inside it, not ten cards on the board."""
    task = client.post("/api/tasks", json={"title": "Set up Fiverr profile"}).json()
    assert task["subtasks"] == []

    with_step = client.post(
        f"/api/tasks/{task['id']}/subtasks", json={"title": "Write the bio"}
    ).json()
    client.post(f"/api/tasks/{task['id']}/subtasks", json={"title": "Shoot the cover image"})
    listed = client.get("/api/tasks").json()
    stored_task = next(t for t in listed["tasks"] if t["id"] == task["id"])
    assert [s["title"] for s in stored_task["subtasks"]] == ["Write the bio", "Shoot the cover image"]
    assert all(s["done"] is False for s in stored_task["subtasks"])

    # Ids are the server's, and they live in the vault file the HTML UI reads.
    step_id = with_step["subtasks"][0]["id"]
    assert step_id.startswith("st-")
    raw = json.loads((vault / "02-areas" / "todo-data.json").read_text())
    stored = next(t for t in raw["boardTasks"] if t["id"] == task["id"])
    assert stored["subtasks"][0]["title"] == "Write the bio"

    # Checking the first step starts the task; it does not finish it.
    ticked = client.patch(
        f"/api/tasks/{task['id']}/subtasks/{step_id}", json={"done": True}
    ).json()
    assert ticked["subtasks"][0]["done"] is True
    assert ticked["status"] == "in_progress"

    renamed = client.patch(
        f"/api/tasks/{task['id']}/subtasks/{step_id}", json={"title": "Write the seller bio"}
    ).json()
    assert renamed["subtasks"][0]["title"] == "Write the seller bio"
    assert renamed["subtasks"][0]["done"] is True, "a rename must not reset the checkbox"

    dropped = client.delete(f"/api/tasks/{task['id']}/subtasks/{step_id}").json()
    assert [s["title"] for s in dropped["subtasks"]] == ["Shoot the cover image"]
    assert client.delete(f"/api/tasks/{task['id']}/subtasks/{step_id}").status_code == 404


def test_finishing_every_step_never_closes_the_task_by_itself(client):
    task = client.post("/api/tasks", json={"title": "Ship the deck"}).json()
    step = client.post(f"/api/tasks/{task['id']}/subtasks", json={"title": "only step"}).json()
    done = client.patch(
        f"/api/tasks/{task['id']}/subtasks/{step['subtasks'][0]['id']}", json={"done": True}
    ).json()
    assert done["status"] == "in_progress", "closing a task stays Blanco's call"


def test_a_subtask_id_cannot_reach_into_another_task(client):
    """The id is resolved inside its parent, so a stray one is a 404 rather
    than an edit to somebody else's checklist."""
    mine = client.post("/api/tasks", json={"title": "Mine"}).json()
    theirs = client.post("/api/tasks", json={"title": "Theirs"}).json()
    step = client.post(f"/api/tasks/{theirs['id']}/subtasks", json={"title": "their step"}).json()
    step_id = step["subtasks"][0]["id"]

    assert client.patch(
        f"/api/tasks/{mine['id']}/subtasks/{step_id}", json={"done": True}
    ).status_code == 404
    assert client.delete(f"/api/tasks/{mine['id']}/subtasks/{step_id}").status_code == 404
    still_there = client.get("/api/tasks").json()
    theirs_now = next(t for t in still_there["tasks"] if t["id"] == theirs["id"])
    assert theirs_now["subtasks"][0]["done"] is False


def test_subtasks_survive_an_edit_to_the_task_itself(client):
    task = client.post("/api/tasks", json={"title": "Keep my steps"}).json()
    client.post(f"/api/tasks/{task['id']}/subtasks", json={"title": "step one"})
    moved = client.patch(f"/api/tasks/{task['id']}", json={"status": "blocked"}).json()
    assert [s["title"] for s in moved["subtasks"]] == ["step one"]


def test_a_blank_subtask_is_refused(client):
    task = client.post("/api/tasks", json={"title": "No empty steps"}).json()
    assert client.post(f"/api/tasks/{task['id']}/subtasks", json={"title": "   "}).status_code == 422
    assert client.post(f"/api/tasks/{task['id']}/subtasks", json={"title": ""}).status_code == 422
    assert client.post("/api/tasks/bt-nope/subtasks", json={"title": "x"}).status_code == 404


def test_dragging_an_event_to_another_day_moves_it_in_the_markdown(client, vault):
    created = client.post("/api/events", json={
        "title": "Flea market run", "date": "2026-08-05", "time": "07:00"}).json()

    moved = client.patch(f"/api/events/{created['id']}", json={"date": "2026-08-12"}).json()
    assert moved["date"] == "2026-08-12"
    assert moved["time"] == "07:00", "moving a day must not drop the time"

    after = _todo_md(vault).read_text()
    assert "| 2026-08-12 | 07:00 | Flea market run | 📅 |" in after
    assert "2026-08-05" not in after

    assert client.patch("/api/events/ev-nope", json={"date": "2026-08-12"}).status_code == 404


def test_timeline_only_returns_future_events(client):
    events = client.get("/api/tasks/timeline").json()
    assert [e["id"] for e in events] == ["ev-1"]


def test_event_and_note_creation(client):
    event = client.post("/api/events", json={"title": "Flea market run", "date": "2099-02-02"}).json()
    assert event["id"].startswith("ev-")
    note = client.post("/api/notes", json={"text": "Traders Village opens 7am"}).json()
    assert note["date"] == date.today().isoformat()


# --- certs ------------------------------------------------------------------


def test_cert_roadmap_is_parsed_from_markdown(client):
    track = client.get("/api/certs").json()
    slugs = [c["slug"] for c in track["certs"]]
    assert "comptia-a-plus" in slugs
    assert "google-it-automation-with-python" in slugs
    assert track["counts"]["total"] == 3

    google = next(c for c in track["certs"] if c["slug"] == "google-it-automation-with-python")
    assert google["status"] == "in_progress"  # from the 🟡 glyph
    assert google["phase"] == "Phase 1a — Python Foundation"
    assert google["cost"] == "$0"


def test_cert_override_beats_markdown(client):
    updated = client.patch("/api/certs/comptia-a-plus", json={"status": "passed"}).json()
    assert updated["status"] == "passed"
    assert updated["percent"] == 100
    assert updated["overridden"] is True

    track = client.get("/api/certs").json()
    assert track["counts"]["passed"] == 1
    assert client.patch("/api/certs/nope", json={"status": "passed"}).status_code == 404


# --- ai stack spend ---------------------------------------------------------


def test_ai_stack_seeds_real_providers_with_no_invented_costs(client):
    """Names come from configured API keys; the prices are Blanco's to enter."""
    body = client.get("/api/money/ai-stack").json()
    names = {s["name"] for s in body["services"]}
    assert {"Anthropic API", "OpenAI API", "OpenRouter", "DeepSeek API"} <= names
    assert all(s["cost"] == 0 for s in body["services"])
    assert body["monthly_total"] == 0


def test_ai_stack_reports_what_it_does_not_know(client):
    """An unpriced service is an unknown, not a free one.

    A total that silently omits three providers looks authoritative and is
    wrong, so the gap is named rather than absorbed.
    """
    body = client.get("/api/money/ai-stack").json()
    assert "Anthropic API" in body["unpriced"]

    services = {s["name"]: s["id"] for s in body["services"]}
    client.patch(f"/api/money/ai-stack/{services['Anthropic API']}", json={"cost": 40})

    body = client.get("/api/money/ai-stack").json()
    assert "Anthropic API" not in body["unpriced"]
    assert body["monthly_total"] == 40


def test_ai_stack_normalises_annual_plans_to_a_month(client):
    created = client.post("/api/money/ai-stack", json={
        "name": "Cursor Pro", "kind": "subscription", "billing": "annual", "cost": 240,
    }).json()
    assert created["monthly_cost"] == 20

    body = client.get("/api/money/ai-stack").json()
    assert body["monthly_total"] == 20
    assert body["fixed_monthly"] == 20
    assert body["annual_total"] == 240


def test_ai_stack_separates_committed_from_metered(client):
    """The metered half is the part that moves, and the part that surprises."""
    client.post("/api/money/ai-stack", json={
        "name": "ChatGPT Plus", "kind": "subscription", "billing": "monthly", "cost": 20})
    services = {s["name"]: s["id"] for s in client.get("/api/money/ai-stack").json()["services"]}
    client.patch(f"/api/money/ai-stack/{services['OpenRouter']}", json={"cost": 12})

    body = client.get("/api/money/ai-stack").json()
    assert body["fixed_monthly"] == 20
    assert body["usage_monthly"] == 12
    assert body["monthly_total"] == 32


def test_ai_stack_free_tier_is_not_an_unknown(client):
    client.post("/api/money/ai-stack", json={
        "name": "Groq Free", "billing": "free", "cost": 0})
    body = client.get("/api/money/ai-stack").json()
    assert "Groq Free" not in body["unpriced"]


def test_ai_stack_recording_a_cost_builds_the_trend(client):
    services = {s["name"]: s["id"] for s in client.get("/api/money/ai-stack").json()["services"]}
    client.patch(f"/api/money/ai-stack/{services['OpenAI API']}", json={"cost": 18})

    history = client.get("/api/money/ai-stack").json()["history"]
    assert len(history) == 1
    assert history[0]["amount"] == 18
    assert history[0]["period"] == date.today().strftime("%Y-%m")


def test_ai_stack_refuses_duplicate_names(client):
    """Adding the same service twice would double the total."""
    assert client.post("/api/money/ai-stack", json={"name": "OpenRouter"}).status_code == 409


def test_ai_stack_inactive_service_leaves_the_total(client):
    services = {s["name"]: s["id"] for s in client.get("/api/money/ai-stack").json()["services"]}
    client.patch(f"/api/money/ai-stack/{services['DeepSeek API']}", json={"cost": 9})
    assert client.get("/api/money/ai-stack").json()["monthly_total"] == 9

    client.patch(f"/api/money/ai-stack/{services['DeepSeek API']}", json={"is_active": False})
    assert client.get("/api/money/ai-stack").json()["monthly_total"] == 0


def test_ai_stack_not_found(client):
    assert client.patch("/api/money/ai-stack/999", json={"cost": 1}).status_code == 404
    assert client.delete("/api/money/ai-stack/999").status_code == 404


# --- credit -----------------------------------------------------------------


def test_credit_seeds_only_decided_removals(client):
    """The seed follows client_decisions.json, not the handoff's prose.

    SELF-EMPLOYED, the TransUnion JANE DOE alias, and the phone number all
    carry an audit_reason arguing for removal but were decided "keep". The
    original handoff listed SELF-EMPLOYED as a Phase 1 dispute anyway. Blanco's
    decision is the contract, so none of the three may appear here.
    """
    disputes = client.get("/api/credit/disputes").json()
    items = {d["item"] for d in disputes}

    assert "JANE DOA" in items
    assert "ACME MOVERS LLC" in items
    assert not any("SELF-EMPLOYED" in i for i in items)
    assert not any(i == "JANE DOE" for i in items)
    assert not any("555-0100" in i for i in items)


def test_credit_seeds_the_charge_off_the_handoff_missed(client):
    """The one derogatory item on the whole file has to be tracked."""
    disputes = client.get("/api/credit/disputes").json()
    charge_off = [d for d in disputes if d["category"] == "tradeline"]
    assert len(charge_off) == 1
    assert charge_off[0]["bureau"] == "TransUnion"
    assert "DEMO LENDER" in charge_off[0]["item"]


def test_credit_dispute_bureaus_match_the_source_data(client):
    """The handoff put two items on the wrong bureaus."""
    disputes = client.get("/api/credit/disputes").json()
    by_item: dict[str, set[str]] = {}
    for d in disputes:
        by_item.setdefault(d["item"].split(",")[0].strip(), set()).add(d["bureau"])

    # Handoff said Experian + TransUnion; only Experian reports it.
    assert by_item["200 SAMPLE AVE APT 1"] == {"Experian"}
    # Handoff said Equifax only; it is on both, under two spellings.
    assert by_item["ACME MOVERS LLC"] == {"Equifax"}
    assert by_item["ACME MOVERS"] == {"TransUnion"}


def test_credit_ships_no_invented_scores_or_accounts(client):
    """Same rule migration 003 set: nothing Blanco did not supply.

    None of the three bureau reports carries a score, and he has no open
    tradelines. Seeding either would be inventing his financial position.
    """
    assert client.get("/api/credit/scores").json() == []
    assert client.get("/api/credit/accounts").json() == []


def test_sending_a_dispute_starts_the_thirty_day_clock(client):
    dispute = client.get("/api/credit/disputes").json()[0]
    assert dispute["response_due"] is None
    assert dispute["days_remaining"] is None

    sent = client.patch(
        f"/api/credit/disputes/{dispute['id']}", json={"sent_on": "2026-07-01"}
    ).json()
    assert sent["response_due"] == "2026-07-31"  # FCRA gives the bureau 30 days
    assert sent["days_remaining"] == (
        date(2026, 7, 31) - date.today()
    ).days


def test_dispute_past_its_deadline_is_reported_overdue(client):
    dispute = client.get("/api/credit/disputes").json()[0]
    client.patch(f"/api/credit/disputes/{dispute['id']}", json={"sent_on": "2020-01-01"})

    overdue = client.get("/api/credit/disputes/overdue").json()
    assert [d["id"] for d in overdue] == [dispute["id"]]
    assert overdue[0]["days_remaining"] < 0


def test_resolving_a_dispute_stops_its_clock(client):
    dispute = client.get("/api/credit/disputes").json()[0]
    client.patch(f"/api/credit/disputes/{dispute['id']}", json={"sent_on": "2020-01-01"})

    resolved = client.patch(
        f"/api/credit/disputes/{dispute['id']}", json={"outcome": "removed"}
    ).json()
    assert resolved["resolved_on"] == date.today().isoformat()
    assert resolved["days_remaining"] is None
    assert client.get("/api/credit/disputes/overdue").json() == []


def test_plan_starts_at_week0_because_chexsystems_gates_everything(client):
    """Week 0 is not in the playbook — it is here because the bank block is real.

    The playbook opens a business bank account in Week 1 and Chime in Week 2.
    Both pull ChexSystems, where an unpaid DDA closure is reporting.
    """
    steps = client.get("/api/credit/plan/next").json()
    assert {s["phase"] for s in steps} == {"week0"}
    assert any("ChexSystems" in s["title"] for s in steps)
    assert any(s["est_cost"] == 100 for s in steps)


def test_plan_advances_a_phase_at_a_time_not_a_step(client):
    """Week 1 runs disputes, cards and the business track concurrently."""
    for step in client.get("/api/credit/plan/next").json():
        assert client.post(f"/api/credit/plan/{step['id']}/done").json()["done"] is True

    nxt = client.get("/api/credit/plan/next").json()
    assert {s["phase"] for s in nxt} == {"week1"}
    assert {s["track"] for s in nxt} == {"repair", "personal", "business"}


def test_completing_a_step_is_reversible(client):
    step = client.get("/api/credit/plan/next").json()[0]
    client.post(f"/api/credit/plan/{step['id']}/done")
    reopened = client.post(f"/api/credit/plan/{step['id']}/reopen").json()
    assert reopened["done"] is False
    assert reopened["done_on"] is None


def test_utilization_ignores_accounts_that_report_no_limit(client):
    """Installment loans and rent tradelines have no limit to divide by."""
    client.post("/api/credit/accounts", json={
        "account_name": "Discover it Secured", "account_type": "secured_card",
        "credit_limit": 200, "balance": 20, "deposit": 200,
    })
    client.post("/api/credit/accounts", json={
        "account_name": "Self Credit Builder", "account_type": "installment",
        "balance": 150, "monthly_cost": 25,
    })

    body = client.get("/api/credit").json()
    assert body["total_limit"] == 200
    assert body["total_balance"] == 20  # the installment balance is excluded
    assert body["utilization"] == 0.1
    assert body["monthly_cost"] == 25
    assert body["deposits_held"] == 200

    installment = [a for a in body["accounts"] if a["account_type"] == "installment"][0]
    assert installment["utilization"] == 0.0, "no limit means no utilisation, not 100%"


def test_overview_counts_fintech_synthetics_against_the_cap(client):
    """The playbook caps Bucket B at two; past that the file reads manufactured."""
    for name in ("Kikoff", "Grow Credit", "Ava"):
        client.post("/api/credit/accounts", json={
            "account_name": name, "account_type": "unsecured_card", "bucket": "B",
        })
    client.post("/api/credit/accounts", json={
        "account_name": "Discover it Secured", "account_type": "secured_card", "bucket": "A",
    })

    assert client.get("/api/credit").json()["bucket_b_count"] == 3


def test_latest_scores_returns_one_row_per_bureau_and_type(client):
    for day, score in (("2026-06-01", 610), ("2026-07-01", 640)):
        client.post("/api/credit/scores", json={
            "bureau": "Equifax", "score_type": "fico8", "score": score, "recorded_on": day,
        })
    client.post("/api/credit/scores", json={
        "bureau": "TransUnion", "score_type": "fico8", "score": 590,
    })

    latest = client.get("/api/credit/scores/latest").json()
    assert len(latest) == 2
    equifax = [s for s in latest if s["bureau"] == "Equifax"][0]
    assert equifax["score"] == 640, "the newer reading wins"


def test_score_entry_keeps_observation_date_separate_from_write_time(client):
    """Entering last week's number must not date it today, or trends lie."""
    body = client.post("/api/credit/scores", json={
        "bureau": "Experian", "score": 605, "recorded_on": "2026-07-15",
    }).json()
    assert body["recorded_on"] == "2026-07-15"


def test_credit_account_not_found(client):
    assert client.patch("/api/credit/accounts/999", json={"balance": 1}).status_code == 404
    assert client.patch("/api/credit/disputes/999", json={"outcome": "removed"}).status_code == 404
    assert client.post("/api/credit/plan/999/done").status_code == 404


# --- money ------------------------------------------------------------------


def test_ventures_carry_identity_but_no_invented_numbers(client):
    """Names and paths are real; every judgement call ships unset."""
    ventures = client.get("/api/ventures").json()
    ids = {v["id"] for v in ventures}
    assert {
        "northwind", "biz", "import", "dispatch-bot", "trading", "studio", "career",
        "electronics-export",
    } <= ids

    for v in ventures:
        assert v["name"], "a venture must keep its real name"
        assert v["stage"] == "unset", f"{v['id']} shipped with an assumed stage"
        assert v["monthly_target"] == 0, f"{v['id']} shipped with an invented target"
        assert v["capital_in"] == 0
        assert v["next_action"] == "", f"{v['id']} shipped with an invented next action"
        assert v["thesis"] == ""

    gt = next(v for v in ventures if v["id"] == "import")
    assert gt["vault_path"] == "Your Business/projects/import-sourcing"


def test_money_overview_reports_no_target(client):
    m = client.get("/api/money").json()
    assert m["total_target"] == 0
    assert m["attainment"] == 0.0

    metric = next(x for x in client.get("/api/command/brief").json()["metrics"]
                  if x["key"] == "income_attainment")
    assert metric["target"] is None
    assert metric["health"] == "unknown"
    assert "No monthly targets set" in metric["hint"]


def test_revenue_event_updates_monthly_actual(client):
    client.post(
        "/api/ventures/northwind/events",
        json={"kind": "revenue", "amount": 420.50, "label": "Sold 2x OptiPlex"},
    )
    client.post(
        "/api/ventures/northwind/events",
        json={"kind": "expense", "amount": 120.00, "label": "Lot purchase"},
    )
    venture = client.get("/api/ventures/northwind").json()
    assert venture["monthly_actual"] == 300.50

    overview = client.get("/api/money").json()
    assert overview["revenue_mtd"] == 420.50
    assert overview["expenses_mtd"] == 120.00
    assert overview["net_mtd"] == 300.50


def test_venture_update_and_404s(client):
    updated = client.patch("/api/ventures/trading", json={"stage": "paused"}).json()
    assert updated["stage"] == "paused"
    assert client.get("/api/ventures/ghost").status_code == 404
    assert client.post("/api/ventures/ghost/events", json={"label": "x"}).status_code == 404


# --- journal ----------------------------------------------------------------


def test_journal_overview_scores_moods(client):
    body = client.get("/api/journal").json()
    assert body["entry_count"] == 2
    assert body["last_entry_date"] == "2026-07-13"
    assert body["longest_streak"] == 2  # Jul 12 and 13 are consecutive
    assert [p["score"] for p in body["series"]] == [4, 5]  # 😊 then 🔥


def test_journal_write_round_trip(client, vault):
    today = date.today().isoformat()
    entry = client.put(
        f"/api/journal/entries/{today}",
        json={"mood": "💪", "text": "Shipped the OS backend.", "tags": ["build"]},
    ).json()
    assert entry["mood"] == "💪"
    assert entry["created"] is not None

    stored = json.loads((vault / "05-journal" / "diary.json").read_text())
    assert stored["entries"][today]["text"] == "Shipped the OS backend."

    assert client.get("/api/journal").json()["current_streak"] == 1
    assert client.put("/api/journal/entries/07-2026", json={"mood": "🔥"}).status_code == 422


# --- agents & systems -------------------------------------------------------


def test_agent_roster_discovers_subagents_from_disk(client, workspace):
    roster = client.get("/api/agents").json()
    assert roster["probed"] is False  # probes disabled in tests
    ids = {a["id"] for a in roster["agents"]}
    # Fixed pieces plus the two sub-agent dirs the fixture created.
    # Namespaced by commander: the three rosters are separate systems, and a
    # bare "circuit" says nothing about who it answers to.
    assert {"openclaw:main", "openclaw:circuit", "openclaw:mic"} <= ids

    circuit = next(a for a in roster["agents"] if a["id"] == "openclaw:circuit")
    assert circuit["name"] == "Circuit"
    assert circuit["emoji"] == "\U0001F4BB"
    assert circuit["role"].startswith("Electronics business advisor")
    assert circuit["kind"] == "subagent"
    assert circuit["engine"] == "openclaw"

    assert client.get("/api/agents/openclaw:main").json()["kind"] == "commander"
    assert client.get("/api/agents/ghost").status_code == 404


def test_roster_has_no_phantom_agents(client):
    """Agents whose workspace no longer exists must not be invented, and a
    commander whose binary is absent must not appear at all."""
    ids = {a["id"] for a in client.get("/api/agents").json()["agents"]}
    assert not ({"scribe", "researcher", "openclaw:ghost"} & ids)


def test_systems_health_shape(client):
    body = client.get("/api/systems").json()
    assert body["overall"] in ("green", "yellow", "red", "unknown")
    assert any(s["id"] == "vault" for s in body["services"])
    assert all("last_checked" in s for s in body["services"])
    assert isinstance(body["disks"], list)


# --- knowledge --------------------------------------------------------------


def test_knowledge_overview_and_search(client):
    overview = client.get("/api/knowledge").json()
    assert overview["total_notes"] >= 2
    assert any(t["folder"] == "credit" for t in overview["reference_topics"])

    results = client.get("/api/knowledge/search", params={"q": "utilization"}).json()
    assert results["hit_count"] == 1
    assert results["hits"][0]["title"] == "Credit Scoring Basics"

    detail = client.get(
        "/api/knowledge/note",
        params={"path": "03-resources/reference/credit/2026-07-24_scoring.md"},
    ).json()
    assert "Utilization" in detail["content"]
    assert detail["note"]["tags"] == ["credit", "reference"]


def test_note_read_refuses_path_traversal(client):
    assert client.get("/api/knowledge/note", params={"path": "../../etc/passwd"}).status_code == 404


# --- bridge -----------------------------------------------------------------


def test_bridge_escalation_raises_an_alert(client):
    client.post(
        "/api/bridge",
        json={"kind": "escalation", "title": "Invoice 90 days overdue", "amount": 2500},
    )
    messages = client.get("/api/bridge", params={"unread_only": True}).json()
    assert len(messages) == 1
    assert messages[0]["amount"] == 2500

    alerts = client.get("/api/command/alerts").json()
    assert any(a["source"] == "bridge" and a["severity"] == "critical" for a in alerts)

    client.post(f"/api/bridge/{messages[0]['id']}/read")
    assert client.get("/api/bridge", params={"unread_only": True}).json() == []


# --- command deck -----------------------------------------------------------


def test_focus_falls_back_to_active_md_then_accepts_override(client):
    focus = client.get("/api/command/focus").json()
    # No seeded row any more: it reads the live workspace ACTIVE.md.
    assert focus["source"] == "active_md"
    assert "Proxmox" in focus["headline"]

    updated = client.put(
        "/api/command/focus",
        json={"headline": "Ship the OS dashboard", "detail": "21st.dev design pass", "horizon": "week"},
    ).json()
    assert updated["headline"] == "Ship the OS dashboard"
    assert updated["source"] == "os"


def test_daily_brief_fuses_every_module(client):
    brief = client.get("/api/command/brief").json()

    assert brief["day"] == date.today().isoformat()
    assert brief["greeting"].endswith("Blanco")
    assert {m["key"] for m in brief["metrics"]} == {
        "income_attainment", "ventures_earning", "debt_total", "open_tasks",
        "cert_progress", "journal_streak", "agents_online",
    }
    # The overdue seed task must surface as the first thing to do.
    assert brief["now_next"][0]["kind"] == "task"
    assert brief["now_next"][0]["urgency"] == "critical"
    assert brief["agents_total"] >= 5


def test_sweep_is_idempotent(client):
    first = client.post("/api/command/sweep").json()
    second = client.post("/api/command/sweep").json()
    assert len(first) == len(second)  # dedupe_key prevents duplicates
    assert any(a["source"] == "tasks" and "overdue" in a["title"] for a in second)


def test_alert_ack(client):
    client.post("/api/command/sweep")
    alert = client.get("/api/command/alerts").json()[0]
    acked = client.post(f"/api/command/alerts/{alert['id']}/ack").json()
    assert acked["acknowledged"] is True
    assert alert["id"] not in [a["id"] for a in client.get("/api/command/alerts").json()]
    assert client.post("/api/command/alerts/9999/ack").status_code == 404


def test_activity_log_records_writes(client):
    client.post("/api/tasks", json={"title": "Log me"})
    activity = client.get("/api/command/activity").json()
    assert activity[0]["module"] == "tasks"
    assert activity[0]["subject"] == "Log me"


# --- stream -----------------------------------------------------------------


def test_sse_stream_emits_a_pulse(client):
    with client.stream("GET", "/api/stream", params={"max_events": 1, "interval": 1}) as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        body = "".join(response.iter_text())
    assert "event: pulse" in body
    payload = json.loads(body.split("event: pulse\ndata: ")[1].split("\n")[0])
    assert set(payload) == {
        "open_alerts", "bridge_unread", "systems_health", "agents_online", "agents_total",
    }


def test_optional_services_are_excluded_from_health(client, monkeypatch):
    """A down optional service must not turn the whole OS red or raise an alert."""
    from app import schemas
    from app.services import systems_service

    now = "2026-07-27T00:00:00+00:00"
    svc = [
        schemas.ServiceStatus(id="vault", name="Vault", kind="file", status="up",
                              target="/vault", last_checked=now),
        schemas.ServiceStatus(id="todo-server", name="Todo server (legacy)", kind="http",
                              status="down", target=":8799", last_checked=now, optional=True),
    ]
    assert systems_service.health(svc).overall == "green"

    monkeypatch.setattr(systems_service, "services", lambda: svc)
    alerts = client.post("/api/command/sweep").json()
    # Disk alerts are also source="systems" and depend on the real machine, so
    # assert specifically that the optional service raised nothing.
    assert not any("Todo server" in a["title"] for a in alerts)


def test_delete_venture_event_recalculates(client):
    created = client.post(
        "/api/ventures/northwind/events",
        json={"kind": "revenue", "amount": 500, "label": "Mistyped sale"},
    ).json()
    assert client.get("/api/ventures/northwind").json()["monthly_actual"] == 500

    assert client.delete(f"/api/ventures/northwind/events/{created['id']}").json()["ok"] is True
    assert client.get("/api/ventures/northwind").json()["monthly_actual"] == 0
    assert client.delete(f"/api/ventures/northwind/events/{created['id']}").status_code == 404
    # Wrong venture must not be able to delete another's event.
    other = client.post("/api/ventures/trading/events", json={"amount": 10, "label": "x"}).json()
    assert client.delete(f"/api/ventures/northwind/events/{other['id']}").status_code == 404


def test_dashboard_is_served_and_api_still_wins(client):
    """The static mount at / must not shadow any API route."""
    page = client.get("/")
    assert page.status_code == 200
    assert "BLANCO OS" in page.text
    assert "text/html" in page.headers["content-type"]

    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/manifest.json").json()["short_name"] == "Blanco OS"


# --- focus timer -------------------------------------------------------------


def test_timer_start_countdown_and_stop(client):
    empty = client.get("/api/timer").json()
    assert empty["active"] is None
    assert empty["work_presets"] == [25, 50, 90]
    assert empty["break_presets"] == [5, 10, 15]

    started = client.post(
        "/api/timer/start", json={"kind": "work", "minutes": 25, "label": "Ship the deck"}
    ).json()
    assert started["status"] == "running"
    assert started["planned_secs"] == 1500
    assert 1490 <= started["remaining_secs"] <= 1500

    active = client.get("/api/timer").json()["active"]
    assert active["label"] == "Ship the deck"

    stopped = client.post("/api/timer/stop").json()
    assert stopped["status"] == "cancelled"
    assert client.get("/api/timer").json()["active"] is None
    assert client.post("/api/timer/stop").status_code == 404


def test_starting_a_session_replaces_the_running_one(client):
    client.post("/api/timer/start", json={"kind": "work", "minutes": 50})
    client.post("/api/timer/start", json={"kind": "break", "minutes": 5})

    overview = client.get("/api/timer").json()
    assert overview["active"]["kind"] == "break"
    # Exactly one may run at a time — the DB enforces it, not just the service.
    assert sum(1 for h in overview["history"] if h["status"] == "running") == 1
    assert [h["status"] for h in overview["history"]] == ["running", "cancelled"]


def test_completing_counts_toward_today(client):
    client.post("/api/timer/start", json={"kind": "work", "minutes": 25})
    done = client.post("/api/timer/complete").json()
    assert done["status"] == "completed"

    overview = client.get("/api/timer").json()
    assert overview["sessions_completed_today"] == 1
    assert overview["work_minutes_today"] == 25  # full plan credited on completion
    assert overview["active"] is None


def test_expired_session_auto_completes_on_read(client):
    """A block that ran out while the deck was closed must not stay 'running'."""
    import sqlite3

    from app.deps import get_db

    client.post("/api/timer/start", json={"kind": "work", "minutes": 25})
    db: sqlite3.Connection = get_db()
    db.execute("UPDATE focus_sessions SET ends_at = datetime('now', '-1 minute') WHERE status='running'")
    db.commit()

    overview = client.get("/api/timer").json()
    assert overview["active"] is None
    assert overview["sessions_completed_today"] == 1


def test_timer_rejects_absurd_durations(client):
    assert client.post("/api/timer/start", json={"kind": "work", "minutes": 0}).status_code == 422
    assert client.post("/api/timer/start", json={"kind": "work", "minutes": 999}).status_code == 422
    assert client.post("/api/timer/start", json={"kind": "nap", "minutes": 10}).status_code == 422


# --- email -------------------------------------------------------------------


def test_email_status_degrades_without_a_token(client, tmp_path, monkeypatch):
    """No categorizer on disk must be reported, not raised."""
    body = client.get("/api/email/status").json()
    assert body["configured"] is False       # temp workspace has no token.json
    assert body["tracked_total"] == 0
    assert body["categories"] == []


def test_email_inbox_reports_not_configured_instead_of_failing(client):
    body = client.get("/api/email").json()
    assert body["fetched"] is False
    assert "not connected" in body["error"]
    assert body["messages"] == []


def test_email_classify_unpacks_the_categorizer_tuple():
    """categorize_email returns (category, scores) — the string must be extracted."""
    from app.services import email_service

    class FakeChecker:
        @staticmethod
        def categorize_email(*_a):
            return ("Jobs & Career", {"Jobs & Career": 15})

        @staticmethod
        def get_importance(*_a):
            return (4, "important", True, "LinkedIn - Recruiter contact")

    category, level, label, is_vip, vip_label = email_service._classify(
        FakeChecker, {}, "a@b.com", "subject", "preview"
    )
    assert category == "Jobs & Career"
    assert (level, label, is_vip) == (4, "important", True)
    assert vip_label == "LinkedIn - Recruiter contact"


def test_email_module_is_listed(client):
    ids = {m["id"] for m in client.get("/api/system").json()["modules"]}
    assert {"email", "timer"} <= ids


# --- email search, categories, importance corrections ------------------------


def test_email_rules_crud_and_precedence(client):
    """sender beats domain beats category — most specific correction wins."""
    from app.services.email_service import _apply_rules

    client.post("/api/email/rules", json={
        "scope": "category", "match_value": "jobs & career", "importance": 3})
    client.post("/api/email/rules", json={
        "scope": "domain", "match_value": "linkedin.com", "importance": 2})
    sender_rule = client.post("/api/email/rules", json={
        "scope": "sender", "match_value": "editors-noreply@linkedin.com",
        "importance": 1, "note": "newsletter", "original_importance": 4}).json()

    assert sender_rule["importance"] == 1
    assert sender_rule["original_importance"] == 4
    assert len(client.get("/api/email/rules").json()) == 3

    from app.deps import get_db
    from app.services.email_service import _rule_index
    rules = _rule_index(get_db())

    # Exact sender wins over its own domain and category.
    level, by, _ = _apply_rules(rules, "LinkedIn <editors-noreply@linkedin.com>",
                                "Jobs & Career", 4)
    assert (level, by) == (1, "sender:editors-noreply@linkedin.com")

    # A different sender at the same domain falls through to the domain rule.
    level, by, _ = _apply_rules(rules, "jobalerts-noreply@linkedin.com", "Jobs & Career", 4)
    assert (level, by) == (2, "domain:linkedin.com")

    # Neither: the category rule catches it.
    level, by, _ = _apply_rules(rules, "recruiter@somewhere.io", "Jobs & Career", 4)
    assert (level, by) == (3, "category:jobs & career")

    # No rule at all leaves the agent's score untouched.
    level, by, rid = _apply_rules(rules, "a@b.com", "Newsletters & Promos", 2)
    assert (level, by, rid) == (2, "", None)


def test_email_rule_upsert_is_idempotent_and_case_insensitive(client):
    first = client.post("/api/email/rules", json={
        "scope": "sender", "match_value": "Loud@Example.COM", "importance": 5}).json()
    second = client.post("/api/email/rules", json={
        "scope": "sender", "match_value": "loud@example.com", "importance": 1,
        "note": "changed my mind"}).json()

    assert first["id"] == second["id"], "same target must update, not duplicate"
    assert second["match_value"] == "loud@example.com"
    assert second["importance"] == 1
    assert second["note"] == "changed my mind"
    assert len(client.get("/api/email/rules").json()) == 1


def test_email_rule_delete(client):
    rule = client.post("/api/email/rules", json={
        "scope": "domain", "match_value": "spam.io", "importance": 1}).json()
    assert client.delete(f"/api/email/rules/{rule['id']}").json()["ok"] is True
    assert client.get("/api/email/rules").json() == []
    assert client.delete(f"/api/email/rules/{rule['id']}").status_code == 404


def test_email_rule_validation(client):
    assert client.post("/api/email/rules", json={
        "scope": "sender", "match_value": "a@b.com", "importance": 9}).status_code == 422
    assert client.post("/api/email/rules", json={
        "scope": "planet", "match_value": "a@b.com", "importance": 3}).status_code == 422
    assert client.post("/api/email/rules", json={
        "scope": "sender", "match_value": "", "importance": 3}).status_code == 422


def test_email_address_and_domain_extraction():
    from app.services.email_service import _address, _domain

    assert _address("LinkedIn News <Editors-NoReply@LinkedIn.com>") == "editors-noreply@linkedin.com"
    assert _domain("LinkedIn News <Editors-NoReply@LinkedIn.com>") == "linkedin.com"
    assert _address("plain@example.org") == "plain@example.org"
    assert _domain("no-at-sign") == ""


def test_email_categories_endpoint(client):
    # Temp workspace has no categorizer, so this degrades to empty rather than 500.
    assert client.get("/api/email/categories").json() == []


# --- vault write-through -----------------------------------------------------


def _todo_md(vault):
    return (vault / "02-areas" / "todo-list.md")


def test_event_write_rebuilds_the_markdown_appointments_table(client, vault):
    md = _todo_md(vault)
    before = md.read_text()

    client.post("/api/events", json={
        "title": "Call Houston ITAD", "date": "2026-08-05", "time": "14:30"})

    after = md.read_text()
    assert "| 2026-08-05 | 14:30 | Call Houston ITAD | 📅 |" in after
    # The seeded event is still there — the table is rebuilt, not replaced.
    assert "Study block" in after
    # Hand-curated sections are untouched.
    for section in ("## 👤 Personal", "## 💼 Work", "## 📌 Quick Reference"):
        assert section in after, f"{section} was destroyed"
    assert before.count("## ") == after.count("## ")


def test_event_delete_removes_it_from_the_markdown(client, vault):
    created = client.post("/api/events", json={
        "title": "Temporary thing", "date": "2026-09-01"}).json()
    assert "Temporary thing" in _todo_md(vault).read_text()

    client.delete(f"/api/events/{created['id']}")
    assert "Temporary thing" not in _todo_md(vault).read_text()


def test_sync_refuses_to_touch_a_file_without_the_section(client, vault):
    """A reshaped note must be left alone, not half-rewritten."""
    md = _todo_md(vault)
    md.write_text("# My list\n\nNo appointments heading here.\n")
    original = md.read_text()

    response = client.post("/api/events/sync")
    assert response.status_code == 409
    assert "not found" in response.json()["detail"]
    assert md.read_text() == original, "file was modified despite the failure"


def test_sync_escapes_pipes_so_the_table_cannot_break(client, vault):
    client.post("/api/events", json={"title": "A | B | C", "date": "2026-08-08"})
    import re

    row = [l for l in _todo_md(vault).read_text().splitlines() if "A \\| B" in l]
    assert row, "pipe in the title was not escaped"
    # Count only unescaped pipes: 5 cells means 6 real delimiters.
    assert len(re.findall(r"(?<!\\)\|", row[0])) == 6


def test_sync_endpoint_reports_what_it_did(client):
    body = client.post("/api/events/sync").json()
    assert body["ok"] is True
    assert "Appointments" in body["message"] or "up to date" in body["message"]


def test_quick_add_task_lands_in_the_vault_json(client, vault):
    import json as _json

    client.post("/api/tasks", json={
        "title": "Bid on an OptiPlex lot", "priority": "high", "due_date": "2026-08-10"})
    raw = _json.loads((vault / "02-areas" / "todo-data.json").read_text())
    stored = [t for t in raw["boardTasks"] if t["title"] == "Bid on an OptiPlex lot"]
    assert stored and stored[0]["dueDate"] == "2026-08-10"
    assert stored[0]["priority"] == "high"


# --- metric history / trends -------------------------------------------------


def test_snapshot_then_trend(client):
    from datetime import date, timedelta

    from app.deps import get_db

    client.post("/api/command/history/snapshot")
    assert "open_tasks" in client.get("/api/command/history").json()

    # Plant a yesterday so today has something to compare against.
    db = get_db()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.execute(
        "INSERT INTO metric_history (day, metric_key, value, health) VALUES (?,?,?,?)",
        (yesterday, "open_tasks", 5.0, "yellow"),
    )
    db.commit()

    metrics = {m["key"]: m for m in client.get("/api/command/brief?sweep=false").json()["metrics"]}
    open_tasks = metrics["open_tasks"]
    assert open_tasks["trend"] == "down"          # fixture has 1 open task, was 5
    assert "▼ 4 vs last" in open_tasks["hint"]
    assert "⚠" not in open_tasks["hint"], "fewer open tasks is an improvement"


def test_trend_flags_a_bad_direction(client):
    from datetime import date, timedelta

    from app.deps import get_db

    db = get_db()
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    db.execute(
        "INSERT INTO metric_history (day, metric_key, value, health) VALUES (?,?,?,?)",
        (yesterday, "journal_streak", 3.0, "green"),
    )
    db.commit()

    metrics = {m["key"]: m for m in client.get("/api/command/brief?sweep=false").json()["metrics"]}
    streak = metrics["journal_streak"]
    assert streak["trend"] == "down"
    assert "⚠" in streak["hint"], "a falling streak should be flagged"


def test_snapshot_is_idempotent_per_day(client):
    from app.deps import get_db

    client.post("/api/command/history/snapshot")
    client.post("/api/command/history/snapshot")
    rows = get_db().execute(
        "SELECT COUNT(*) AS n FROM metric_history WHERE metric_key = 'open_tasks'"
    ).fetchone()
    assert rows["n"] == 1, "same day must update, not duplicate"


def test_metric_series_endpoint(client):
    client.post("/api/command/history/snapshot")
    body = client.get("/api/command/history/open_tasks").json()
    assert body["metric_key"] == "open_tasks"
    assert len(body["points"]) == 1
    assert body["points"][0]["day"]


# --- notifications -----------------------------------------------------------


def test_notification_writes_to_the_hq_inbox(client, workspace):
    body = client.post("/api/command/notify/test", json={
        "title": "Test ping", "body": "hello", "severity": "info"}).json()
    assert body["sent"]["hq_inbox"].endswith(".md")
    assert body["sent"]["discord"] == "not configured"

    dropped = list((workspace / "hq" / "INBOX").glob("blanco-os-*.md"))
    assert dropped, "nothing landed where Sweet Jones looks"
    text = dropped[0].read_text()
    assert "Test ping" in text and "hello" in text


def test_notifications_dedupe_but_force_overrides(client):
    from app.services import notify
    from app.deps import get_db

    db = get_db()
    first = notify.send(db, "Standing problem", "still broken", "critical", dedupe_key="k1")
    assert first["hq_inbox"].endswith(".md")

    second = notify.send(db, "Standing problem", "still broken", "critical", dedupe_key="k1")
    assert second["hq_inbox"] == "deduped", "a standing alert must not spam"

    third = notify.send(db, "Standing problem", "x", "critical", dedupe_key="k1", force=True)
    assert third["hq_inbox"].endswith(".md")


def test_info_alerts_are_below_the_notify_floor(client):
    from app.services import notify
    from app.deps import get_db

    result = notify.send(get_db(), "Minor thing", severity="info", dedupe_key="quiet")
    assert "skipped" in result


def test_sweep_announces_alerts_without_raising(client, workspace):
    """A notification failure must never break the sweep."""
    alerts = client.post("/api/command/sweep").json()
    assert isinstance(alerts, list)
    assert client.get("/api/command/brief").status_code == 200


def test_freshness_walks_subdirectories(tmp_path):
    """A note written into a subdirectory must count as activity.

    Statting only the parent directory made the OS report a job six days
    stale when it had written a note three days earlier.
    """
    import os
    import time

    from app.services.systems_service import _file_freshness, _newest_mtime

    root = tmp_path / "reference"
    (root / "building").mkdir(parents=True)
    note = root / "building" / "2026-07-27_electrical-basics.md"
    note.write_text("# Electrical Basics")

    # Age the parent dir well past the threshold, keep the note fresh.
    old = time.time() - (10 * 24 * 3600)
    os.utime(root, (old, old))

    assert _newest_mtime(root) == note.stat().st_mtime
    status, detail = _file_freshness(root, max_age_hours=96)
    assert status == "up", f"subdirectory write was missed: {detail}"


def test_freshness_reports_empty_and_missing(tmp_path):
    from app.services.systems_service import _file_freshness

    assert _file_freshness(tmp_path / "nope", 96)[0] == "down"
    (tmp_path / "empty").mkdir()
    assert _file_freshness(tmp_path / "empty", 96)[0] == "stale"


def test_local_day_window_is_not_utc_day(client, monkeypatch):
    """After 7 PM Chicago it is already tomorrow in UTC.

    A work block logged at 8 PM belongs to the day Blanco actually worked it,
    so daily rollups must use his calendar day, not UTC's.
    """
    from app.services import store

    start_utc, end_utc = store.local_day_bounds_utc("2026-07-27")
    # CDT is UTC-5, so the local day starts at 05:00 UTC and is 24h long.
    assert start_utc == "2026-07-27 05:00:00"
    assert end_utc == "2026-07-28 05:00:00"


def test_timer_totals_survive_the_utc_rollover(client):
    """A session started after the UTC date flips must still count as today."""
    from app.deps import get_db
    from app.services import store

    started = client.post("/api/timer/start", json={"kind": "work", "minutes": 25}).json()
    client.post("/api/timer/complete")

    # Force the row to 00:30 UTC — i.e. 7:30 PM the previous evening in Chicago.
    db = get_db()
    start_utc, _ = store.local_day_bounds_utc()
    late = start_utc[:10] + " 23:30:00"  # still inside the local-day window
    db.execute("UPDATE focus_sessions SET started_at = ? WHERE id = ?", (late, started["id"]))
    db.commit()

    overview = client.get("/api/timer").json()
    assert overview["sessions_completed_today"] == 1
    assert overview["work_minutes_today"] == 25


# --- messenger ---------------------------------------------------------------

def _stub_turn(monkeypatch, stdout="", stderr="", returncode=0):
    """Stand in for a commander CLI, so these tests never spawn a real one.

    Patches `Popen` rather than the worker, so the streaming reader, the reply
    parser and the session bookkeeping are all exercised for real — the parts
    that actually differ between the three.
    """
    import subprocess

    from app.services import chat_service

    class FakeProc:
        # Stands in for a CLI that has already exited: `poll` never returns
        # None, so the wall-clock watchdog sees a finished process and stops
        # immediately instead of spinning for the length of the ceiling.
        pid = 4242

        def __init__(self):
            self.stdout = iter(stdout.splitlines(keepends=True))
            self.stderr = io.StringIO(stderr)
            self.stdin = io.StringIO()
            self.returncode = returncode

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def poll(self):
            return self.returncode

        def wait(self, timeout=None):
            return self.returncode

        def kill(self):
            pass

    captured = {}

    def fake_popen(argv, **kwargs):
        # The ACP bridge is tried first for OpenClaw and falls back to argv, so
        # the *last* call is the one carrying the real invocation.
        captured["argv"] = argv
        captured["env"] = kwargs.get("env") or {}
        captured["cwd"] = kwargs.get("cwd")
        captured["kwargs"] = kwargs
        return FakeProc()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    # The worker is a daemon thread; running it inline keeps the assertions
    # deterministic without a sleep-and-hope poll. Swapping the module
    # reference rather than threading.Thread itself keeps the patch inside
    # chat_service — patching the real class breaks Timer for everyone else.
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: _Inline(target, args))
    return captured


def _swap_worker(monkeypatch, factory):
    from app.services import chat_service

    # Timer has to come through unpatched: the turn schedules one to expire the
    # live buffer, and a namespace without it turns every turn into an
    # AttributeError after the answer has already been stored.
    monkeypatch.setattr(chat_service, "threading",
                        SimpleNamespace(Thread=factory, Lock=threading.Lock,
                                        Timer=_NoTimer))


class _Inline:
    def __init__(self, target, args):
        self._target, self._args = target, args

    def start(self):
        self._target(*self._args)

    def join(self, timeout=None):
        return None


class _NoTimer:
    """A Timer that never fires. The live buffer's expiry is real behaviour in
    the app but only a dangling thread in a test run."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self):
        return None


def test_chat_send_returns_immediately_with_a_pending_reply(client, monkeypatch):
    """A turn is a cold CLI start, so the request must not block on it."""
    from app.services import chat_service

    captured = {}
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: captured.setdefault("stub", _Noop(args)))

    body = client.post("/api/chat", json={"message": "what's my focus?"}).json()
    assert body["message"]["role"] == "user"
    assert body["reply"]["status"] == "pending"
    # (reply_id, session_id, engine, agent, message, native_session_id)
    args = captured["stub"].args
    assert args[2] == "openclaw" and args[4] == "what's my focus?"


class _Noop:
    def __init__(self, args):
        self.args = args

    def start(self):
        pass

    def join(self, timeout=None):
        return None


def test_the_three_commanders_are_the_whole_console(client):
    engines = client.get("/api/chat/engines").json()
    assert [e["id"] for e in engines] == ["openclaw", "claude", "hermes"]
    assert [e["name"] for e in engines] == ["OpenClaw", "Claude Code", "Hermes"]
    # Every tab reports whether its binary is actually on this box, so a
    # missing CLI reads as "not installed" rather than as a broken turn.
    assert all("available" in e and "binary" in e for e in engines)


def test_a_local_model_is_no_longer_an_engine(client):
    assert client.post("/api/chat", json={"message": "hi", "engine": "ollama:mistral"}
                       ).status_code == 422
    assert client.get("/api/chat/agents?engine=ollama:mistral").status_code == 422


def test_each_commander_has_its_own_roster(client):
    openclaw = client.get("/api/chat/agents?engine=openclaw").json()
    assert openclaw[0]["id"] == "main" and openclaw[0]["primary"] is True
    assert openclaw[0]["name"] == "Sweet Jones"
    # Discovered from the workspace fixture.
    assert {"circuit", "mic"} <= {a["id"] for a in openclaw}
    assert all(a["engine"] == "openclaw" for a in openclaw)

    for engine in ("claude", "hermes"):
        roster = client.get(f"/api/chat/agents?engine={engine}").json()
        assert roster[0]["primary"] is True
        assert all(a["engine"] == engine for a in roster)

    # Unfiltered is every roster concatenated, never a merged namespace.
    everyone = client.get("/api/chat/agents").json()
    assert {a["engine"] for a in everyone} == {"openclaw", "claude", "hermes"}


def test_openclaw_threads_by_session_key_so_it_resumes_from_turn_one(client, monkeypatch):
    captured = _stub_turn(
        monkeypatch,
        stdout='banner noise\n{"status":"ok","result":{"payloads":[{"text":"PONG"}]}}',
    )
    session = client.get("/api/chat/sessions?engine=openclaw").json()[0]

    reply = client.post("/api/chat", json={"message": "ping", "session_id": session["id"]}
                        ).json()["reply"]
    assert client.get(f"/api/chat/{reply['id']}").json()["body"] == "PONG"

    argv = captured["argv"]
    assert argv[1:3] == ["agent", "--agent"]
    assert f"agent:main:deck-{session['id']}" in argv
    # OpenClaw needs no round-trip to be resumable — the key we sent is the id.
    assert client.get("/api/chat/sessions?engine=openclaw").json()[0]["resumable"] is True


def test_claude_streams_and_remembers_its_session(client, monkeypatch):
    """stream-json deltas become the visible reply; the result event ends it."""
    stdout = "\n".join([
        '{"type":"system","session_id":"sess-abc"}',
        '{"type":"stream_event","event":{"type":"content_block_delta",'
        '"delta":{"type":"text_delta","text":"PO"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta",'
        '"delta":{"type":"text_delta","text":"NG"}}}',
        '{"type":"result","session_id":"sess-abc","result":"PONG"}',
    ]) + "\n"
    captured = _stub_turn(monkeypatch, stdout=stdout)

    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]
    assert client.get(f"/api/chat/{reply['id']}").json()["body"] == "PONG"

    assert "--include-partial-messages" in captured["argv"]
    # First turn has nothing to resume; the id it handed back is now stored.
    assert "--resume" not in captured["argv"]
    assert client.get("/api/chat/sessions?engine=claude").json()[0]["resumable"] is True

    client.post("/api/chat", json={"message": "again", "engine": "claude",
                                   "session_id": session["id"]})
    argv = captured["argv"]
    assert argv[argv.index("--resume") + 1] == "sess-abc"


def test_claude_keeps_the_session_id_even_when_the_turn_failed(client, monkeypatch):
    """A failed turn that still has a thread is worth keeping — losing the id
    would silently orphan the conversation."""
    stdout = "\n".join([
        '{"type":"system","session_id":"sess-err"}',
        '{"type":"result","session_id":"sess-err","is_error":true,'
        '"result":"tool use was denied"}',
    ]) + "\n"
    _stub_turn(monkeypatch, stdout=stdout)

    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]
    landed = client.get(f"/api/chat/{reply['id']}").json()
    assert landed["status"] == "failed" and "denied" in landed["error"]
    assert client.get("/api/chat/sessions?engine=claude").json()[0]["resumable"] is True


def test_hermes_answer_excludes_its_own_plumbing(client, monkeypatch):
    """`chat -Q` still prints a resume notice and the session id first; neither
    belongs in the bubble."""
    stdout = (
        '↻ Resumed session 20260829_234605_64f4de "Ping" (1 user message)\n'
        "⚡ YOLO mode restored from session — all commands auto-approved.\n"
        "\n"
        "session_id: 20260829_234605_64f4de\n"
        "PONG\n"
    )
    captured = _stub_turn(monkeypatch, stdout=stdout)

    session = client.get("/api/chat/sessions?engine=hermes").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "hermes",
                                           "session_id": session["id"]}).json()["reply"]
    assert client.get(f"/api/chat/{reply['id']}").json()["body"] == "PONG"

    # `chat`, not the top-level --oneshot: only the former rehydrates. And no
    # -Q — quiet mode suppresses the tool previews that are the only sign of
    # life Hermes gives during a turn. Oneshot is implied on non-TTY stdio, so
    # dropping the flag costs nothing but stops throwing the working away.
    assert captured["argv"][1:3] == ["chat", "--pass-session-id"]
    assert "-Q" not in captured["argv"]
    assert client.get("/api/chat/sessions?engine=hermes").json()[0]["resumable"] is True


def test_hermes_reports_a_provider_error_rather_than_answering_with_it(client, monkeypatch):
    """Hermes prints failures on stdout and exits zero, so an HTTP-shaped line
    is the only signal there is."""
    _stub_turn(monkeypatch, stdout="HTTP 404: Model 'deepseek' not found.\n")

    session = client.get("/api/chat/sessions?engine=hermes").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "hermes",
                                           "session_id": session["id"]}).json()["reply"]
    landed = client.get(f"/api/chat/{reply['id']}").json()
    assert landed["status"] == "failed"
    assert "deepseek" in landed["error"] and landed["body"] == ""


def test_a_hermes_subagent_is_selected_with_the_profile_flag(client, monkeypatch):
    """A Hermes profile is chosen with `-p <name>`. HERMES_PROFILE in the
    environment is only a label to Hermes and silently ran the default."""
    from app.services import commanders

    monkeypatch.setattr(commanders, "_hermes_profile_list",
                        lambda: "Profile   Model\n────\n◆default   x\n research  y\n")
    roster = client.get("/api/chat/agents?engine=hermes").json()
    assert [a["id"] for a in roster] == ["default", "research"]

    captured = _stub_turn(monkeypatch, stdout="session_id: s1\nPONG\n")
    session = client.get("/api/chat/sessions?engine=hermes").json()[0]
    client.patch(f"/api/chat/sessions/{session['id']}", json={"agent": "research"})
    client.post("/api/chat", json={"message": "ping", "engine": "hermes",
                                   "session_id": session["id"]})
    argv = captured["argv"]
    assert argv[1:3] == ["-p", "research"]
    # This used to assert the opposite — that the deck must not send a model or
    # provider, because the profile's own config.yaml owns them. That rule is
    # what made every Hermes sub-agent unusable: each profile's config names
    # provider `deepseek`, and no profile holds a deepseek credential (a cloned
    # profile gets an empty `providers` map in its auth.json), so every turn
    # died on "No usable credentials" while still exiting 0. The deck's
    # provider now applies to profiles too. Changed 2026-09-25.
    assert "--provider" in argv and "--model" in argv


def test_the_message_follows_the_session_not_the_payload(client, monkeypatch):
    """A stale client must not be able to fire a Claude turn into a Hermes
    thread — the thread owns its commander."""
    captured = _stub_turn(monkeypatch, stdout="session_id: s1\nPONG\n")
    session = client.get("/api/chat/sessions?engine=hermes").json()[0]

    client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                   "session_id": session["id"]})
    assert "hermes" in captured["argv"][0]


def test_repointing_a_session_forgets_the_old_thread(client, monkeypatch):
    """A resume handle belongs to the sub-agent that produced it."""
    _stub_turn(monkeypatch, stdout='{"status":"ok","result":{"payloads":[{"text":"hi"}]}}')
    session = client.get("/api/chat/sessions?engine=openclaw").json()[0]

    updated = client.patch(f"/api/chat/sessions/{session['id']}",
                           json={"agent": "circuit"}).json()
    assert updated["agent"] == "circuit"

    _stub_turn(monkeypatch, stdout='{"status":"ok","result":{"payloads":[{"text":"hi"}]}}')
    client.post("/api/chat", json={"message": "ping", "session_id": session["id"]})
    assert client.get("/api/chat?session_id=%d" % session["id"]).json()[-1]["agent"] == "circuit"


def test_sessions_are_scoped_to_one_commander(client):
    """Switching commander switches which threads exist, and the first visit to
    each lands in a usable one rather than an empty tab strip."""
    for engine in ("openclaw", "claude", "hermes"):
        sessions = client.get(f"/api/chat/sessions?engine={engine}").json()
        assert sessions and all(s["engine"] == engine for s in sessions)

    made = client.post("/api/chat/sessions",
                       json={"label": "Build", "engine": "claude"}).json()
    assert made["engine"] == "claude"
    claude = client.get("/api/chat/sessions?engine=claude").json()
    assert [s["label"] for s in claude][-1] == "Build"
    # …and it is invisible from the other two tabs.
    assert made["id"] not in [s["id"] for s in
                              client.get("/api/chat/sessions?engine=hermes").json()]


def test_the_last_session_of_a_commander_cannot_be_closed(client):
    hermes = client.get("/api/chat/sessions?engine=hermes").json()
    assert client.delete(f"/api/chat/sessions/{hermes[0]['id']}").status_code == 400

    extra = client.post("/api/chat/sessions", json={"engine": "hermes"}).json()
    assert client.delete(f"/api/chat/sessions/{extra['id']}").status_code == 200


def test_forgetting_a_thread_keeps_its_visible_history(client, monkeypatch):
    _stub_turn(monkeypatch, stdout="session_id: s1\nPONG\n")
    session = client.get("/api/chat/sessions?engine=hermes").json()[0]
    client.post("/api/chat", json={"message": "ping", "engine": "hermes",
                                   "session_id": session["id"]})

    after = client.patch(f"/api/chat/sessions/{session['id']}", json={"forget": True}).json()
    assert after["resumable"] is False
    assert len(client.get(f"/api/chat?session_id={session['id']}").json()) == 2


def test_clearing_a_thread_also_drops_its_resume_handle(client, monkeypatch):
    """Otherwise the commander keeps a memory of everything the deck just said
    was gone."""
    _stub_turn(monkeypatch, stdout="session_id: s1\nPONG\n")
    session = client.get("/api/chat/sessions?engine=hermes").json()[0]
    client.post("/api/chat", json={"message": "ping", "engine": "hermes",
                                   "session_id": session["id"]})

    client.delete(f"/api/chat?session_id={session['id']}")
    assert client.get(f"/api/chat?session_id={session['id']}").json() == []
    assert client.get("/api/chat/sessions?engine=hermes").json()[0]["resumable"] is False


def test_os_notices_go_to_the_alert_tray_not_the_console(client):
    """The timeline is strictly the conversation. A notice that scrolls away
    between two questions is a notice nobody acted on."""
    from app.db import connect
    from app.services import notify, store

    db = connect()
    try:
        alert = store.raise_alert(db, schemas_alert("disk at 92%"))
        notify.announce_alerts(db, [alert])
    finally:
        db.close()

    assert client.get("/api/chat").json() == []
    titles = [a["title"] for a in client.get("/api/command/alerts").json()]
    assert "disk at 92%" in titles


def schemas_alert(title):
    from app import schemas

    return schemas.AlertCreate(source="test", severity="warning", title=title)


def test_reply_stream_emits_deltas_then_one_done(client, monkeypatch):
    from app.services import chat_service

    # The turn is stubbed to hang: this test is about the SSE endpoint, and a
    # worker that finished first would leave nothing to stream.
    _stub_turn(monkeypatch, stdout="")
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: _Noop(args))

    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]

    chat_service.turns.open(reply["id"])

    def land():
        from app.db import connect

        chat_service.turns.delta(reply["id"], "PONG")
        db = connect()
        try:
            db.execute("UPDATE chat_messages SET body='PONG', status='done' WHERE id=?",
                       (reply["id"],))
            db.commit()
        finally:
            db.close()
        chat_service.turns.close(reply["id"])

    threading.Timer(0.35, land).start()

    with client.stream("GET", f"/api/chat/{reply['id']}/stream") as response:
        events = "".join(chunk for chunk in response.iter_text())

    assert '"text": "PONG"' in events
    assert events.count("event: done") == 1
    chat_service.turns.drop(reply["id"])


def test_the_stream_carries_the_working_not_only_the_answer(client, monkeypatch):
    """The point of the rebuild: a commander's tool calls reach the console
    while the turn is still running, instead of a spinner until it exits."""
    from app.services import chat_service

    _stub_turn(monkeypatch, stdout="")
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: _Noop(args))
    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]
    chat_service.turns.open(reply["id"])

    def land():
        from app.db import connect

        chat_service.turns.activity(reply["id"], "Bash", "git status",
                                    icon="tool", state="running")
        chat_service.turns.usage(reply["id"], cost_usd=0.0125)
        chat_service.turns.delta(reply["id"], "done")
        db = connect()
        try:
            db.execute("UPDATE chat_messages SET body='done', status='done' WHERE id=?",
                       (reply["id"],))
            db.commit()
        finally:
            db.close()
        chat_service.turns.close(reply["id"])

    threading.Timer(0.35, land).start()
    with client.stream("GET", f"/api/chat/{reply['id']}/stream") as response:
        events = "".join(chunk for chunk in response.iter_text())

    assert "event: activity" in events and "git status" in events
    assert "event: usage" in events and "0.0125" in events
    chat_service.turns.drop(reply["id"])


def test_a_reconnecting_reader_is_not_resent_what_it_already_saw(client, monkeypatch):
    """A reload mid-turn used to strand the bubble. Events are numbered, so a
    reader resumes at the last one it saw."""
    from app.services import chat_service

    _stub_turn(monkeypatch, stdout="")
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: _Noop(args))
    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]

    chat_service.turns.open(reply["id"])
    chat_service.turns.delta(reply["id"], "first")
    chat_service.turns.delta(reply["id"], "second")
    # The worker records the row before closing the buffer; the endpoint sends
    # its one `done` from the row, so the row has to exist for it to finish.
    _land_reply(reply["id"], "firstsecond")
    chat_service.turns.close(reply["id"])

    with client.stream("GET", f"/api/chat/{reply['id']}/stream",
                       params={"after": 1}) as response:
        events = "".join(chunk for chunk in response.iter_text())

    # Assert on the delta payloads, not the whole feed: the one `done` event
    # carries the finished row, whose body legitimately contains both halves.
    assert '{"text": "second"}' in events
    assert '{"text": "first"}' not in events
    chat_service.turns.drop(reply["id"])


def test_a_running_turn_can_be_stopped_and_keeps_what_it_wrote(client, monkeypatch):
    """Esc in a terminal does not throw the half-written answer away."""
    from app.services import chat_service

    _stub_turn(monkeypatch, stdout="")
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: _Noop(args))
    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]

    # A turn nobody is running cannot be stopped, and saying so beats a 500.
    assert client.post("/api/chat/999999/cancel").status_code == 409

    turn = chat_service.turns.open(reply["id"])
    chat_service.turns.delta(reply["id"], "half an answer")
    killed = {}
    chat_service._register_canceller(reply["id"], lambda: killed.setdefault("yes", True))

    assert chat_service.turns.is_cancelled(reply["id"]) is False
    threading.Timer(0.2, lambda: _finish_cancelled(reply["id"], turn)).start()
    stopped = client.post(f"/api/chat/{reply['id']}/cancel")

    assert stopped.status_code == 200
    assert killed.get("yes") is True
    assert "half an answer" in stopped.json()["body"]
    chat_service.turns.drop(reply["id"])


def _land_reply(reply_id, body):
    from app.db import connect

    db = connect()
    try:
        db.execute("UPDATE chat_messages SET body=?, status='done' WHERE id=?",
                   (body, reply_id))
        db.commit()
    finally:
        db.close()


def _finish_cancelled(reply_id, turn):
    """Stand in for the worker noticing the cancel and recording the row."""
    from app.db import connect

    db = connect()
    try:
        db.execute("UPDATE chat_messages SET body=?, status='done' WHERE id=?",
                   (turn.text + "\n\n_(stopped)_", reply_id))
        db.commit()
    finally:
        db.close()


def test_pending_turns_are_listed_so_a_reload_can_re_attach(client, monkeypatch):
    _stub_turn(monkeypatch, stdout="")
    _swap_worker(monkeypatch, lambda target, args=(), daemon=None: _Noop(args))
    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    reply = client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                           "session_id": session["id"]}).json()["reply"]

    running = client.get("/api/chat/pending/all").json()
    assert [m["id"] for m in running] == [reply["id"]]


def test_claude_is_not_left_waiting_on_stdin(client, monkeypatch):
    """`claude -p` waits three seconds for input that is never coming and says
    so on stderr — three seconds added to every turn on this tab."""
    import subprocess as sp

    captured = _stub_turn(monkeypatch, stdout='{"type":"result","result":"PONG"}\n')
    session = client.get("/api/chat/sessions?engine=claude").json()[0]
    client.post("/api/chat", json={"message": "ping", "engine": "claude",
                                   "session_id": session["id"]})

    assert captured["kwargs"]["stdin"] is sp.DEVNULL
    # Its own process group, so stopping a turn kills what the CLI spawned too.
    assert captured["kwargs"]["start_new_session"] is True
    # Nothing here can approve on Blanco's behalf, and the default ("host")
    # leaves a prompt unanswered until the ceiling — which reads as a hang.
    assert captured["argv"][captured["argv"].index("--permission-prompts") + 1] == "none"


def test_openclaw_falls_back_to_the_same_thread_the_bridge_would_have_used(
        client, monkeypatch):
    """The ACP bridge is the streaming path; the argv is the fallback. Both
    must send the same session key, or a fallback turn quietly opens a second
    empty conversation beside the real one."""
    from app.services import commanders

    captured = _stub_turn(
        monkeypatch,
        stdout='{"status":"ok","result":{"payloads":[{"text":"PONG"}]}}',
    )
    session = client.get("/api/chat/sessions?engine=openclaw").json()[0]
    client.post("/api/chat", json={"message": "ping", "session_id": session["id"]})

    agent = commanders.resolve("openclaw", "main")
    expected = commanders.openclaw_session_key(agent, f"deck-{session['id']}")
    assert expected in captured["argv"]


def test_reply_stream_404s_on_an_unknown_message(client):
    assert client.get("/api/chat/9999/stream").status_code == 404


def test_chat_history_and_clear(client, monkeypatch):
    _stub_turn(monkeypatch, stdout='{"status":"ok","result":{"payloads":[{"text":"hi"}]}}')
    client.post("/api/chat", json={"message": "one"})
    assert len(client.get("/api/chat").json()) == 2

    assert client.delete("/api/chat").status_code == 200
    assert client.get("/api/chat").json() == []


def test_chat_rejects_empty_and_oversized(client):
    assert client.post("/api/chat", json={"message": ""}).status_code == 422
    assert client.post("/api/chat", json={"message": "x" * 4001}).status_code == 422


def test_chat_message_404(client):
    assert client.get("/api/chat/9999").status_code == 404


def test_chat_send_into_an_unknown_session_404s(client):
    assert client.post("/api/chat", json={"message": "hi", "session_id": 999}
                       ).status_code == 404


def test_chat_sessions_rename_and_delete_unknown_404(client):
    assert client.patch("/api/chat/sessions/999", json={"label": "x"}).status_code == 404
    assert client.delete("/api/chat/sessions/999").status_code == 404


def test_a_restart_closes_out_replies_whose_worker_died(client):
    """No daemon thread survives a restart, so a row still pending at startup
    is waiting on a worker that no longer exists. One had been "thinking" for
    three weeks."""
    from app.db import bootstrap, connect

    db = connect()
    try:
        db.execute("INSERT INTO chat_messages (role, status) VALUES ('assistant', 'pending')")
        db.commit()
    finally:
        db.close()

    bootstrap().close()

    stranded = [m for m in client.get("/api/chat").json() if m["role"] == "assistant"]
    assert stranded and all(m["status"] == "failed" for m in stranded)
    assert "restart" in stranded[-1]["error"]


# --------------------------------------------------------------------------
# opening proposals in Opera
# --------------------------------------------------------------------------
def test_open_in_opera_refuses_other_hosts_and_schemes(client):
    for url in ("https://evil.example/x", "http://claude.ai/x", "javascript:alert(1)"):
        assert client.post("/api/open-in-opera", json={"url": url}).status_code == 400


def test_open_in_opera_launches_with_url_as_one_argument(client, monkeypatch, tmp_path):
    fake = tmp_path / "opera.exe"
    fake.write_text("")
    monkeypatch.setenv("BLANCO_OS_OPERA_PATH", str(fake))
    from app.config import get_settings
    get_settings.cache_clear()

    calls = []
    import subprocess
    monkeypatch.setattr(subprocess, "Popen", lambda argv, **kw: calls.append(argv))

    url = "https://claude.ai/code/artifact/abc"
    assert client.post("/api/open-in-opera", json={"url": url}).status_code == 200
    assert calls == [[str(fake), url]]
