#!/usr/bin/env bash
# Smoke test: bring the stack up, wait for health, sign up a dev user, create a project from a tiny sample,
# check key endpoints. Usage:  deploy/smoke.sh            (builds and starts the stack)
#   SMOKE_NO_UP=1 deploy/smoke.sh     test an already running stack
#   SMOKE_KEEP=1 deploy/smoke.sh      leave the stack running afterwards
#   SMOKE_DOWN_VOLUMES=1              remove volumes on teardown (default: keep)
# Requires: docker compose, curl, python3, tar. Uses AUTH_MODE=dev. Never prints tokens.
set -euo pipefail
cd "$(dirname "$0")/.."
COMPOSE=(docker compose -f deploy/docker-compose.yml --env-file "${SMOKE_ENV_FILE:-deploy/.env}")
API="${SMOKE_API_URL:-http://127.0.0.1:${API_PORT:-8000}}"
WEB="${SMOKE_WEB_URL:-http://127.0.0.1:${WEB_PORT:-3000}}"
TMP="$(mktemp -d)"; PASS=0; FAIL=0
cleanup() {
  rm -rf "$TMP"
  if [ "${SMOKE_NO_UP:-0}" != "1" ] && [ "${SMOKE_KEEP:-0}" != "1" ]; then
    "${COMPOSE[@]}" down ${SMOKE_DOWN_VOLUMES:+-v} >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT
ok()   { echo "  ok    $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
check(){ local name="$1" code="$2" want="$3"; [ "$code" = "$want" ] && ok "$name ($code)" || bad "$name (got $code, want $want)"; }
jget() { python3 -c 'import json,sys; d=json.load(sys.stdin)
for k in sys.argv[1].split("."):
    d=d[int(k)] if isinstance(d,list) else d[k]
print(d)' "$1"; }

if [ "${SMOKE_NO_UP:-0}" != "1" ]; then
  [ -f deploy/.env ] || { echo "deploy/.env missing: cp deploy/.env.example deploy/.env and set secrets" >&2; exit 2; }
  echo "== starting stack"; "${COMPOSE[@]}" up -d --build
fi

echo "== waiting for health (max 180s)"
for i in $(seq 1 90); do
  [ "$(curl -s -o /dev/null -w '%{http_code}' "$API/healthz" || true)" = "200" ] && break
  sleep 2; [ "$i" = 90 ] && { echo "api never became healthy" >&2; "${COMPOSE[@]}" logs --tail=50 api worker 2>/dev/null || true; exit 1; }
done
check "GET /healthz" "$(curl -s -o /dev/null -w '%{http_code}' "$API/healthz")" 200
check "GET /readyz"  "$(curl -s -o /dev/null -w '%{http_code}' "$API/readyz")" 200
check "GET web /"    "$(curl -s -o /dev/null -w '%{http_code}' "$WEB/" || true)" 200

echo "== auth"
EMAIL="smoke-$(date +%s)-$RANDOM@example.com"; PW="$(python3 -c 'import secrets;print(secrets.token_urlsafe(18))')"
CREDS="{\"email\":\"$EMAIL\",\"password\":\"$PW\"}"
code=$(curl -s -o "$TMP/signup.json" -w '%{http_code}' -H 'content-type: application/json' -d "$CREDS" "$API/v1/auth/signup")
check "signup" "$code" 201 || true
[ "$code" = 200 ] && { PASS=$((PASS+1)); FAIL=$((FAIL-1)); }   # accept 200 or 201
TOKEN=$(jget access_token < "$TMP/signup.json" 2>/dev/null || jget token < "$TMP/signup.json" 2>/dev/null || true)
if [ -z "$TOKEN" ]; then
  curl -s -H 'content-type: application/json' -d "$CREDS" "$API/v1/auth/login" > "$TMP/login.json"
  TOKEN=$(jget access_token < "$TMP/login.json" 2>/dev/null || jget token < "$TMP/login.json" 2>/dev/null || true)
fi
[ -n "$TOKEN" ] || { echo "no token returned by signup/login" >&2; exit 1; }
H=(-H "Authorization: Bearer $TOKEN")
check "no token -> 401" "$(curl -s -o /dev/null -w '%{http_code}' "$API/v1/me")" 401
check "GET /v1/me" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$API/v1/me")" 200

echo "== workspace and project"
curl -s "${H[@]}" "$API/v1/workspaces" > "$TMP/ws.json"
WID=$(jget items.0.id < "$TMP/ws.json" 2>/dev/null || true)
if [ -z "$WID" ]; then
  WID=$(curl -s "${H[@]}" -H 'content-type: application/json' -d '{"name":"smoke"}' "$API/v1/workspaces" | jget id)
fi
code=$(curl -s -o "$TMP/proj.json" -w '%{http_code}' "${H[@]}" -H 'content-type: application/json' \
  -d '{"name":"smoke-sample"}' "$API/v1/workspaces/$WID/projects")
check "create project" "$code" 201
PID=$(jget id < "$TMP/proj.json")

mkdir -p "$TMP/sample"
printf 'def add(a, b):\n    """Add two numbers."""\n    return a + b\n' > "$TMP/sample/calc.py"
printf '# sample\nTiny project for the smoke test.\n' > "$TMP/sample/README.md"
tar -czf "$TMP/sample.tar.gz" -C "$TMP/sample" .
check "upload source" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" -F "file=@$TMP/sample.tar.gz" "$API/v1/projects/$PID/source")" 201
check "list projects" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$API/v1/workspaces/$WID/projects")" 200
check "estimate" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" -X POST -H 'content-type: application/json' -d '{}' "$API/v1/projects/$PID/estimate")" 200
check "usage" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$API/v1/workspaces/$WID/usage")" 200
check "entitlements" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$API/v1/workspaces/$WID/entitlements")" 200

echo "== isolation"
OTHER="other-$RANDOM@example.com"
curl -s -H 'content-type: application/json' -d "{\"email\":\"$OTHER\",\"password\":\"$PW\"}" "$API/v1/auth/signup" > "$TMP/o.json"
OT=$(jget access_token < "$TMP/o.json" 2>/dev/null || jget token < "$TMP/o.json" 2>/dev/null || true)
if [ -n "$OT" ]; then
  check "other tenant gets 404 on project" "$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $OT" "$API/v1/projects/$PID")" 404
fi

echo "== optional run (needs a model key): SMOKE_RUN=1"
if [ "${SMOKE_RUN:-0}" = "1" ]; then
  code=$(curl -s -o "$TMP/run.json" -w '%{http_code}' "${H[@]}" -H 'content-type: application/json' \
    -d '{"type":"analyze","model_profile":"fast","max_usd":0.5,"incremental":false}' "$API/v1/projects/$PID/runs")
  check "start run" "$code" 202
fi

echo "== delete project (retention check)"
check "delete project" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" -X DELETE "$API/v1/projects/$PID")" 204
check "deleted project is 404" "$(curl -s -o /dev/null -w '%{http_code}' "${H[@]}" "$API/v1/projects/$PID")" 404

echo "== result: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
