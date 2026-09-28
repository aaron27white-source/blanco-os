"""Response/request contracts for Blanco OS.

These model names show up verbatim in /openapi.json, which is what the UI is
designed against — rename with care.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

Severity = Literal["critical", "warning", "info"]
Health = Literal["green", "yellow", "red", "unknown"]
# "unset" is the honest default: the OS does not guess what stage a venture is
# at, or what it should earn — Blanco sets that.
Stage = Literal["unset", "idea", "building", "piloting", "earning", "paused", "archived"]
CertStatus = Literal["not_started", "in_progress", "passed", "failed", "dropped"]
TaskStatus = Literal["todo", "in_progress", "blocked", "done"]
Priority = Literal["high", "medium", "low"]

Bureau = Literal["Equifax", "Experian", "TransUnion"]
ScoreType = Literal["fico8", "fico9", "vantage3", "auto", "mortgage", "bankcard"]
# A is real credit an underwriter weights fully; B is fintech synthetic, which
# moves FICO but reads as a manufactured file when it dominates. The build
# playbook caps B at two accounts.
Bucket = Literal["A", "B"]
AccountType = Literal[
    "secured_card", "unsecured_card", "installment", "authorized_user",
    "store_card", "loan", "rent_reporting", "net30_vendor",
]
DisputeCategory = Literal["name", "address", "employer", "tradeline", "inquiry"]
DisputeOutcome = Literal["pending", "removed", "verified", "updated", "no_response"]

DebtStatus = Literal["outstanding", "partial", "paid"]
InvestmentType = Literal["brokerage", "retirement", "crypto", "cash"]
# "debt_payment" is its own kind rather than an expense category: the monthly
# summary has to answer "how much went at the debt" separately from "how much
# did I spend", and a category string would let a typo lose the distinction.
TransactionKind = Literal["income", "expense", "debt_payment"]
PayoffStrategy = Literal["avalanche", "snowball"]
NoteCategory = Literal["account", "card", "loan", "subscription", "contact", "tax", "general"]


# --------------------------------------------------------------------------
# meta
# --------------------------------------------------------------------------
class ModuleInfo(BaseModel):
    """One tile/route in the OS shell. The UI builds its nav from this list."""

    id: str
    name: str
    emoji: str
    tagline: str
    api_prefix: str
    writable: bool
    source: str = Field(description="Where the underlying data actually lives")


class SystemInfo(BaseModel):
    name: str
    version: str
    operator: str
    handle: str
    timezone: str
    schema_version: int
    vault_path: str
    workspace_path: str
    server_time: str
    modules: list[ModuleInfo]


# --------------------------------------------------------------------------
# command deck
# --------------------------------------------------------------------------
class Focus(BaseModel):
    headline: str
    detail: str = ""
    horizon: Literal["today", "week", "quarter"] = "today"
    set_at: str | None = None
    source: Literal["os", "active_md", "none"] = "none"


class FocusUpdate(BaseModel):
    headline: str
    detail: str = ""
    horizon: Literal["today", "week", "quarter"] = "today"


class Alert(BaseModel):
    id: int
    source: str
    severity: Severity
    title: str
    detail: str = ""
    action_label: str = ""
    action_href: str = ""
    acknowledged: bool = False
    created_at: str


class AlertCreate(BaseModel):
    source: str
    severity: Severity = "info"
    title: str
    detail: str = ""
    action_label: str = ""
    action_href: str = ""
    dedupe_key: str | None = None


class Metric(BaseModel):
    """A single headline number for a stat tile."""

    key: str
    label: str
    value: float
    unit: str = ""
    target: float | None = None
    trend: Literal["up", "down", "flat", "unknown"] = "unknown"
    health: Health = "unknown"
    hint: str = ""


class MetricPoint(BaseModel):
    day: str
    value: float
    target: float | None = None
    health: Health = "unknown"


class MetricSeries(BaseModel):
    metric_key: str
    points: list[MetricPoint]


class NotifyResult(BaseModel):
    sent: dict[str, str] = Field(description="channel -> outcome")


class NotifyTest(BaseModel):
    title: str = "Blanco OS test"
    body: str = "If you can read this, notifications work."
    severity: Severity = "info"


class BriefItem(BaseModel):
    kind: Literal["task", "event", "cert", "venture", "system", "bridge", "mail", "debt"]
    title: str
    detail: str = ""
    due: str | None = None
    href: str = ""
    urgency: Severity = "info"


class DailyBrief(BaseModel):
    generated_at: str
    day: str
    greeting: str
    focus: Focus
    metrics: list[Metric]
    now_next: list[BriefItem] = Field(description="What to do next, ranked")
    alerts: list[Alert]
    bridge_unread: int
    systems_health: Health
    agents_online: int
    agents_total: int


# --------------------------------------------------------------------------
# tasks
# --------------------------------------------------------------------------
class Subtask(BaseModel):
    """One step inside a task. "Set up Fiverr profile" is a task; "write the
    bio", "shoot the cover image", "publish gig #1" are its subtasks."""

    id: str
    title: str
    done: bool = False


def _required_text(value: str | None) -> str | None:
    """Trim, and refuse whitespace. Length alone lets "   " through, which
    stores a step with no visible title and no way to click it."""
    if value is None:
        return None
    trimmed = value.strip()
    if not trimmed:
        raise ValueError("must not be blank")
    return trimmed


class SubtaskCreate(BaseModel):
    title: str = Field(max_length=300)

    _trim = field_validator("title")(_required_text)


class SubtaskUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    done: bool | None = None

    _trim = field_validator("title")(_required_text)


class Task(BaseModel):
    id: str
    title: str
    status: TaskStatus = "todo"
    priority: Priority = "medium"
    due_date: str | None = None
    notes: str = ""
    agent: bool = Field(default=False, description="Proposed by an agent, not by Blanco")
    agent_reason: str = ""
    subtasks: list[Subtask] = Field(
        default_factory=list, description="Steps, in the order they were added"
    )


class TaskCreate(BaseModel):
    title: str
    status: TaskStatus = "todo"
    priority: Priority = "medium"
    due_date: str | None = None
    notes: str = ""
    agent: bool = False
    agent_reason: str = ""


class TaskUpdate(BaseModel):
    title: str | None = None
    status: TaskStatus | None = None
    priority: Priority | None = None
    due_date: str | None = None
    notes: str | None = None


class ScheduledEvent(BaseModel):
    id: str
    title: str
    date: str
    time: str = ""
    agent: bool = False
    reason: str = ""


class EventCreate(BaseModel):
    title: str
    date: str
    time: str = ""
    agent: bool = False
    reason: str = ""


class EventUpdate(BaseModel):
    title: str | None = None
    date: str | None = None
    time: str | None = None


class QuickNote(BaseModel):
    id: str
    text: str
    date: str


class NoteCreate(BaseModel):
    text: str


class TaskBoard(BaseModel):
    last_synced: str | None
    source_file: str
    tasks: list[Task]
    events: list[ScheduledEvent]
    notes: list[QuickNote]
    counts: dict[str, int]


# --------------------------------------------------------------------------
# notepad
# --------------------------------------------------------------------------
NotepadColor = Literal["default", "red", "amber", "green", "blue", "violet"]


class NotepadNote(BaseModel):
    id: int
    title: str
    body: str
    pinned: bool = False
    color: NotepadColor = "default"
    created_at: str
    updated_at: str
    # First non-empty line of the body, for the list rail — so the client never
    # has to hold every note's full text just to draw the index.
    preview: str = ""
    chars: int = 0


class NotepadNoteCreate(BaseModel):
    title: str = ""
    body: str = ""
    pinned: bool = False
    color: NotepadColor = "default"


class NotepadNoteUpdate(BaseModel):
    title: str | None = None
    body: str | None = None
    pinned: bool | None = None
    color: NotepadColor | None = None
    # Optimistic concurrency: the `updated_at` the editor last saw. The deck
    # panel and the detached window can hold the same note open, and without
    # this the slower typist's save silently erases the faster one's. Omit it
    # and the write is an unconditional last-write-wins.
    base_updated_at: str | None = None


class NotepadOverview(BaseModel):
    notes: list[NotepadNote]
    count: int


# --------------------------------------------------------------------------
# certs
# --------------------------------------------------------------------------
class Cert(BaseModel):
    slug: str
    name: str
    phase: str
    cost: str = ""
    duration: str = ""
    status: CertStatus = "not_started"
    percent: int = 0
    started_on: str | None = None
    finished_on: str | None = None
    note: str = ""
    overridden: bool = Field(default=False, description="Status came from the OS, not the markdown")


class CertProgressUpdate(BaseModel):
    status: CertStatus | None = None
    percent: int | None = Field(default=None, ge=0, le=100)
    started_on: str | None = None
    finished_on: str | None = None
    note: str | None = None


class CertTrack(BaseModel):
    title: str
    source_file: str
    updated_at: str | None
    percent_complete: int
    counts: dict[str, int]
    certs: list[Cert]


# --------------------------------------------------------------------------
# money / ventures
# --------------------------------------------------------------------------
class Venture(BaseModel):
    id: str
    name: str
    emoji: str
    thesis: str
    stage: Stage
    health: Health
    monthly_target: float
    monthly_actual: float
    capital_in: float
    next_action: str
    vault_path: str
    repo_path: str
    attainment: float = Field(description="monthly_actual / monthly_target, 0 when no target")
    division_id: str | None = Field(
        default=None,
        description="Your Business division this stream belongs to, or null if it is not Your Business work",
    )
    updated_at: str


class VentureUpdate(BaseModel):
    name: str | None = None
    thesis: str | None = None
    stage: Stage | None = None
    health: Health | None = None
    monthly_target: float | None = None
    monthly_actual: float | None = None
    capital_in: float | None = None
    next_action: str | None = None
    # division_id is deliberately absent. update_venture() drops None fields,
    # so a nullable column here could be attached but never detached. Moving a
    # venture between divisions goes through the explicit
    # PUT/DELETE /api/biz/divisions/{id}/ventures/{venture_id} routes.


class VentureEvent(BaseModel):
    id: int
    venture_id: str
    kind: Literal["revenue", "expense", "milestone", "note"]
    amount: float
    label: str
    occurred_on: str


class VentureEventCreate(BaseModel):
    kind: Literal["revenue", "expense", "milestone", "note"] = "revenue"
    amount: float = 0
    label: str
    occurred_on: str | None = None


class MoneyOverview(BaseModel):
    month: str
    total_target: float
    total_actual: float
    attainment: float
    revenue_mtd: float
    expenses_mtd: float
    net_mtd: float
    capital_deployed: float
    by_venture: list[Venture]
    recent_events: list[VentureEvent]


class AiService(BaseModel):
    id: int
    name: str
    provider: str
    kind: Literal["api", "subscription", "tool", "infra"]
    billing: Literal["monthly", "annual", "usage", "free"]
    cost: float
    currency: str
    is_active: bool
    billing_email: str
    notes: str
    monthly_cost: float = Field(
        description="Normalised to a month — annual plans divided by 12, so the total is comparable"
    )
    updated_at: str


class AiServiceCreate(BaseModel):
    name: str
    provider: str = ""
    kind: Literal["api", "subscription", "tool", "infra"] = "api"
    billing: Literal["monthly", "annual", "usage", "free"] = "usage"
    cost: float = 0
    billing_email: str = ""
    notes: str = ""


class AiServiceUpdate(BaseModel):
    provider: str | None = None
    kind: Literal["api", "subscription", "tool", "infra"] | None = None
    billing: Literal["monthly", "annual", "usage", "free"] | None = None
    cost: float | None = None
    is_active: bool | None = None
    notes: str | None = None


class AiSpendPoint(BaseModel):
    period: str
    amount: float


class AiSpendOverview(BaseModel):
    monthly_total: float = Field(description="Every active service normalised to one month")
    annual_total: float
    fixed_monthly: float = Field(description="Subscriptions — predictable, committed spend")
    usage_monthly: float = Field(description="Metered APIs — the part that moves")
    services: list[AiService]
    by_kind: dict[str, float]
    unpriced: list[str] = Field(
        description="Active services with no cost entered. The total understates by however "
        "much these are actually costing."
    )
    history: list[AiSpendPoint]
    share_of_income: float = Field(
        description="Monthly AI spend as a fraction of monthly income target, 0 when no target set"
    )


# --------------------------------------------------------------------------
# credit
# --------------------------------------------------------------------------
class CreditScore(BaseModel):
    id: int
    bureau: Bureau
    score_type: ScoreType
    score: int
    source: str
    recorded_on: str = Field(description="When the score was observed, not when the row was written")


class CreditScoreCreate(BaseModel):
    bureau: Bureau
    score_type: ScoreType = "fico8"
    score: int = Field(ge=300, le=900)
    source: str = "manual"
    recorded_on: str | None = Field(default=None, description="ISO date; defaults to today")


class CreditAccount(BaseModel):
    id: int
    account_name: str
    account_type: AccountType
    bucket: Bucket = Field(description="A = real credit, B = fintech synthetic")
    track: Literal["personal", "business"]
    bureau: str
    credit_limit: float
    balance: float
    apr: float
    monthly_cost: float
    deposit: float
    opened_on: str | None
    closed_on: str | None
    payment_status: str
    is_active: bool
    notes: str
    utilization: float = Field(description="balance / credit_limit, 0 when there is no limit")
    updated_at: str


class CreditAccountCreate(BaseModel):
    account_name: str
    account_type: AccountType
    bucket: Bucket = "A"
    track: Literal["personal", "business"] = "personal"
    bureau: str = "all"
    credit_limit: float = 0
    balance: float = 0
    apr: float = 0
    monthly_cost: float = 0
    deposit: float = 0
    opened_on: str | None = None
    payment_status: str = "current"
    notes: str = ""


class CreditAccountUpdate(BaseModel):
    account_name: str | None = None
    credit_limit: float | None = None
    balance: float | None = None
    apr: float | None = None
    monthly_cost: float | None = None
    deposit: float | None = None
    closed_on: str | None = None
    payment_status: str | None = None
    is_active: bool | None = None
    notes: str | None = None


class CreditDispute(BaseModel):
    id: int
    bureau: Bureau
    category: DisputeCategory
    item: str
    reason: str
    sent_on: str | None
    response_due: str | None = Field(description="sent_on + 30 days, the FCRA response window")
    outcome: DisputeOutcome
    resolved_on: str | None
    notes: str
    days_remaining: int | None = Field(
        default=None,
        description="Days until response_due. Negative once the bureau has blown the deadline, "
        "which is itself grounds for deletion. None while unsent or already resolved.",
    )


class CreditDisputeCreate(BaseModel):
    bureau: Bureau
    category: DisputeCategory
    item: str
    reason: str = ""
    sent_on: str | None = None
    notes: str = ""


class CreditDisputeUpdate(BaseModel):
    sent_on: str | None = None
    outcome: DisputeOutcome | None = None
    notes: str | None = None


class CreditPlanStep(BaseModel):
    id: int
    phase: str
    sort_order: int
    track: Literal["personal", "business", "repair"]
    title: str
    detail: str
    est_cost: float
    done: bool
    done_on: str | None


class CreditOverview(BaseModel):
    """The whole credit tab in one call, mirroring GET /api/command/brief."""

    latest_scores: list[CreditScore]
    accounts: list[CreditAccount]
    open_disputes: list[CreditDispute]
    overdue_disputes: list[CreditDispute] = Field(
        description="Past the 30-day window with no response — escalate these first"
    )
    next_steps: list[CreditPlanStep] = Field(description="The next unfinished phase of the plan")
    total_limit: float
    total_balance: float
    utilization: float = Field(description="Aggregate revolving utilization across active accounts")
    monthly_cost: float = Field(description="Recurring spend on the credit stack")
    deposits_held: float = Field(description="Refundable security deposits currently tied up")
    bucket_b_count: int = Field(
        description="Fintech-synthetic accounts. The playbook caps these at 2 — past that a "
        "human underwriter reads the file as manufactured."
    )
    plan_progress: float = Field(description="Fraction of plan steps marked done")


# --------------------------------------------------------------------------
# finance — debts, investments, personal cash flow
#
# Advisory only. Nothing in this module moves money, places a trade, or talks
# to a broker: every number is one Blanco or an agent recorded by hand.
# --------------------------------------------------------------------------
class Debt(BaseModel):
    id: int
    creditor: str
    amount: float = Field(description="Principal as first recorded — never changes")
    balance: float = Field(description="What is still owed")
    paid: float = Field(description="amount − balance")
    progress: float = Field(description="Fraction of the principal retired, 0–1")
    interest_rate: float | None = Field(
        default=None, description="APR. None means unknown, which is not the same as 0%."
    )
    due_date: str | None = None
    status: DebtStatus
    notes: str = ""
    days_overdue: int | None = Field(
        default=None, description="Positive once past due_date and still owing"
    )
    added_at: str
    updated_at: str


class DebtCreate(BaseModel):
    creditor: str
    amount: float = Field(gt=0)
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    due_date: str | None = None
    notes: str = ""


class DebtUpdate(BaseModel):
    creditor: str | None = None
    interest_rate: float | None = Field(default=None, ge=0, le=100)
    due_date: str | None = None
    notes: str | None = None


class DebtPayment(BaseModel):
    id: int
    debt_id: int
    amount: float
    paid_on: str
    note: str = ""


class DebtPaymentCreate(BaseModel):
    amount: float = Field(gt=0)
    paid_on: str | None = None
    note: str = ""


class PayoffStep(BaseModel):
    debt_id: int
    creditor: str
    balance: float
    interest_rate: float | None = None
    order: int = Field(description="1 is the debt this strategy attacks first")
    months_to_clear: int | None = Field(
        default=None, description="None when no monthly payment was supplied"
    )
    projected_payoff_date: str | None = None


class PayoffPlan(BaseModel):
    strategy: PayoffStrategy
    order: list[PayoffStep]
    months_to_debt_free: int | None = None
    debt_free_date: str | None = None
    total_interest: float = Field(description="Interest paid over the projection, 0 when no rates")


class PayoffRecommendation(BaseModel):
    strategy: PayoffStrategy
    target_debt_id: int | None
    target_creditor: str | None
    headline: str = Field(description="The ONE next move, phrased for the agent to quote")
    why: str


class PayoffPlanResponse(BaseModel):
    total_debt: float
    debt_count: int
    monthly_payment: float | None = Field(
        default=None, description="What the projection assumed. None means order only, no dates."
    )
    avalanche: PayoffPlan
    snowball: PayoffPlan
    recommendation: PayoffRecommendation


class InvestmentAccount(BaseModel):
    id: int
    name: str
    type: InvestmentType
    balance: float
    contributions_to_date: float
    gain: float = Field(description="balance − contributions_to_date. Manual entries, not marked to market.")
    notes: str = ""
    updated_at: str


class InvestmentAccountCreate(BaseModel):
    name: str
    type: InvestmentType = "brokerage"
    balance: float = Field(default=0, ge=0)
    contributions_to_date: float = Field(default=0, ge=0)
    notes: str = ""


class InvestmentAccountUpdate(BaseModel):
    name: str | None = None
    type: InvestmentType | None = None
    balance: float | None = Field(default=None, ge=0)
    contributions_to_date: float | None = Field(default=None, ge=0)
    notes: str | None = None


class InvestmentGoal(BaseModel):
    id: int
    name: str
    target_amount: float
    monthly_contribution: float
    target_date: str | None = None
    current_value: float
    progress: float = Field(description="current_value / target_amount, 0–1")
    months_at_current_rate: int | None = Field(
        default=None, description="None when nothing is being contributed"
    )
    on_track: bool | None = Field(
        default=None, description="None when there is no target_date to judge against"
    )
    notes: str = ""
    updated_at: str


class InvestmentGoalCreate(BaseModel):
    name: str
    target_amount: float = Field(gt=0)
    monthly_contribution: float = Field(default=0, ge=0)
    target_date: str | None = None
    current_value: float = Field(default=0, ge=0)
    notes: str = ""


class InvestmentGoalUpdate(BaseModel):
    name: str | None = None
    target_amount: float | None = Field(default=None, gt=0)
    monthly_contribution: float | None = Field(default=None, ge=0)
    target_date: str | None = None
    current_value: float | None = Field(default=None, ge=0)
    notes: str | None = None


class Transaction(BaseModel):
    id: int
    occurred_on: str
    category: str
    amount: float
    kind: TransactionKind
    note: str = ""


class TransactionCreate(BaseModel):
    amount: float = Field(gt=0)
    kind: TransactionKind
    category: str = "uncategorized"
    occurred_on: str | None = None
    note: str = ""


class BudgetCategory(BaseModel):
    id: int
    name: str
    monthly_budget: float
    spent: float = Field(description="Actual spend in the month being reported")
    remaining: float
    variance: float = Field(description="monthly_budget − spent. Negative means over.")
    over_budget: bool
    notes: str = ""


class BudgetCategoryCreate(BaseModel):
    name: str
    monthly_budget: float = Field(default=0, ge=0)
    notes: str = ""


class BudgetCategoryUpdate(BaseModel):
    monthly_budget: float | None = Field(default=None, ge=0)
    notes: str | None = None


class FinanceNote(BaseModel):
    id: int
    title: str
    category: NoteCategory
    body: str = ""
    institution: str = ""
    last4: str = Field(default="", description="Last four digits only — never the full number")
    opened_on: str | None = None
    pinned: bool = False
    archived: bool = False
    created_at: str
    updated_at: str


class FinanceNoteCreate(BaseModel):
    title: str = Field(min_length=1)
    category: NoteCategory = "general"
    body: str = ""
    institution: str = ""
    last4: str = Field(
        default="", max_length=4, pattern=r"^\d{0,4}$",
        description="Last four digits only. Capped at four so a full card number cannot be stored.",
    )
    opened_on: str | None = None
    pinned: bool = False


class FinanceNoteUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1)
    category: NoteCategory | None = None
    body: str | None = None
    institution: str | None = None
    last4: str | None = Field(default=None, max_length=4, pattern=r"^\d{0,4}$")
    opened_on: str | None = None
    pinned: bool | None = None
    archived: bool | None = None


class NetWorth(BaseModel):
    assets: float = Field(description="Investment and cash account balances")
    liabilities: float = Field(description="Outstanding debt balances")
    net_worth: float
    by_account_type: dict[str, float] = {}
    debt_total: float
    debt_count: int
    as_of: str


class MonthlySummary(BaseModel):
    month: str = Field(description="YYYY-MM")
    income: float
    expenses: float
    debt_payments: float
    net: float = Field(description="income − expenses − debt_payments")
    savings_rate: float = Field(description="net / income, 0 when there was no income")
    budget: list[BudgetCategory]
    total_budgeted: float
    uncategorized_spend: float = Field(
        description="Expense with no matching budget category — invisible to variance otherwise"
    )


class FinanceOverview(BaseModel):
    """The whole finance tab in one call, mirroring GET /api/credit."""

    debts: list[Debt]
    total_debt: float
    overdue_debts: list[Debt]
    next_move: PayoffRecommendation
    investment_accounts: list[InvestmentAccount]
    investment_goals: list[InvestmentGoal]
    net_worth: NetWorth
    month: MonthlySummary
    pinned_notes: list[FinanceNote] = Field(
        default=[], description="The handful he keeps at the top — see /api/finance/notes for all"
    )
    advisory_only: bool = Field(
        default=True,
        description="Always true. This module tracks and advises; it never moves money.",
    )


class VaultSyncResult(BaseModel):
    ok: bool
    detail: str
    debts_written: int = 0


# --------------------------------------------------------------------------
# journal / wellbeing
# --------------------------------------------------------------------------
class JournalEntry(BaseModel):
    date: str
    mood: str = ""
    text: str = ""
    tags: list[str] = []
    created: str | None = None
    updated: str | None = None


class JournalEntryWrite(BaseModel):
    mood: str = ""
    text: str = ""
    tags: list[str] = []


class MoodPoint(BaseModel):
    date: str
    mood: str
    score: int = Field(description="1-5, mapped from the mood glyph; 0 when unknown")


class JournalOverview(BaseModel):
    source_file: str
    entry_count: int
    current_streak: int
    longest_streak: int
    last_entry_date: str | None
    average_score_30d: float
    series: list[MoodPoint]
    recent: list[JournalEntry]


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------
class AgentSchedule(BaseModel):
    label: str
    cron: str = ""
    human: str = ""


class Agent(BaseModel):
    id: str
    name: str
    emoji: str
    role: str
    model: str = ""
    engine: str = Field(
        default="",
        description="Which commander this agent answers to — empty for standalone services",
    )
    kind: Literal["commander", "subagent", "service"]
    # "standby" = exists and can be spawned on demand, but isn't resident.
    status: Literal["online", "standby", "offline", "scheduled", "unknown"]
    workspace: str = ""
    channel: str = ""
    schedules: list[AgentSchedule] = []
    last_seen: str | None = None
    detail: str = ""


class AgentRoster(BaseModel):
    probed: bool
    online: int = Field(description="Resident right now")
    standby: int = Field(default=0, description="Spawnable on demand, not resident")
    scheduled: int = Field(default=0, description="Runs on a cron, not resident")
    total: int
    agents: list[Agent]


# --------------------------------------------------------------------------
# systems
# --------------------------------------------------------------------------
class ServiceStatus(BaseModel):
    id: str
    name: str
    kind: Literal["http", "process", "cron", "file"]
    status: Literal["up", "down", "stale", "unknown"]
    target: str
    detail: str = ""
    last_checked: str
    optional: bool = Field(
        default=False,
        description="Nice-to-have: excluded from the health roll-up and never alerts",
    )


class CronEntry(BaseModel):
    schedule: str
    command: str
    comment: str = ""


class DiskUsage(BaseModel):
    mount: str
    total_gb: float
    used_gb: float
    free_gb: float
    percent_used: float


class SystemsHealth(BaseModel):
    overall: Health
    checked_at: str
    services: list[ServiceStatus]
    cron: list[CronEntry]
    disks: list[DiskUsage]
    uptime: str = ""


# --------------------------------------------------------------------------
# knowledge
# --------------------------------------------------------------------------
class VaultNote(BaseModel):
    title: str
    path: str
    folder: str
    tags: list[str] = []
    modified_at: str | None = None
    excerpt: str = ""


class FolderStat(BaseModel):
    folder: str
    note_count: int


class KnowledgeOverview(BaseModel):
    vault_path: str
    total_notes: int
    by_folder: list[FolderStat]
    reference_topics: list[FolderStat]
    recent: list[VaultNote]


class SearchHit(BaseModel):
    title: str
    path: str
    folder: str
    line: int
    snippet: str
    score: float


class SearchResults(BaseModel):
    query: str
    hit_count: int
    hits: list[SearchHit]


# --------------------------------------------------------------------------
# bridge (Your Business -> Personal, one-way)
# --------------------------------------------------------------------------
class BridgeMessage(BaseModel):
    id: int
    origin: str
    kind: Literal["notice", "deal", "invoice", "escalation"]
    title: str
    body: str = ""
    amount: float | None = None
    read: bool
    read_at: str | None = Field(
        default=None,
        description="When it was first read — how fast Blanco reacted is the "
                    "signal the Your Business monitor's learning loop uses",
    )
    received_at: str


class BridgeMessageCreate(BaseModel):
    origin: str = "biz"
    kind: Literal["notice", "deal", "invoice", "escalation"] = "notice"
    title: str
    body: str = ""
    amount: float | None = None


# --------------------------------------------------------------------------
# your business divisions
# --------------------------------------------------------------------------
# Your Business runs as three divisions. A division owns ventures, and ventures own
# venture_events — so a division's revenue is a roll-up of the one ledger that
# already exists rather than a second set of numbers to keep in agreement.


class Division(BaseModel):
    id: str
    name: str
    emoji: str
    tagline: str
    stage: Stage
    health: Health
    monthly_target: float
    monthly_actual: float = Field(
        description="Rolled up from this division's ventures — not stored on the division"
    )
    attainment: float = Field(description="monthly_actual / monthly_target, 0 when no target")
    pipeline_value: float = Field(
        description="Total value of deals still open (lead, qualified or proposal)"
    )
    open_deals: int
    next_action: str
    vault_path: str
    ventures: list[Venture]
    updated_at: str


class DivisionUpdate(BaseModel):
    name: str | None = None
    tagline: str | None = None
    stage: Stage | None = None
    health: Health | None = None
    monthly_target: float | None = None
    next_action: str | None = None
    vault_path: str | None = None


DealStage = Literal["lead", "qualified", "proposal", "won", "lost"]
# The two stages that close a deal. Reaching either stamps closed_on; moving
# back out of one clears it, so "when did this close" can never outlive the
# close itself.
CLOSED_DEAL_STAGES: tuple[str, ...] = ("won", "lost")


class Deal(BaseModel):
    id: int
    division_id: str
    client: str
    title: str
    stage: DealStage
    value: float
    source: str
    next_action: str
    opened_on: str
    closed_on: str | None = None
    notes: str
    updated_at: str


class DealCreate(BaseModel):
    division_id: str
    client: str
    title: str = ""
    stage: DealStage = "lead"
    value: float = 0
    source: str = ""
    next_action: str = ""
    opened_on: str | None = Field(default=None, description="Defaults to today")
    notes: str = ""

    @field_validator("client")
    @classmethod
    def _client_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("a deal needs a client")
        return v.strip()


class DealUpdate(BaseModel):
    client: str | None = None
    title: str | None = None
    stage: DealStage | None = None
    value: float | None = None
    source: str | None = None
    next_action: str | None = None
    notes: str | None = None


# --- annotations: who Blanco is signed up with, and what is open with them ---
#
# Distinct from DealStage on purpose: a deal moves through a pipeline and ends,
# an annotation is a standing account that persists with or without a deal.
AnnotationKind = Literal["platform", "client", "vendor", "marketplace", "service", "other"]
AnnotationStatus = Literal["active", "needs_finish", "blocked", "done", "dormant"]
# What the Your Business header counts as "still on Blanco's plate".
OPEN_ANNOTATION_STATUSES: tuple[str, ...] = ("active", "needs_finish", "blocked")


class Annotation(BaseModel):
    id: int
    division_id: str | None = None
    company: str
    domain: str
    kind: AnnotationKind
    account_email: str
    project: str
    status: AnnotationStatus
    next_action: str
    signed_up_on: str | None = None
    last_seen: str | None = None
    source: str
    notes: str
    updated_at: str


class AnnotationCreate(BaseModel):
    company: str
    division_id: str | None = None
    domain: str = ""
    kind: AnnotationKind = "platform"
    account_email: str = ""
    project: str = ""
    status: AnnotationStatus = "active"
    next_action: str = ""
    signed_up_on: str | None = None
    last_seen: str | None = None
    source: str = ""
    notes: str = ""

    @field_validator("company")
    @classmethod
    def _company_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("an annotation needs a company")
        return v.strip()

    @field_validator("domain", "account_email")
    @classmethod
    def _fold_case(cls, v: str) -> str:
        # Both are identity, and mail casing is arbitrary — "Twilio.com" and
        # "twilio.com" must not become two rows through the unique index.
        return v.strip().lower()


class AnnotationUpdate(BaseModel):
    division_id: str | None = None
    company: str | None = None
    domain: str | None = None
    kind: AnnotationKind | None = None
    account_email: str | None = None
    project: str | None = None
    status: AnnotationStatus | None = None
    next_action: str | None = None
    signed_up_on: str | None = None
    last_seen: str | None = None
    source: str | None = None
    notes: str | None = None


class AnnotationImportResult(BaseModel):
    """What a Hermes mail sweep changed, so a re-run is readable at a glance."""

    created: int
    updated: int
    unchanged: int
    annotations: list[Annotation] = Field(default_factory=list)


class LearnedSender(BaseModel):
    domain: str
    weight: float = Field(description="Positive = Blanco engages with it; negative = he ignores it")


class EmailMonitorHealth(BaseModel):
    status: Literal["ok", "stale", "erroring", "never_run", "not_installed"]
    installed: bool
    last_poll_at: str = ""
    minutes_since_poll: float | None = None
    poll_count: int = 0
    alerted_total: int = 0
    last_error: str = ""
    stale_after_minutes: int = 90
    learned_senders: list[LearnedSender] = Field(default_factory=list)


class BizOverview(BaseModel):
    month: str
    total_target: float
    total_actual: float
    attainment: float
    pipeline_value: float = Field(description="Open deal value across every division")
    open_deals: int
    won_this_month: float
    divisions: list[Division]
    recent_deals: list[Deal]
    unread_notices: int


# --------------------------------------------------------------------------
# email
# --------------------------------------------------------------------------
class EmailStatus(BaseModel):
    configured: bool = Field(description="Gmail token present")
    tracked_total: int = Field(description="Messages the categorizer has processed")
    last_check: str | None
    categories: list[str]
    vip_sender_count: int
    state_file: str
    log_last_written: str | None


class EmailMessage(BaseModel):
    id: str
    thread_id: str
    sender: str
    sender_address: str
    subject: str
    preview: str
    category: str
    importance: int = Field(ge=1, le=5, description="1 low … 5 critical, after any override")
    importance_label: str
    agent_importance: int = Field(default=0, description="What the categorizer scored it")
    overridden_by: str = Field(
        default="", description="Rule that changed it, e.g. 'domain:linkedin.com'"
    )
    is_vip: bool
    vip_label: str = ""
    sender_domain: str = ""
    unread: bool
    received_at: str
    link: str


class Inbox(BaseModel):
    status: EmailStatus
    fetched: bool = Field(description="False when Gmail could not be reached")
    error: str = ""
    messages: list[EmailMessage]
    counts: dict[str, int]
    categories: list[str] = Field(default=[], description="Categories present in this result")
    query: str = ""


RuleScope = Literal["sender", "domain", "category"]


class EmailRule(BaseModel):
    id: int
    scope: RuleScope
    match_value: str
    importance: int = Field(ge=1, le=5)
    note: str = ""
    original_importance: int | None = None
    hits: int = 0
    created_at: str
    updated_at: str


class EmailRuleCreate(BaseModel):
    scope: RuleScope = "sender"
    match_value: str = Field(min_length=1, description="Address, domain, or category name")
    importance: int = Field(ge=1, le=5)
    note: str = ""
    original_importance: int | None = Field(
        default=None, description="What the agent scored it, for the audit trail"
    )


# --------------------------------------------------------------------------
# messenger
# --------------------------------------------------------------------------
# Three commanders, and the set is closed on purpose. Each one is a CLI with
# its own tools, memory and sub-agent roster; adding a fourth is a real
# integration (how it takes a prompt, how it resumes a session, where its
# sub-agents are declared), never a config string.
ChatEngine = Literal["openclaw", "claude", "hermes"]


class ChatMessage(BaseModel):
    """One turn. The timeline is now strictly the conversation — OS notices go
    to the alert tray, where they can be acknowledged rather than scrolling
    away between two questions."""

    id: int
    role: Literal["user", "assistant"]
    agent: str = "main"
    body: str = ""
    status: Literal["pending", "done", "failed"] = "done"
    error: str = ""
    duration_ms: int = 0
    engine: ChatEngine = "openclaw"
    session_id: int = Field(default=1, description="Which terminal session this belongs to")
    created_at: str


class ChatSend(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    agent: str = Field(
        default="",
        description="A sub-agent id belonging to the session's commander — see "
                    "GET /api/chat/agents?engine=. Empty uses whoever the session is "
                    "pointed at, which is the normal case.",
    )
    engine: ChatEngine = Field(
        default="openclaw",
        description="openclaw = `openclaw agent` (Sweet Jones and her sub-agents); "
                     "claude = headless Claude Code turn; "
                     "hermes = `hermes --oneshot` in the gateway",
    )
    session_id: int = Field(default=1, description="Which terminal session to post into")


class ChatExchange(BaseModel):
    message: ChatMessage = Field(description="What you sent")
    reply: ChatMessage = Field(description="Pending until the agent answers — poll it")


class ChatAgent(BaseModel):
    """One sub-agent, always owned by exactly one commander.

    The rosters are genuinely separate systems — an OpenClaw sub-agent is a
    directory with an IDENTITY.md, a Claude one is a markdown file with
    frontmatter, a Hermes one is an isolated profile — so the id is only
    meaningful alongside its `engine`.
    """

    id: str
    engine: ChatEngine
    name: str
    emoji: str
    role: str = ""
    primary: bool = Field(
        default=False, description="The commander itself, rather than one of its sub-agents"
    )
    source: str = Field(default="", description="Where this definition was read from")


class ChatEngineInfo(BaseModel):
    """One commander tab in the console."""

    id: ChatEngine
    name: str
    emoji: str
    note: str = Field(default="", description="Shown under the tabs")
    binary: str = Field(default="", description="Resolved path of the CLI")
    available: bool = Field(default=False, description="Whether that binary is on this box")
    model: str = Field(default="", description="Model override the deck applies, if any")
    agent_count: int = Field(default=0, description="Sub-agents on its roster")


class ChatSessionInfo(BaseModel):
    id: int
    label: str
    engine: ChatEngine = "openclaw"
    agent: str = "main"
    created_at: str
    resumable: bool = Field(
        default=False,
        description="Whether the commander handed back a session id, so the next "
                    "turn continues the thread instead of starting cold",
    )


class ChatSessionCreate(BaseModel):
    label: str = Field(default="", max_length=60)
    engine: ChatEngine = "openclaw"
    agent: str = "main"


class ChatSessionUpdate(BaseModel):
    """PATCH payload — rename a session, repoint it at another sub-agent, or both.

    `engine` is deliberately absent: a session carries one commander's native
    conversation id, and that id means nothing to the other two.
    """

    label: str | None = Field(default=None, min_length=1, max_length=60)
    agent: str | None = None
    forget: bool = Field(
        default=False,
        description="Drop the remembered native session id — the next turn starts a fresh "
                    "conversation on the commander's side, keeping the visible history",
    )


# --------------------------------------------------------------------------
# focus timer
# --------------------------------------------------------------------------
class FocusSession(BaseModel):
    id: int
    kind: Literal["work", "break"]
    label: str = ""
    planned_secs: int
    started_at: str
    ends_at: str
    stopped_at: str | None = None
    status: Literal["running", "completed", "cancelled"]
    remaining_secs: int = Field(description="0 unless running")
    elapsed_secs: int


class TimerStart(BaseModel):
    kind: Literal["work", "break"] = "work"
    minutes: int = Field(default=25, ge=1, le=240)
    label: str = ""


class TimerOverview(BaseModel):
    active: FocusSession | None
    work_minutes_today: int
    break_minutes_today: int
    sessions_completed_today: int
    work_presets: list[int]
    break_presets: list[int]
    history: list[FocusSession]


# --------------------------------------------------------------------------
# now playing (Windows media session)
# --------------------------------------------------------------------------
PlaybackStatus = Literal["playing", "paused", "stopped"]
# Transport commands Windows accepts for the session in front.
# `play` and `pause` are separate from `play_pause` on purpose: a toggle fired
# from a stale strip does the opposite of what the button said.
NowPlayingAction = Literal[
    "play_pause", "play", "pause", "next", "previous", "stop", "seek"
]


class MediaSession(BaseModel):
    """One app registering transport controls — a browser tab, Spotify, Media
    Player. The capability flags are what that app will actually accept, so the
    deck can grey a control out instead of offering a button that does nothing.
    """

    app_id: str = ""
    app_label: str = ""
    title: str = ""
    artist: str = ""
    album: str = ""
    status: PlaybackStatus = "stopped"
    position_secs: int = 0
    duration_secs: int = 0
    can_seek: bool = False
    can_next: bool = False
    can_previous: bool = False
    can_pause: bool = False


class NowPlaying(MediaSession):
    """Whatever is playing on this PC, in any app — browser tabs included.

    `available` is False on a machine with no Windows underneath, which is the
    normal state for a headless deploy rather than an error worth surfacing.
    """

    available: bool
    error: str = ""
    # Album art as a data URI — the deck cannot fetch from the Windows side.
    thumbnail: str = ""
    sessions: list[MediaSession] = Field(
        default_factory=list,
        description="Every player Windows can see, so the deck can pause the video in "
                    "one browser without touching the music in another",
    )


class NowPlayingControl(BaseModel):
    action: NowPlayingAction
    app_id: str = Field(
        default="",
        description="Which player to drive. Empty means whichever Windows considers "
                    "current, which is what a single-player box always wants.",
    )
    position_secs: int = Field(
        default=0, ge=0, description="Target position — `seek` only"
    )


class NowPlayingResult(BaseModel):
    available: bool
    # False when the app refused, e.g. a session that cannot skip.
    ok: bool = False
    error: str = ""


# --------------------------------------------------------------------------
# activity / stream
# --------------------------------------------------------------------------
class ActivityItem(BaseModel):
    id: int
    module: str
    verb: str
    subject: str
    meta: dict = {}
    created_at: str


class Ack(BaseModel):
    ok: bool = True
    message: str = ""


class OpenUrl(BaseModel):
    url: str


# --- Your Business · Freight Broker ------------------------------------------------
FreightLogKind = Literal["call", "carrier", "shipper", "load", "money",
                         "paperwork", "learning", "win", "note"]


class FreightStep(BaseModel):
    id: str
    label: str
    hint: str = ""
    done: bool
    done_at: str | None = None


class FreightPhase(BaseModel):
    id: str
    title: str
    when: str
    note: str = Field(description="Vault note (basename) the phase comes from")
    steps: list[FreightStep]
    done: int
    total: int


class FreightLogEntry(BaseModel):
    id: int
    day: str
    kind: FreightLogKind
    title: str
    detail: str
    amount: float | None
    created_at: str


class FreightLogCreate(BaseModel):
    day: str = Field(default="", pattern=r"^(\d{4}-\d{2}-\d{2})?$",
                     description="YYYY-MM-DD; empty means today")
    kind: FreightLogKind = "note"
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(default="", max_length=4000)
    amount: float | None = None


class FreightDaily(BaseModel):
    day: str
    calls: int = 0
    carriers_added: int = 0
    shippers_contacted: int = 0
    loads_booked: int = 0
    revenue: float = 0
    carrier_cost: float = 0
    margin: float = 0
    notes: str = ""


class FreightDailyUpsert(BaseModel):
    calls: int = Field(default=0, ge=0)
    carriers_added: int = Field(default=0, ge=0)
    shippers_contacted: int = Field(default=0, ge=0)
    loads_booked: int = Field(default=0, ge=0)
    revenue: float = Field(default=0, ge=0)
    carrier_cost: float = Field(default=0, ge=0)
    notes: str = Field(default="", max_length=2000)


class FreightTracker(BaseModel):
    phases: list[FreightPhase]
    next_step: FreightStep | None
    next_phase: str | None
    steps_done: int
    steps_total: int
    log: list[FreightLogEntry]
    daily: list[FreightDaily]
    totals: FreightDaily
    vault_log: str = Field(description="Vault path of the mirrored progress log Haul reads")


# --- job search: Personal → Job Search -------------------------------------
JobStatus = Literal["saved", "applied", "interviewing", "offer", "rejected", "withdrawn"]


class JobApplication(BaseModel):
    id: int
    company: str
    role: str
    status: JobStatus
    source: str
    link: str
    pay: str
    applied_on: str | None = None
    next_action: str
    notes: str
    updated_at: str


class JobApplicationCreate(BaseModel):
    company: str
    role: str = ""
    status: JobStatus = "saved"
    source: str = ""
    link: str = ""
    pay: str = ""
    applied_on: str | None = Field(default=None, description="Defaults to today once status is applied or later")
    next_action: str = ""
    notes: str = ""

    @field_validator("company")
    @classmethod
    def _company_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("an application needs a company")
        return v.strip()


class JobApplicationUpdate(BaseModel):
    company: str | None = None
    role: str | None = None
    status: JobStatus | None = None
    source: str | None = None
    link: str | None = None
    pay: str | None = None
    applied_on: str | None = None
    next_action: str | None = None
    notes: str | None = None


class ResumeFile(BaseModel):
    name: str
    modified: str


class JobSearchOverview(BaseModel):
    applications: list[JobApplication]
    counts: dict[str, int]
    resumes: list[ResumeFile]
