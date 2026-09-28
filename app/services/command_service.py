"""The Command Deck — one call that fuses every module into a daily brief.

This is the endpoint the dashboard's home screen is built on: focus, the stat
tiles, a ranked "what to do next" list, and open alerts.
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime

from app import schemas
from app.config import get_settings
from app.services import (
    agents_service,
    bridge_service,
    certs_service,
    email_service,
    finance_service,
    history_service,
    journal_service,
    money_service,
    notify,
    store,
    systems_service,
    tasks_service,
)
from app.services.store import now_iso


def greeting(when: datetime | None = None) -> str:
    hour = (when or datetime.now()).hour
    name = get_settings().operator_name
    if hour < 5:
        return f"Still up, {name}"
    if hour < 12:
        return f"Morning, {name}"
    if hour < 17:
        return f"Afternoon, {name}"
    return f"Evening, {name}"


def sweep(
    conn: sqlite3.Connection, health: schemas.SystemsHealth | None = None
) -> list[schemas.Alert]:
    """Re-derive machine-detectable alerts. Idempotent via dedupe keys.

    Callers that already hold a health snapshot should pass it — probing is the
    single most expensive thing this service does.
    """
    health = health if health is not None else systems_service.health()
    for service in health.services:
        if service.optional:
            continue
        key = f"service-{service.id}"
        if service.status == "down":
            store.raise_alert(
                conn,
                schemas.AlertCreate(
                    source="systems",
                    severity="critical",
                    title=f"{service.name} is down",
                    detail=service.detail or service.target,
                    action_label="Open systems",
                    action_href="/systems",
                    dedupe_key=key,
                ),
            )
        elif service.status == "stale":
            store.raise_alert(
                conn,
                schemas.AlertCreate(
                    source="systems",
                    severity="warning",
                    title=f"{service.name} looks stale",
                    detail=service.detail,
                    action_label="Open systems",
                    action_href="/systems",
                    dedupe_key=key,
                ),
            )

    for disk in health.disks:
        # Windows passthrough mounts (/mnt/*) are managed from Windows, not WSL —
        # alerting on them daily is noise (Blanco's call 8/7). Watch real disks only.
        if disk.mount.startswith("/mnt/"):
            continue
        if disk.percent_used >= 90:
            store.raise_alert(
                conn,
                schemas.AlertCreate(
                    source="systems",
                    severity="warning",
                    title=f"Disk {disk.mount} at {disk.percent_used:.0f}%",
                    detail=f"{disk.free_gb} GB free of {disk.total_gb} GB",
                    dedupe_key=f"disk-{disk.mount}",
                ),
            )

    board = tasks_service.board()
    if board.counts.get("overdue"):
        store.raise_alert(
            conn,
            schemas.AlertCreate(
                source="tasks",
                severity="warning",
                title=f"{board.counts['overdue']} task(s) overdue",
                detail="Past their due date and not marked done.",
                action_label="Open tasks",
                action_href="/tasks",
                dedupe_key="tasks-overdue",
            ),
        )

    # One alert per overdue debt rather than a count: unlike overdue tasks,
    # each of these has a person's name on it and a different conversation
    # attached, so collapsing them loses the thing that makes them act-on-able.
    for debt in finance_service.overdue_debts(conn):
        store.raise_alert(
            conn,
            schemas.AlertCreate(
                source="finance",
                severity="warning",
                title=f"{debt.creditor} is {debt.days_overdue} day(s) overdue",
                detail=f"${debt.balance:,.2f} still owed, was due {debt.due_date}.",
                action_label="Open finance",
                action_href="/finance",
                dedupe_key=f"debt-overdue-{debt.id}",
            ),
        )

    journal = journal_service.overview()
    if journal.current_streak == 0 and journal.entry_count:
        store.raise_alert(
            conn,
            schemas.AlertCreate(
                source="journal",
                severity="info",
                title="Journal streak broken",
                detail=f"Last entry {journal.last_entry_date}.",
                action_label="Write today's entry",
                action_href="/journal",
                dedupe_key="journal-streak",
            ),
        )

    alerts = store.list_alerts(conn)
    # Anything worth waking him for goes out now; notify() dedupes so a standing
    # alert is announced once, not on every sweep.
    notify.announce_alerts(conn, alerts)
    return alerts


def _metrics(conn: sqlite3.Connection) -> list[schemas.Metric]:
    board = tasks_service.board()
    money = money_service.overview(conn)
    finance = finance_service.overview(conn)
    certs = certs_service.track(conn)
    journal = journal_service.overview()
    agents = agents_service.roster()

    open_tasks = board.counts["total"] - board.counts["done"]
    overdue = board.counts["overdue"]

    has_target = money.total_target > 0
    staged = [v for v in money.by_venture if v.stage != "unset"]

    return [
        schemas.Metric(
            key="income_attainment",
            label="Income vs target",
            value=round(money.total_actual, 2),
            unit="USD",
            target=round(money.total_target, 2) if has_target else None,
            health=(
                ("green" if money.attainment >= 0.8 else "yellow" if money.attainment >= 0.3 else "red")
                if has_target
                else "unknown"
            ),
            trend="unknown",
            hint=(
                f"{money.attainment * 100:.0f}% of the ${money.total_target:,.0f}/mo goal"
                if has_target
                else "No monthly targets set yet — set them per venture"
            ),
        ),
        schemas.Metric(
            key="ventures_earning",
            label="Ventures earning",
            value=sum(1 for v in money.by_venture if v.stage == "earning"),
            target=len(money.by_venture),
            health="green" if any(v.stage == "earning" for v in money.by_venture) else "unknown",
            hint=(
                f"{len(staged)} of {len(money.by_venture)} streams have a stage set"
                if len(staged) < len(money.by_venture)
                else "Diversification is the north star"
            ),
        ),
        schemas.Metric(
            key="debt_total",
            label="Debt outstanding",
            value=round(finance.total_debt, 2),
            unit="USD",
            # Zero is the goal, so the target is the number to reach rather
            # than a ceiling — the deck renders "$7,850 → $0", not a fraction.
            target=0,
            health=(
                "green" if finance.total_debt <= 0
                else "red" if finance.overdue_debts
                else "yellow"
            ),
            hint=(
                finance.next_move.headline
                if finance.total_debt > 0
                else "Debt free. Keep it that way."
            ),
        ),
        schemas.Metric(
            key="open_tasks",
            label="Open tasks",
            value=open_tasks,
            health="red" if overdue else "green" if open_tasks < 8 else "yellow",
            hint=f"{overdue} overdue · {board.counts['due_today']} due today",
        ),
        schemas.Metric(
            key="cert_progress",
            label="Cert track",
            value=certs.percent_complete,
            unit="%",
            target=100,
            health="green" if certs.percent_complete >= 50 else "yellow",
            hint=f"{certs.counts.get('passed', 0)}/{certs.counts.get('total', 0)} passed",
        ),
        schemas.Metric(
            key="journal_streak",
            label="Journal streak",
            value=journal.current_streak,
            unit="days",
            target=max(journal.longest_streak, 7),
            health="green" if journal.current_streak >= 3 else "yellow" if journal.current_streak else "red",
            hint=f"best {journal.longest_streak}d · mood {journal.average_score_30d}/5 (30d)",
        ),
        schemas.Metric(
            key="agents_online",
            label="Agents online",
            value=agents.online,
            target=agents.total,
            health="green" if agents.online else "red",
            hint=f"{agents.standby} on standby · {agents.scheduled} scheduled · {agents.total} in the fleet",
        ),
    ]


# Importance 4 and up. The categoriser reserves that band for mail a person
# actually sent, real contracts and offers, money, and security — automated
# job-match digests and marketing are capped below it on purpose.
MAIL_DECK_THRESHOLD = 4


def _important_mail(conn: sqlite3.Connection, limit: int = 4) -> list[schemas.EmailMessage]:
    """Unread mail worth interrupting him for.

    Gmail is a network call on someone else's uptime, and the brief has to
    render regardless — a mail outage must not take the home screen down, so
    this degrades to an empty list rather than raising.
    """
    try:
        inbox = email_service.fetch(conn, limit=25, unread_only=True)
    except Exception:
        return []
    important = [m for m in inbox.messages if m.importance >= MAIL_DECK_THRESHOLD]
    important.sort(key=lambda m: m.importance, reverse=True)
    return important[:limit]


def _now_next(conn: sqlite3.Connection, limit: int = 8) -> list[schemas.BriefItem]:
    """Rank what deserves attention across every module."""
    today = date.today().isoformat()
    items: list[tuple[int, schemas.BriefItem]] = []

    board = tasks_service.board()
    for task in board.tasks:
        if task.status == "done":
            continue
        overdue = bool(task.due_date and task.due_date < today)
        due_today = task.due_date == today
        rank = 0 if overdue else 1 if due_today else 2 if task.priority == "high" else 5
        items.append(
            (
                rank,
                schemas.BriefItem(
                    kind="task",
                    title=task.title,
                    detail=("overdue" if overdue else "due today" if due_today else task.priority),
                    due=task.due_date,
                    href=f"/tasks/{task.id}",
                    urgency="critical" if overdue else "warning" if due_today else "info",
                ),
            )
        )

    for event in tasks_service.timeline()[:5]:
        rank = 1 if event.date == today else 4
        items.append(
            (
                rank,
                schemas.BriefItem(
                    kind="event",
                    title=event.title,
                    detail=f"{event.date} {event.time}".strip(),
                    due=event.date,
                    href="/calendar",
                    urgency="warning" if event.date == today else "info",
                ),
            )
        )

    for venture in money_service.list_ventures(conn):
        if not venture.next_action or venture.stage in ("paused", "archived"):
            continue
        items.append(
            (
                3 if venture.health == "red" else 6,
                schemas.BriefItem(
                    kind="venture",
                    title=venture.next_action,
                    detail=f"{venture.emoji} {venture.name}",
                    href=f"/money/{venture.id}",
                    urgency="warning" if venture.health == "red" else "info",
                ),
            )
        )

    # Mail that a person actually sent. The categoriser rates automated
    # job-match digests and marketing at 1-2 and reserves 4-5 for direct human
    # mail, real contracts and offers, so the cutoff here is what keeps the
    # deck from filling up with recruiter spam.
    for mail in _important_mail(conn):
        items.append(
            (
                0 if mail.importance >= 5 else 2,
                schemas.BriefItem(
                    kind="mail",
                    title=mail.subject or "(no subject)",
                    detail=f"{mail.sender} · {mail.category}",
                    href="/email",
                    urgency="critical" if mail.importance >= 5 else "warning",
                ),
            )
        )

    # The ONE debt move, ranked alongside everything else rather than pinned:
    # it is a standing priority, not an emergency, so an overdue task or a real
    # human email should still outrank it on any given morning.
    plan = finance_service.payoff_plan(conn)
    if plan.recommendation.target_debt_id is not None:
        items.append(
            (
                3 if plan.total_debt else 6,
                schemas.BriefItem(
                    kind="debt",
                    title=plan.recommendation.headline,
                    detail=f"${plan.total_debt:,.2f} across {plan.debt_count} debt(s)",
                    href="/finance",
                    urgency="info",
                ),
            )
        )

    unread = bridge_service.unread_count(conn)
    if unread:
        items.append(
            (
                2,
                schemas.BriefItem(
                    kind="bridge",
                    title=f"{unread} unread from Your Business",
                    detail="Work OS pushed notices across the bridge",
                    href="/bridge",
                    urgency="warning",
                ),
            )
        )

    items.sort(key=lambda pair: (pair[0], pair[1].due or "9999-12-31"))
    return [item for _, item in items[:limit]]


def brief(conn: sqlite3.Connection, run_sweep: bool = True) -> schemas.DailyBrief:
    # One snapshot, shared with the sweep — this used to probe twice per brief.
    health = systems_service.health()
    if run_sweep:
        sweep(conn, health)

    agents = agents_service.roster()
    metrics = history_service.apply_trends(conn, _metrics(conn))

    return schemas.DailyBrief(
        generated_at=now_iso(),
        day=date.today().isoformat(),
        greeting=greeting(),
        focus=store.get_focus(conn),
        metrics=metrics,
        now_next=_now_next(conn),
        alerts=store.list_alerts(conn),
        bridge_unread=bridge_service.unread_count(conn),
        systems_health=health.overall,
        agents_online=agents.online,
        agents_total=agents.total,
    )
