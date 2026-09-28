#!/usr/bin/env bash
# Brings up Blanco's whole stack and prints a status block.
#
# Absorbs what the retired Desktop launchers used to do — 5lanxo_Land.bat,
# OpenClaw-Only.bat and start-hermes-services.bat — so one icon starts
# everything. Called by "5lanxo Land.bat"; safe to run directly.
#
# Every step is idempotent: already-running services are left alone.
set -u

# No ANSI colour: cmd.exe renders the escape codes literally in some consoles,
# and the bat already sets its own colour.
ok()   { printf '        [ ok ] %s\n' "$1"; }
warn() { printf '        [ .. ] %s\n' "$1"; }
bad()  { printf '        [FAIL] %s\n' "$1"; }

up() { curl -sf -m 3 -o /dev/null "$1" 2>/dev/null; }

wait_for() {  # wait_for <url> <seconds>
    for _ in $(seq 1 "$2"); do
        up "$1" && return 0
        sleep 1
    done
    return 1
}

# ── 1. OpenClaw gateway — Sweet Jones ────────────────────────────────
echo "  [1/4] Sweet Jones — OpenClaw gateway (8080)"
if up http://127.0.0.1:8080/health; then
    ok "already up"
else
    openclaw gateway restart >/dev/null 2>&1
    wait_for http://127.0.0.1:8080/health 20 && ok "started" || bad "did not come up"
fi

# ── 2. Nerve WebUI ───────────────────────────────────────────────────
echo "  [2/4] Nerve WebUI (3080)"
if up http://127.0.0.1:3080/; then
    ok "already up"
elif [ -x $HOME/start-nerve.sh ] || [ -f $HOME/start-nerve.sh ]; then
    setsid bash $HOME/start-nerve.sh >/tmp/nerve.log 2>&1 < /dev/null &
    wait_for http://127.0.0.1:3080/ 20 && ok "started" || warn "still starting — see /tmp/nerve.log"
else
    warn "start-nerve.sh not found, skipped"
fi

# ── 3. Hermes WebUI ──────────────────────────────────────────────────
echo "  [3/4] Hermes WebUI (8787)"
if up http://127.0.0.1:8787/; then
    ok "already up"
elif [ -f $HOME/start-hermes-webui.sh ]; then
    setsid bash $HOME/start-hermes-webui.sh >/tmp/hermes-webui.log 2>&1 < /dev/null &
    wait_for http://127.0.0.1:8787/ 20 && ok "started" || warn "still starting — see /tmp/hermes-webui.log"
else
    warn "start-hermes-webui.sh not found, skipped"
fi

# ── 4. Blanco OS ─────────────────────────────────────────────────────
echo "  [4/4] Blanco OS — Agentic Command (8800)"
systemctl --user start blanco-os >/dev/null 2>&1
if wait_for http://127.0.0.1:8800/api/health 30; then
    ok "up"
else
    bad "not answering — journalctl --user -u blanco-os -n 50"
    exit 1
fi

echo
exec "$(dirname "$0")/status.sh"
