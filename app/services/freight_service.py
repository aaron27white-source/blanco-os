"""Your Business · Freight Broker — the roadmap, the running log, the daily numbers.

The steps come straight from the playbook in the vault
(`Your Business/projects/freight-broker/`), phase by phase. Blanco's
progress is the only thing stored. Nothing is pre-checked and no number is
seeded: an empty log means nothing has been logged yet.

Every write also regenerates `Freight-Broker-Progress-Log.md` in the vault.
That file is how Haul 🚚, the freight sub-agent, sees what Blanco has actually
done, because Haul reads the vault rather than this database.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

from app import schemas
from app.config import get_settings

log = logging.getLogger(__name__)

VAULT_DIR = ("Your Business", "projects", "freight-broker")
VAULT_LOG = "Freight-Broker-Progress-Log.md"

# (phase id, title, when, vault note, [(step id, label, hint)])
STEPS: list[tuple[str, str, str, str, list[tuple[str, str, str]]]] = [
    ("phase0", "Phase 0 — Dispatcher First", "Months 1–3", "Freight-00-Phase-0-Dispatcher", [
        ("p0-trucks", "Land 1–3 owner-operators or a small carrier to dispatch for",
         "Facebook groups and small Houston carriers with 1–5 trucks. Typical pay is 5–10% of gross per truck."),
        ("p0-trial", "Run a trial week to prove your rate negotiation",
         "Free or at a reduced rate. Proof beats pitch."),
        ("p0-filing", "Start the LLC / OP-1 filing (month 2–3 at the latest)",
         "Start it regardless of how dispatching is going, so there's no income gap."),
        ("p0-exit", "Exit dispatching by the end of month 3",
         "The trucks you dispatched become your first warm carriers."),
    ]),
    ("phase1", "Phase 1 — Get Licensed", "Weeks 1–8", "Freight-02-Phase-1-Licensing", [
        ("p1-llc", "Form the Texas LLC", "Texas SOS online filing. Confirm the current fee before you file."),
        ("p1-ein", "Get the EIN", "Free and instant from the IRS."),
        ("p1-bank", "Open the business bank account", "Keep it separate from personal money from day one."),
        ("p1-credit", "Pull your credit report and fix anything glaring", "Your score sets the bond premium."),
        ("p1-op1", "File FMCSA OP-1 and get the MC number", "Through the Unified Registration System."),
        ("p1-bond", "BMC-84 bond: get quotes from 3+ providers, then file it", "$75,000 coverage. The surety files it with FMCSA."),
        ("p1-boc3", "File BOC-3 (process agent)", "Use a blanket service that covers all 50 states."),
        ("p1-ucr", "Register UCR", "The smallest bracket."),
        ("p1-active", "Authority is active", "Roughly 4–6 weeks of processing, then a 10-day protest period."),
    ]),
    ("phase2", "Phase 2 — Set Up Shop", "Weeks 6–12", "Freight-03-Phase-2-Setup", [
        ("p2-tms", "Pick a TMS (or a solid spreadsheet for the first 20 loads)", ""),
        ("p2-board", "Subscribe to ONE load board (DAT or Truckstop)", "DAT has more volume."),
        ("p2-vetting", "Set up carrier vetting: SAFER plus a vetting service", "MyCarrierPackets or Highway once you scale."),
        ("p2-carrier-agmt", "Broker–carrier agreement template locked", "Start from TIA's standard templates."),
        ("p2-shipper-agmt", "Shipper agreement template locked", ""),
        ("p2-factoring", "Factoring option lined up", "It bridges carrier net-15 against shipper net-30."),
    ]),
    ("carriers", "Carrier Network", "Months 3–6", "Freight-04-Carrier-Network", [
        ("c-first5", "First 5 carrier relationships locked", "Backhauls out of Houston are the cheapest capacity."),
        ("c-core10", "\"Core 10\" carriers established", "10 carriers who pick up when you call will cover about 70% of loads."),
    ]),
    ("loads", "Shippers & Loads", "Months 3–6", "Freight-05-Shipper-Hunt", [
        ("s-subbroker", "First 3PL / forwarder overflow partner", "The fastest way to paid loads with no shipper book of your own."),
        ("s-firstload", "First load booked, papered and paid", "Rate con, BOL, POD, invoice. See the first-load walkthrough."),
        ("s-direct", "First direct shipper contract", "Ask to be their backup broker, not their only one."),
        ("s-reserve", "30 days of carrier payments in reserve", "Build this before you scale volume."),
    ]),
    ("customs", "Phase 3 — Customs Broker", "Months 6–10", "Freight-11-Phase-3-Customs-Broker", [
        ("x-volume", "Freight at $9K–14K/month, consistently", "Only then does the customs track start."),
        ("x-course", "Enrol in a customs exam prep course", "Historical pass rate is low, so don't self-study alone."),
        ("x-exam", "Pass the Customs Broker License Exam", "CBP offers it twice a year. Confirm the current dates."),
        ("x-license", "Apply for the individual broker license", ""),
        ("x-entries", "First customs entries for existing shippers", "Start with shippers who already trust you and import from Mexico."),
    ]),
    ("automation", "Automation — the Your Business Product", "Month 6+", "Freight-12-Houston-AI-Automation", [
        ("a-vetting", "Carrier vetting bot", "Auto-check SAFER and insurance before you approve a carrier."),
        ("a-quoting", "Rate-quoting assistant", "Only after about 100 loads quoted by hand."),
        ("a-tracking", "Load tracking + status updates", "The most time-consuming manual task in the business."),
        ("a-customs", "Customs document pre-fill", "HTS classification from the shipper's invoice."),
    ]),
]
STEP_IDS = {s[0] for p in STEPS for s in p[4]}
DAILY_FIELDS = ("calls", "carriers_added", "shippers_contacted", "loads_booked",
                "revenue", "carrier_cost", "notes")


def _today() -> str:
    return date.today().isoformat()


def _done(conn: sqlite3.Connection) -> dict[str, str]:
    return {r["step_id"]: r["done_at"] for r in conn.execute("SELECT step_id, done_at FROM freight_steps_done")}


def _phases(conn: sqlite3.Connection) -> list[schemas.FreightPhase]:
    done = _done(conn)
    out = []
    for pid, title, when, note, steps in STEPS:
        items = [schemas.FreightStep(id=sid, label=label, hint=hint,
                                     done=sid in done, done_at=done.get(sid))
                 for sid, label, hint in steps]
        out.append(schemas.FreightPhase(id=pid, title=title, when=when, note=note, steps=items,
                                        done=sum(i.done for i in items), total=len(items)))
    return out


def _entry(row: sqlite3.Row) -> schemas.FreightLogEntry:
    return schemas.FreightLogEntry(**{k: row[k] for k in row.keys()})


def _daily(row: sqlite3.Row) -> schemas.FreightDaily:
    d = {k: row[k] for k in row.keys() if k != "updated_at"}
    return schemas.FreightDaily(**d, margin=round(d["revenue"] - d["carrier_cost"], 2))


def list_log(conn: sqlite3.Connection, limit: int = 100) -> list[schemas.FreightLogEntry]:
    rows = conn.execute("SELECT * FROM freight_log ORDER BY day DESC, id DESC LIMIT ?", (limit,))
    return [_entry(r) for r in rows]


def list_daily(conn: sqlite3.Connection, limit: int = 30) -> list[schemas.FreightDaily]:
    rows = conn.execute("SELECT * FROM freight_daily ORDER BY day DESC LIMIT ?", (limit,))
    return [_daily(r) for r in rows]


def _totals(conn: sqlite3.Connection) -> schemas.FreightDaily:
    r = conn.execute(
        "SELECT COALESCE(SUM(calls),0) c, COALESCE(SUM(carriers_added),0) ca,"
        " COALESCE(SUM(shippers_contacted),0) s, COALESCE(SUM(loads_booked),0) l,"
        " COALESCE(SUM(revenue),0) r, COALESCE(SUM(carrier_cost),0) cc FROM freight_daily").fetchone()
    return schemas.FreightDaily(day="all", calls=r["c"], carriers_added=r["ca"], shippers_contacted=r["s"],
                                loads_booked=r["l"], revenue=r["r"], carrier_cost=r["cc"],
                                margin=round(r["r"] - r["cc"], 2))


def tracker(conn: sqlite3.Connection) -> schemas.FreightTracker:
    phases = _phases(conn)
    nxt, nxt_phase = None, None
    for p in phases:
        step = next((s for s in p.steps if not s.done), None)
        if step:
            nxt, nxt_phase = step, p.title
            break
    return schemas.FreightTracker(
        phases=phases, next_step=nxt, next_phase=nxt_phase,
        steps_done=sum(p.done for p in phases), steps_total=sum(p.total for p in phases),
        log=list_log(conn), daily=list_daily(conn), totals=_totals(conn),
        vault_log="/".join((*VAULT_DIR, VAULT_LOG)),
    )


# --- writes -----------------------------------------------------------------
def set_step(conn: sqlite3.Connection, step_id: str, done: bool) -> bool:
    if step_id not in STEP_IDS:
        return False
    if done:
        conn.execute("INSERT OR IGNORE INTO freight_steps_done (step_id) VALUES (?)", (step_id,))
    else:
        conn.execute("DELETE FROM freight_steps_done WHERE step_id = ?", (step_id,))
    conn.commit()
    mirror_to_vault(conn)
    return True


def add_log(conn: sqlite3.Connection, payload: schemas.FreightLogCreate) -> schemas.FreightLogEntry:
    cur = conn.execute(
        "INSERT INTO freight_log (day, kind, title, detail, amount) VALUES (?, ?, ?, ?, ?)",
        (payload.day or _today(), payload.kind, payload.title.strip(), payload.detail.strip(), payload.amount))
    conn.commit()
    mirror_to_vault(conn)
    return _entry(conn.execute("SELECT * FROM freight_log WHERE id = ?", (cur.lastrowid,)).fetchone())


def delete_log(conn: sqlite3.Connection, entry_id: int) -> bool:
    gone = conn.execute("DELETE FROM freight_log WHERE id = ?", (entry_id,)).rowcount > 0
    conn.commit()
    if gone:
        mirror_to_vault(conn)
    return gone


def upsert_daily(conn: sqlite3.Connection, day: str, payload: schemas.FreightDailyUpsert) -> schemas.FreightDaily:
    values = [getattr(payload, f) for f in DAILY_FIELDS]
    conn.execute(
        f"INSERT INTO freight_daily (day, {', '.join(DAILY_FIELDS)}) VALUES (?{', ?' * len(DAILY_FIELDS)})"
        f" ON CONFLICT(day) DO UPDATE SET {', '.join(f'{f} = excluded.{f}' for f in DAILY_FIELDS)},"
        " updated_at = datetime('now')",
        (day, *values))
    conn.commit()
    mirror_to_vault(conn)
    return _daily(conn.execute("SELECT * FROM freight_daily WHERE day = ?", (day,)).fetchone())


def delete_daily(conn: sqlite3.Connection, day: str) -> bool:
    gone = conn.execute("DELETE FROM freight_daily WHERE day = ?", (day,)).rowcount > 0
    conn.commit()
    if gone:
        mirror_to_vault(conn)
    return gone


# --- the vault mirror Haul reads ---------------------------------------------
def _money(v: float | None) -> str:
    return "" if v is None else f"${v:,.0f}"


def render_markdown(conn: sqlite3.Connection) -> str:
    t = tracker(conn)
    lines = [
        "---",
        "tags: [venture/freight-broker, key-20, log, generated]",
        "links: [\"[[Freight-Broker-Playbook]]\", \"[[Freight-Broker-Build-Log]]\"]",
        "---",
        "",
        "# 🚚 Freight Broker — Progress Log",
        "",
        "> Generated by Blanco OS from the Freight Broker page. Do not edit by hand:",
        "> the next change on the page overwrites it. Haul reads this to see what Blanco has actually done.",
        "",
        f"**Roadmap:** {t.steps_done}/{t.steps_total} steps done.",
    ]
    if t.next_step:
        lines.append(f"**Next step:** {t.next_step.label} ({t.next_phase})")
    lines += ["", "## Roadmap", ""]
    for p in t.phases:
        lines.append(f"### {p.title} · {p.when} · {p.done}/{p.total} — [[{p.note}]]")
        for s in p.steps:
            stamp = f" _(done {s.done_at[:10]})_" if s.done and s.done_at else ""
            lines.append(f"- [{'x' if s.done else ' '}] {s.label}{stamp}")
        lines.append("")
    lines += ["## Daily numbers (last 30 days)", ""]
    if t.daily:
        lines += ["| Day | Calls | Carriers + | Shippers | Loads | Revenue | Carrier cost | Margin | Notes |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for d in t.daily:
            lines.append(f"| {d.day} | {d.calls} | {d.carriers_added} | {d.shippers_contacted} | {d.loads_booked}"
                         f" | {_money(d.revenue)} | {_money(d.carrier_cost)} | {_money(d.margin)}"
                         f" | {d.notes.replace('|', '/').replace(chr(10), ' ')} |")
        tt = t.totals
        lines.append(f"| **All time** | {tt.calls} | {tt.carriers_added} | {tt.shippers_contacted} | {tt.loads_booked}"
                     f" | {_money(tt.revenue)} | {_money(tt.carrier_cost)} | {_money(tt.margin)} | |")
    else:
        lines.append("_No daily numbers logged yet._")
    lines += ["", "## Log (newest first)", ""]
    if t.log:
        for e in t.log:
            amt = f" · {_money(e.amount)}" if e.amount is not None else ""
            lines.append(f"- **{e.day}** `{e.kind}` {e.title}{amt}")
            if e.detail:
                lines += [f"  {line}" for line in e.detail.splitlines()]
    else:
        lines.append("_Nothing logged yet._")
    return "\n".join(lines) + "\n"


def mirror_to_vault(conn: sqlite3.Connection) -> None:
    """Best effort. A vault that can't be written must not fail the write
    Blanco just made on the page, because the database already has it."""
    try:
        folder = get_settings().vault_path.joinpath(*VAULT_DIR)
        folder.mkdir(parents=True, exist_ok=True)
        (folder / VAULT_LOG).write_text(render_markdown(conn), encoding="utf-8")
    except OSError:
        log.warning("could not mirror the freight log to the vault", exc_info=True)
