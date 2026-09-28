"""Your Business: three divisions, their ventures, and the deal pipeline.

The roll-up gets the most coverage here. Everything else is CRUD, and CRUD that
breaks breaks loudly — a division total that quietly double-counts a venture
does not, and it would be believed, because it appears next to numbers that are
correct.
"""

from __future__ import annotations

from datetime import date


DIVISIONS = ("consultations", "electronics", "ecommerce", "freight", "freelance")


# --------------------------------------------------------------------------
# seeding and the venture moves
# --------------------------------------------------------------------------
def test_divisions_exist_in_order(client):
    divisions = client.get("/api/biz/divisions").json()
    assert [d["id"] for d in divisions] == list(DIVISIONS)
    assert [d["name"] for d in divisions] == [
        "Consultations & Automations", "Electronics", "E-Commerce", "Freight Broker",
        "Freelance",
    ]


def test_freight_starts_empty(client):
    assert client.get("/api/biz/divisions/freight").json()["ventures"] == []


def test_freelance_starts_empty_and_takes_marketplace_deals(client):
    assert client.get("/api/biz/divisions/freelance").json()["ventures"] == []
    deal = _deal(client, division_id="freelance", client="Upwork client", source="upwork")
    assert deal.status_code == 201, deal.text
    assert deal.json()["division_id"] == "freelance"
    assert deal.json()["source"] == "upwork"


def test_divisions_carry_identity_but_no_invented_numbers(client):
    """Same rule migration 003 set: seed what is on disk, never a judgement call."""
    for division in client.get("/api/biz/divisions").json():
        assert division["name"] and division["emoji"], "identity is seeded"
        assert division["stage"] == "unset", f"{division['id']} arrived with a stage"
        assert division["health"] == "unknown"
        assert division["monthly_target"] == 0
        assert division["next_action"] == ""


def test_electronics_owns_both_electronics_ventures(client):
    electronics = client.get("/api/biz/divisions/electronics").json()
    assert {v["id"] for v in electronics["ventures"]} == {"northwind", "electronics-export"}


def test_consultations_owns_the_biz_venture(client):
    """The biz row is linked, not replaced — it carries the ledger history."""
    consultations = client.get("/api/biz/divisions/consultations").json()
    assert [v["id"] for v in consultations["ventures"]] == ["biz"]


def test_ecommerce_starts_empty(client):
    assert client.get("/api/biz/divisions/ecommerce").json()["ventures"] == []


def test_ventures_outside_biz_stay_unparented(client):
    ventures = {v["id"]: v for v in client.get("/api/ventures").json()}
    assert ventures["trading"]["division_id"] is None
    assert ventures["studio"]["division_id"] is None
    assert ventures["northwind"]["division_id"] == "electronics"


# --------------------------------------------------------------------------
# the roll-up — the part worth testing properly
# --------------------------------------------------------------------------
def test_division_actual_rolls_up_from_its_ventures(client):
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 400, "label": "parts lot"})
    client.post("/api/ventures/electronics-export/events",
                json={"kind": "revenue", "amount": 250, "label": "broker fee"})

    electronics = client.get("/api/biz/divisions/electronics").json()
    assert electronics["monthly_actual"] == 650


def test_expenses_net_against_the_rollup(client):
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 400, "label": "parts lot"})
    client.post("/api/ventures/northwind/events",
                json={"kind": "expense", "amount": 150, "label": "shipping"})

    assert client.get("/api/biz/divisions/electronics").json()["monthly_actual"] == 250


def test_the_move_does_not_change_the_money_tab(client):
    """The roll-up is derived, so Your Business cannot double-count against /api/money."""
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 400, "label": "parts lot"})

    money = client.get("/api/money").json()
    biz = client.get("/api/biz").json()

    # Your Business's total is a subset of the money tab's, not an addition to it.
    assert money["total_actual"] >= biz["total_actual"]
    assert biz["total_actual"] == 400
    assert money["revenue_mtd"] == 400


def test_attainment_is_zero_without_a_target_not_an_error(client):
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 400, "label": "parts lot"})
    assert client.get("/api/biz/divisions/electronics").json()["attainment"] == 0.0


def test_attainment_against_a_target_blanco_set(client):
    client.patch("/api/biz/divisions/electronics", json={"monthly_target": 1000})
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 250, "label": "parts lot"})
    assert client.get("/api/biz/divisions/electronics").json()["attainment"] == 0.25


# --------------------------------------------------------------------------
# division edits
# --------------------------------------------------------------------------
def test_blanco_sets_what_the_seed_would_not(client):
    r = client.patch("/api/biz/divisions/consultations", json={
        "stage": "piloting", "health": "yellow", "monthly_target": 2500,
        "next_action": "Run the free pilot with one Houston trade",
    })
    assert r.status_code == 200
    division = r.json()
    assert division["stage"] == "piloting"
    assert division["monthly_target"] == 2500
    assert division["next_action"].startswith("Run the free pilot")

    # and it persists
    assert client.get("/api/biz/divisions/consultations").json()["stage"] == "piloting"


def test_unknown_division_is_404_not_a_silent_empty(client):
    assert client.get("/api/biz/divisions/logistics").status_code == 404
    assert client.patch("/api/biz/divisions/logistics", json={"stage": "idea"}).status_code == 404


# --------------------------------------------------------------------------
# moving ventures between divisions
# --------------------------------------------------------------------------
def test_attach_and_detach_a_venture(client):
    assert client.put("/api/biz/divisions/ecommerce/ventures/trading").status_code == 200
    assert [v["id"] for v in
            client.get("/api/biz/divisions/ecommerce").json()["ventures"]] == ["trading"]

    assert client.delete("/api/biz/divisions/ecommerce/ventures/trading").status_code == 200
    assert client.get("/api/biz/divisions/ecommerce").json()["ventures"] == []
    assert client.get("/api/ventures/trading").json()["division_id"] is None


def test_attaching_moves_rather_than_duplicates(client):
    client.put("/api/biz/divisions/ecommerce/ventures/northwind")

    assert [v["id"] for v in
            client.get("/api/biz/divisions/ecommerce").json()["ventures"]] == ["northwind"]
    assert [v["id"] for v in
            client.get("/api/biz/divisions/electronics").json()["ventures"]] == [
        "electronics-export"
    ]


def test_detaching_leaves_the_ledger_alone(client):
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 400, "label": "parts lot"})
    client.delete("/api/biz/divisions/electronics/ventures/northwind")

    assert client.get("/api/ventures/northwind").json()["monthly_actual"] == 400
    assert len(client.get("/api/ventures/northwind/events").json()) == 1


def test_detaching_a_venture_from_the_wrong_division_is_404(client):
    assert client.delete(
        "/api/biz/divisions/ecommerce/ventures/northwind"
    ).status_code == 404
    # and it stayed where it was
    assert client.get("/api/ventures/northwind").json()["division_id"] == "electronics"


def test_attaching_to_an_unknown_division_is_404(client):
    assert client.put("/api/biz/divisions/logistics/ventures/northwind").status_code == 404


def test_attaching_an_unknown_venture_is_404(client):
    assert client.put("/api/biz/divisions/ecommerce/ventures/nope").status_code == 404


# --------------------------------------------------------------------------
# deals
# --------------------------------------------------------------------------
def _deal(api, **kw):
    """Open a deal. `api` rather than `client` — a deal has a client of its own."""
    payload = {"division_id": "consultations", "client": "Gulf Coast Plumbing"} | kw
    return api.post("/api/biz/deals", json=payload)


def test_a_deal_opens_as_a_lead_dated_today(client):
    deal = _deal(client, value=1500).json()
    assert deal["stage"] == "lead"
    assert deal["opened_on"] == date.today().isoformat()
    assert deal["closed_on"] is None


def test_a_deal_needs_a_client(client):
    assert _deal(client, client="   ").status_code == 422


def test_a_deal_needs_a_real_division(client):
    assert _deal(client, division_id="logistics").status_code == 404


def test_open_deals_are_pipeline_not_revenue(client):
    _deal(client, value=1500)
    _deal(client, value=800, stage="proposal")

    division = client.get("/api/biz/divisions/consultations").json()
    assert division["pipeline_value"] == 2300
    assert division["open_deals"] == 2
    # Pipeline is not money earned.
    assert division["monthly_actual"] == 0


def test_winning_a_deal_takes_it_out_of_pipeline_and_stamps_the_close(client):
    deal_id = _deal(client, value=1500).json()["id"]

    won = client.patch(f"/api/biz/deals/{deal_id}", json={"stage": "won"}).json()
    assert won["stage"] == "won"
    assert won["closed_on"] == date.today().isoformat()

    division = client.get("/api/biz/divisions/consultations").json()
    assert division["pipeline_value"] == 0
    assert division["open_deals"] == 0


def test_losing_a_deal_also_closes_it(client):
    deal_id = _deal(client, value=1500).json()["id"]
    lost = client.patch(f"/api/biz/deals/{deal_id}", json={"stage": "lost"}).json()
    assert lost["closed_on"] == date.today().isoformat()
    assert client.get("/api/biz/divisions/consultations").json()["pipeline_value"] == 0


def test_reopening_a_deal_clears_the_close_date(client):
    """closed_on must never outlive the close it describes."""
    deal_id = _deal(client, value=1500).json()["id"]
    client.patch(f"/api/biz/deals/{deal_id}", json={"stage": "won"})

    reopened = client.patch(f"/api/biz/deals/{deal_id}", json={"stage": "proposal"}).json()
    assert reopened["closed_on"] is None
    assert client.get("/api/biz/divisions/consultations").json()["open_deals"] == 1


def test_editing_a_deal_without_touching_stage_keeps_the_close_date(client):
    deal_id = _deal(client, value=1500).json()["id"]
    client.patch(f"/api/biz/deals/{deal_id}", json={"stage": "won"})
    closed_on = client.get(f"/api/biz/deals/{deal_id}").json()["closed_on"]

    bumped = client.patch(f"/api/biz/deals/{deal_id}", json={"value": 1800}).json()
    assert bumped["value"] == 1800
    assert bumped["closed_on"] == closed_on


def test_a_deal_opened_as_won_is_closed_on_arrival(client):
    deal = _deal(client, value=600, stage="won").json()
    assert deal["closed_on"] == deal["opened_on"]


def test_deals_filter_by_division_and_stage(client):
    _deal(client, value=1500)
    _deal(client, division_id="electronics", client="ITAD Houston", value=900, stage="proposal")

    consult = client.get("/api/biz/deals?division_id=consultations").json()
    assert [d["client"] for d in consult] == ["Gulf Coast Plumbing"]

    proposals = client.get("/api/biz/deals?stage=proposal").json()
    assert [d["client"] for d in proposals] == ["ITAD Houston"]


def test_open_only_excludes_closed_deals(client):
    _deal(client, value=1500)
    won_id = _deal(client, client="Closed Co", value=900).json()["id"]
    client.patch(f"/api/biz/deals/{won_id}", json={"stage": "won"})

    open_deals = client.get("/api/biz/deals?open_only=true").json()
    assert [d["client"] for d in open_deals] == ["Gulf Coast Plumbing"]


def test_deleting_a_deal(client):
    deal_id = _deal(client, value=1500).json()["id"]
    assert client.delete(f"/api/biz/deals/{deal_id}").status_code == 200
    assert client.get(f"/api/biz/deals/{deal_id}").status_code == 404
    assert client.delete(f"/api/biz/deals/{deal_id}").status_code == 404


def test_unknown_deal_is_404(client):
    assert client.get("/api/biz/deals/9999").status_code == 404
    assert client.patch("/api/biz/deals/9999", json={"value": 1}).status_code == 404


# --------------------------------------------------------------------------
# overview
# --------------------------------------------------------------------------
def test_overview_totals_across_divisions(client):
    client.patch("/api/biz/divisions/consultations", json={"monthly_target": 2500})
    client.patch("/api/biz/divisions/electronics", json={"monthly_target": 1500})
    client.post("/api/ventures/northwind/events",
                json={"kind": "revenue", "amount": 400, "label": "parts lot"})
    _deal(client, value=1500)

    overview = client.get("/api/biz").json()
    assert overview["total_target"] == 4000
    assert overview["total_actual"] == 400
    assert overview["attainment"] == 0.1
    assert overview["pipeline_value"] == 1500
    assert overview["open_deals"] == 1
    assert len(overview["divisions"]) == len(DIVISIONS)


def test_won_this_month_counts_only_closed_won_deals(client):
    won_id = _deal(client, value=1500).json()["id"]
    client.patch(f"/api/biz/deals/{won_id}", json={"stage": "won"})
    lost_id = _deal(client, client="Nope Inc", value=900).json()["id"]
    client.patch(f"/api/biz/deals/{lost_id}", json={"stage": "lost"})
    _deal(client, client="Still Open", value=700)

    assert client.get("/api/biz").json()["won_this_month"] == 1500


def test_overview_carries_the_unread_notice_count(client):
    client.post("/api/bridge", json={"kind": "notice", "title": "New lead from example.com"})
    assert client.get("/api/biz").json()["unread_notices"] == 1

    client.post("/api/bridge/read-all")
    assert client.get("/api/biz").json()["unread_notices"] == 0


# --------------------------------------------------------------------------
# freight broker page
# --------------------------------------------------------------------------
FREIGHT_LOG = ("Your Business", "projects", "freight-broker", "Freight-Broker-Progress-Log.md")


def test_freight_starts_with_nothing_checked_and_nothing_logged(client):
    t = client.get("/api/biz/freight").json()
    assert t["steps_done"] == 0 and t["steps_total"] > 20
    assert t["next_step"]["id"] == "p0-trucks"
    assert t["log"] == [] and t["daily"] == []
    assert t["totals"]["revenue"] == 0


def test_freight_step_check_uncheck_and_unknown(client):
    t = client.put("/api/biz/freight/steps/p0-trucks").json()
    assert t["steps_done"] == 1 and t["next_step"]["id"] == "p0-trial"
    t = client.put("/api/biz/freight/steps/p0-trucks?done=false").json()
    assert t["steps_done"] == 0
    assert client.put("/api/biz/freight/steps/nope").status_code == 404


def test_freight_log_and_daily_mirror_to_the_vault(client, vault):
    client.put("/api/biz/freight/steps/p1-llc")
    entry = client.post("/api/biz/freight/log", json={
        "day": "2026-09-22", "kind": "call", "title": "Called 3 Houston carriers", "amount": None}).json()
    assert entry["kind"] == "call" and entry["id"] > 0
    day = client.put("/api/biz/freight/daily/2026-09-22", json={
        "calls": 12, "loads_booked": 1, "revenue": 2400, "carrier_cost": 2000}).json()
    assert day["margin"] == 400

    note = vault.joinpath(*FREIGHT_LOG).read_text(encoding="utf-8")
    assert "- [x] Form the Texas LLC" in note
    assert "Called 3 Houston carriers" in note
    assert "| 2026-09-22 | 12 |" in note

    # Upsert replaces the day rather than adding a second row.
    client.put("/api/biz/freight/daily/2026-09-22", json={"calls": 5})
    t = client.get("/api/biz/freight").json()
    assert len(t["daily"]) == 1 and t["totals"]["calls"] == 5

    assert client.delete(f"/api/biz/freight/log/{entry['id']}").status_code == 204
    assert client.delete(f"/api/biz/freight/log/{entry['id']}").status_code == 404
    assert "Called 3 Houston carriers" not in vault.joinpath(*FREIGHT_LOG).read_text(encoding="utf-8")


def test_freight_rejects_bad_input(client):
    assert client.post("/api/biz/freight/log", json={"title": ""}).status_code == 422
    assert client.post("/api/biz/freight/log", json={"title": "x", "kind": "party"}).status_code == 422
    assert client.put("/api/biz/freight/daily/not-a-day", json={}).status_code == 422
    assert client.put("/api/biz/freight/daily/2026-09-22", json={"calls": -1}).status_code == 422


# --------------------------------------------------------------------------
# annotations — the register of standing accounts
# --------------------------------------------------------------------------
# The pipeline tests above cover CRUD, so these concentrate on the two rules
# that are easy to get wrong and quiet when they break: a re-run must not
# duplicate, and a re-run must not flatten what Blanco typed.
SWEEP = [
    {"company": "Twilio", "domain": "Twilio.com", "kind": "platform",
     "account_email": "Blanco@Example.com", "project": "10DLC registration",
     "status": "blocked", "last_seen": "2026-09-24"},
    {"company": "Render", "domain": "render.com", "kind": "platform",
     "account_email": "blanco@example.com", "last_seen": "2026-09-20"},
]


def test_annotations_start_empty(client):
    assert client.get("/api/biz/annotations").json() == []


def test_import_creates_then_is_idempotent(client):
    first = client.post("/api/biz/annotations/import", json=SWEEP).json()
    assert (first["created"], first["updated"], first["unchanged"]) == (2, 0, 0)

    second = client.post("/api/biz/annotations/import", json=SWEEP).json()
    assert (second["created"], second["updated"], second["unchanged"]) == (0, 0, 2)
    assert len(client.get("/api/biz/annotations").json()) == 2


def test_identity_is_case_folded(client):
    """"Twilio.com" and "twilio.com" are one account, not two.

    Mail casing is arbitrary, so without folding the unique index would let a
    second sweep register every company again under a different spelling.
    """
    client.post("/api/biz/annotations/import", json=SWEEP)
    row = client.get("/api/biz/annotations?search=Twilio").json()[0]
    assert row["domain"] == "twilio.com"
    assert row["account_email"] == "blanco@example.com"

    shouty = [{**SWEEP[0], "domain": "TWILIO.COM", "account_email": "BLANCO@EXAMPLE.COM"}]
    again = client.post("/api/biz/annotations/import", json=shouty).json()
    assert again["created"] == 0
    assert len(client.get("/api/biz/annotations").json()) == 2


def test_sweep_never_overwrites_blancos_judgement(client):
    """The whole point of the register: a re-sweep must not un-finish work."""
    client.post("/api/biz/annotations/import", json=SWEEP)
    row = client.get("/api/biz/annotations?search=Twilio").json()[0]

    client.patch(f"/api/biz/annotations/{row['id']}",
                 json={"status": "done", "project": "10DLC approved"})

    # The sweep still believes it is blocked, and says so again.
    client.post("/api/biz/annotations/import", json=SWEEP)
    after = client.get(f"/api/biz/annotations/{row['id']}").json()
    assert after["status"] == "done"
    assert after["project"] == "10DLC approved"
    # last_seen is a fact about the mailbox, not a judgement — it still moves.
    assert after["last_seen"] == "2026-09-24"


def test_sweep_fills_a_blank_field(client):
    """Preserving Blanco's edits must not mean never filling anything in."""
    client.post("/api/biz/annotations/import", json=[
        {"company": "Render", "account_email": "blanco@example.com"},
    ])
    row = client.get("/api/biz/annotations?search=Render").json()[0]
    assert row["project"] == ""

    client.post("/api/biz/annotations/import", json=[
        {"company": "Render", "account_email": "blanco@example.com",
         "project": "Hosting the pilot"},
    ])
    assert client.get(f"/api/biz/annotations/{row['id']}").json()["project"] == "Hosting the pilot"


def test_open_only_excludes_done_and_dormant(client):
    client.post("/api/biz/annotations/import", json=SWEEP)
    rows = client.get("/api/biz/annotations").json()
    client.patch(f"/api/biz/annotations/{rows[0]['id']}", json={"status": "done"})

    open_rows = client.get("/api/biz/annotations?open_only=true").json()
    assert all(r["status"] in ("active", "needs_finish", "blocked") for r in open_rows)
    assert len(open_rows) == len(rows) - 1


def test_blank_company_is_rejected(client):
    assert client.post("/api/biz/annotations", json={"company": "   "}).status_code == 422


def test_unknown_division_is_404(client):
    body = {"company": "Acme", "division_id": "no-such-division"}
    assert client.post("/api/biz/annotations", json=body).status_code == 404


def test_annotation_delete_and_404s(client):
    created = client.post("/api/biz/annotations", json={"company": "Acme"}).json()
    assert client.delete(f"/api/biz/annotations/{created['id']}").status_code == 200
    assert client.get(f"/api/biz/annotations/{created['id']}").status_code == 404
    assert client.delete(f"/api/biz/annotations/{created['id']}").status_code == 404


def test_a_cleared_field_stays_cleared(client):
    """Clearing a field is an edit, and a later sweep must respect it.

    The original rule was value-based — fill a blank, never overwrite a value —
    which silently inverted at the one moment it mattered: Blanco deleting
    something a sweep got wrong. The real Appen row came back with the word
    "available" as its project three times before this was pinned.
    """
    payload = [{"company": "Appen", "account_email": "blanco@example.com",
                "project": "available"}]
    client.post("/api/biz/annotations/import", json=payload)
    row = client.get("/api/biz/annotations?search=Appen").json()[0]
    assert row["project"] == "available"

    client.patch(f"/api/biz/annotations/{row['id']}", json={"project": ""})
    client.post("/api/biz/annotations/import", json=payload)
    assert client.get(f"/api/biz/annotations/{row['id']}").json()["project"] == ""


def test_pinning_one_field_leaves_the_others_sweepable(client):
    """Protecting an edit must not freeze the whole row."""
    client.post("/api/biz/annotations/import", json=[
        {"company": "Outlier", "account_email": "blanco@example.com"},
    ])
    row = client.get("/api/biz/annotations?search=Outlier").json()[0]
    client.patch(f"/api/biz/annotations/{row['id']}", json={"project": ""})

    client.post("/api/biz/annotations/import", json=[
        {"company": "Outlier", "account_email": "blanco@example.com",
         "project": "Queue B", "next_action": "Log in and check", "last_seen": "2026-09-25"},
    ])
    after = client.get(f"/api/biz/annotations/{row['id']}").json()
    assert after["project"] == ""                       # pinned
    assert after["next_action"] == "Log in and check"   # never touched, still fillable
    assert after["last_seen"] == "2026-09-25"
