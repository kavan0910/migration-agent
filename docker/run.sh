#!/usr/bin/env bash
# Run the migration-agent worker in a locked-down container.
#   docker/run.sh <repo-dir> [agent args...]
# Offline by default (--network none). To permit model access set
# MODEL_PROXY_NETWORK to an --internal network that only a allowlisting proxy
# joins (see docker/docker-compose.yml and src/migration_agent/sandbox/README.md).
set -euo pipefail

REPO="${1:?usage: run.sh <repo-dir> [args...]}"; shift || true
IMAGE="${IMAGE:-migration-agent-sandbox:latest}"
NETWORK="${MODEL_PROXY_NETWORK:-none}"

ARGS=(--rm --init
  --read-only
  --tmpfs /tmp:rw,noexec,nosuid,size=64m
  --tmpfs /scratch:rw,noexec,nosuid,size=256m,uid=10001,gid=10001
  --cap-drop ALL
  --security-opt no-new-privileges:true
  --pids-limit 256 --memory 2g --cpus 2
  --user 10001:10001
  --network "$NETWORK"
  -v "$(cd "$REPO" && pwd)":/workspace:ro)

if [ "$NETWORK" != "none" ]; then
  # Only the proxy can reach the internet; the worker must use it.
  ARGS+=(-e HTTPS_PROXY="${HTTPS_PROXY:-http://egress-proxy:3128}" -e HTTP_PROXY="${HTTPS_PROXY:-http://egress-proxy:3128}")
  # Pass the key by name only (value comes from the caller's env, never the image).
  [ -n "${ANTHROPIC_API_KEY:-}" ] && ARGS+=(-e ANTHROPIC_API_KEY)
fi

exec docker run "${ARGS[@]}" "$IMAGE" "$@"
