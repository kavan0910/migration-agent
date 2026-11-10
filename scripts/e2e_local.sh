#!/usr/bin/env bash
# API-level end-to-end journey: starts backend (SQLite, dev auth, ENGINE_OFFLINE, temp LocalStore) + worker, runs
# sign up -> upload -> estimate -> analyze (SSE) -> results -> comment -> usage -> members -> delete, then cleans up.
# No Docker, no LLM calls, no spend. Use E2E_API_URL=... to target an already running stack instead.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=backend/.venv/bin/python; [ -x "$PY" ] || PY=python3
exec "$PY" scripts/e2e_local.py "$@"
