#!/usr/bin/env bash
# Start Blanco OS. Binds to 127.0.0.1 only — this is a personal machine service.
set -euo pipefail
cd "$(dirname "$0")"

PORT="${BLANCO_OS_PORT:-8800}"
HOST="${BLANCO_OS_HOST:-127.0.0.1}"

exec .venv/bin/python -m uvicorn app.main:app --host "$HOST" --port "$PORT" "$@"
