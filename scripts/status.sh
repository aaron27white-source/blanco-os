#!/usr/bin/env bash
# Prints a short Blanco OS status block. Called by the Windows launchers so
# they never have to nest quotes through cmd.exe -> wsl -> bash -> python.
set -u
API="${BLANCO_OS_URL:-http://127.0.0.1:8800}"

if ! curl -sf -m 3 "$API/api/health" >/dev/null 2>&1; then
    echo "        Blanco OS is not answering on $API"
    exit 1
fi

curl -s -m 5 "$API/api/agents" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print("        fleet:  %d online · %d standby · %d scheduled · %d total"
          % (d["online"], d["standby"], d["scheduled"], d["total"]))
except Exception:
    pass
' 2>/dev/null

curl -s -m 5 "$API/api/command/brief?sweep=false" 2>/dev/null | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
    print("        focus:  " + d["focus"]["headline"][:60])
    print("        health: %s · %d open alert(s) · %d unread from Your Business"
          % (d["systems_health"], len(d["alerts"]), d["bridge_unread"]))
    nxt = d["now_next"]
    if nxt:
        print("        next:   " + nxt[0]["title"][:60])
except Exception:
    pass
' 2>/dev/null

exit 0
