"""Runtime configuration for Blanco OS.

Every path is overridable by env var so the API can run against a throwaway
vault in tests without touching the real second-brain.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="BLANCO_OS_",
        env_file=str(REPO_ROOT / ".env"),
        extra="ignore",
    )

    # --- identity -------------------------------------------------------
    operator_name: str = "Blanco"
    operator_handle: str = "5lanxo"
    timezone: str = "America/Chicago"

    # --- storage --------------------------------------------------------
    # OS-owned state (alerts, venture ledger, cert overrides, bridge inbox).
    # Kept on the native WSL fs: SQLite journaling is unreliable over the
    # /mnt/c FUSE mount the vault lives on.
    db_path: Path = REPO_ROOT / "data" / "blanco_os.db"

    # --- external surfaces we read ---------------------------------------
    vault_path: Path = Path.home() / "vault"
    workspace_path: Path = Path.home() / "workspace"

    # --- live probes ------------------------------------------------------
    openclaw_url: str = "http://localhost:8080"
    probe_timeout_seconds: float = 1.5
    # How long a service sweep stays fresh. The freshness probes walk a few
    # hundred vault files across /mnt/c and cost seconds, while the jobs
    # it watches run every 2-4 hours — so re-probing per request is pure waste.
    systems_cache_seconds: float = 30.0
    # Turn every outbound probe off (tests, offline demos).
    probes_enabled: bool = True

    # --- commanders --------------------------------------------------------
    # The three CLIs the console talks to. Each is already installed in the
    # gateway and each brings its own tools, memory and sub-agents, which is
    # why the local Ollama models were retired rather than kept alongside.
    #
    # A turn is a cold CLI start every time — measured ~70s for OpenClaw even
    # on a one-word reply — so the ceiling is generous on purpose. A turn that
    # dies at 120s is indistinguishable from a broken install.
    turn_timeout_seconds: float = 420.0

    # Model overrides live here rather than in the code: when a provider drops
    # a model id, fixing the deck is a .env edit, not a deploy. Empty means
    # "whatever the CLI's own config says", which is the right default for the
    # two whose config Blanco actually maintains.
    openclaw_model: str = ""
    claude_model: str = ""
    # Hermes is the exception: its config.yaml points at `nous/deepseek`, which
    # the provider now 404s. Pinning a provider+model that answers keeps the
    # tab usable without editing Hermes' own config out from under it.
    hermes_provider: str = "anthropic"
    hermes_model: str = "claude-sonnet-4.6"

    # --- notifications -----------------------------------------------------
    notify_enabled: bool = True
    # info | warning | critical — anything below this is not announced.
    notify_min_severity: str = "warning"
    # Optional. Without it only the hq/INBOX drop is used (Sweet Jones relays).
    discord_webhook_url: str = ""

    # --- opening links -----------------------------------------------------
    # Blanco OS opens external links in Opera, never in the Windows default
    # browser. The deck runs in an app window where a plain link would hand the
    # URL to whatever Windows has set (Chrome), so the server launches Opera
    # through WSL interop instead.
    #
    # The endpoint still starts a program, so it gets no say over where that
    # program goes: only https, and only a host on this list. "*.example.com"
    # matches any subdomain; a bare host matches exactly.
    opera_path: Path = Path("/mnt/c/Program Files/Opera/opera.exe")
    open_url_hosts: list[str] = [
        "claude.ai", "*.claude.ai", "anthropic.com", "*.anthropic.com",
        "*.google.com", "google.com",
        "github.com", "*.github.com",
        "example.com", "*.example.com",
        "*.twilio.com", "twilio.com", "*.vapi.ai", "vapi.ai",
        "*.render.com", "render.com", "*.cloudflare.com",
        "openai.com", "*.openai.com", "*.notion.so", "*.linkedin.com",
        "*.upwork.com", "*.fiverr.com", "*.instagram.com", "*.youtube.com",
        # Job Search postings (Personal → Job Search).
        "linkedin.com", "indeed.com", "*.indeed.com", "glassdoor.com", "*.glassdoor.com",
        "ziprecruiter.com", "*.ziprecruiter.com", "*.greenhouse.io", "*.lever.co",
        "*.workable.com", "*.myworkdayjobs.com",
    ]

    # --- server -----------------------------------------------------------
    host: str = "127.0.0.1"
    port: int = 8800
    cors_origins: str = "*"

    # Vault-relative paths. Centralised so a vault reshuffle is a one-file fix.
    @property
    def todo_data_file(self) -> Path:
        return self.vault_path / "02-areas" / "todo-data.json"

    @property
    def todo_markdown_file(self) -> Path:
        return self.vault_path / "02-areas" / "todo-list.md"

    @property
    def diary_file(self) -> Path:
        return self.vault_path / "05-journal" / "diary.json"

    @property
    def cert_roadmap_file(self) -> Path:
        return self.vault_path / "Personal" / "projects" / "certs" / "Cert-Roadmap-Tracker.md"

    @property
    def credit_playbook_file(self) -> Path:
        return self.vault_path / "Personal" / "areas" / "finance" / "Credit-Repair-Playbook.md"

    @property
    def debt_tracker_file(self) -> Path:
        return self.vault_path / "Personal" / "areas" / "finance" / "Debt-Tracker.md"

    @property
    def mood_trends_file(self) -> Path:
        return self.vault_path / "Personal" / "areas" / "health" / "mood-trends.md"

    @property
    def agent_ops_dir(self) -> Path:
        return self.vault_path / "02-areas" / "agent-ops"

    @property
    def reference_dir(self) -> Path:
        return self.vault_path / "03-resources" / "reference"

    @property
    def active_focus_file(self) -> Path:
        return self.workspace_path / "ACTIVE.md"

    @property
    def system_map_file(self) -> Path:
        return self.workspace_path / "Blanco-OS-System-Map.md"

    @property
    def claude_agents_dirs(self) -> list[Path]:
        """Where Claude Code keeps its sub-agent definitions, user scope first.

        User scope, then this workspace's project scope, then every installed
        plugin — a plugin ships agents the same way a project does, and one
        Blanco installs should show up in the roster without a code change.
        Built-in agent types (Explore, Plan, general-purpose…) live inside the
        CLI binary rather than on disk, so nothing can discover those.
        """
        dirs = [Path.home() / ".claude" / "agents", self.workspace_path / ".claude" / "agents"]
        plugin_cache = Path.home() / ".claude" / "plugins" / "cache"
        if plugin_cache.is_dir():
            dirs.extend(sorted(p for p in plugin_cache.glob("*/*/*/agents") if p.is_dir()))
            dirs.extend(sorted(p for p in plugin_cache.glob("*/*/agents") if p.is_dir()))
        return dirs

    @property
    def openclaw_subagents_dir(self) -> Path:
        return self.workspace_path / "subagents"

    @property
    def openclaw_config_file(self) -> Path:
        """OpenClaw's own config — the register of which agents actually exist.

        `workspace/subagents/*/IDENTITY.md` is documentation Blanco writes; this
        file is what OpenClaw will accept after `--agent`. When they disagree,
        this one is right.
        """
        return self.workspace_path.parent / "openclaw.json"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
