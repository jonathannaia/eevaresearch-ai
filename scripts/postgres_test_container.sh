#!/usr/bin/env bash
set -euo pipefail

# Local disposable Postgres test infrastructure only — this is NOT Neon,
# NOT production, and NOT wired into any application/runtime path. It
# starts exactly one loopback-only, --rm Postgres container matching the
# existing tests/_postgres_test_support.py fixture contract (host, port,
# database, role), runs the caller-supplied command with a fresh,
# process-local test password, and removes the container on exit —
# regardless of whether the caller command succeeds or fails.
#
# Each invocation of this script still requires explicit human approval;
# the script's mere existence does not authorize Docker use on its own.
# It must never be used for Neon, production, source scans, UI/runtime
# startup, deployment, or automatic backend activation.
#
# Usage:
#   scripts/postgres_test_container.sh <command> [args...]
#
# Example (illustrative only — not invoked by this script itself):
#   scripts/postgres_test_container.sh \
#     .venv/bin/python3 -m pytest tests/test_backend_factory_postgres.py -q

CONTAINER_NAME="eevaresearch-postgres-test-phase4b"
IMAGE="postgres:16.8"
HOST_BIND="127.0.0.1"
HOST_PORT="55432"
CONTAINER_PORT="5432"
DB_NAME="eevaresearch_test_phase4b"
DB_ROLE="eevaresearch_test_user"
PASSWORD_VAR="EEVARESEARCH_PG_TEST_PASSWORD"
READINESS_TIMEOUT_SECONDS=60

if [ "$#" -eq 0 ]; then
  echo "Usage: $0 <command> [args...]" >&2
  echo "Starts a local disposable Postgres test container, runs <command> with" >&2
  echo "${PASSWORD_VAR} set for it only, then removes the container." >&2
  exit 1
fi

# Read-only Docker availability check — does not create or modify anything.
if ! docker version >/dev/null 2>&1; then
  echo "Docker is not available. Aborting before any container action." >&2
  exit 1
fi

# Read-only existence check for exactly this container name — never a
# broad `docker ps`/`docker ps -a` sweep of unrelated containers.
if docker ps -a --filter "name=^${CONTAINER_NAME}\$" --format '{{.Names}}' | grep -qx "${CONTAINER_NAME}"; then
  echo "A container named '${CONTAINER_NAME}' already exists. Refusing to start, stop, or alter it. Aborting." >&2
  exit 1
fi

# Fresh, process-local password only — never printed, never written to
# any file, never accepted as a command-line argument, no fixed/default
# value.
TEST_PASSWORD="$(openssl rand -base64 24)"

docker run -d --rm \
  --name "${CONTAINER_NAME}" \
  -e POSTGRES_USER="${DB_ROLE}" \
  -e POSTGRES_PASSWORD="${TEST_PASSWORD}" \
  -e POSTGRES_DB="${DB_NAME}" \
  -p "${HOST_BIND}:${HOST_PORT}:${CONTAINER_PORT}" \
  "${IMAGE}" >/dev/null

# Registered immediately after a successful start: runs on normal exit,
# a failing caller command (set -e), or interruption (Ctrl-C/TERM), and
# tolerates the container already being gone. Never masks the caller
# command's own exit status — it only stops the container as a side
# effect and does not call `exit` itself.
cleanup() {
  docker stop "${CONTAINER_NAME}" >/dev/null 2>&1 || true
}
trap cleanup EXIT

start_ts=$(date +%s)
until docker exec "${CONTAINER_NAME}" pg_isready -U "${DB_ROLE}" -d "${DB_NAME}" >/dev/null 2>&1; do
  now_ts=$(date +%s)
  elapsed=$(( now_ts - start_ts ))
  if [ "${elapsed}" -ge "${READINESS_TIMEOUT_SECONDS}" ]; then
    echo "Local disposable Postgres test container did not become ready within ${READINESS_TIMEOUT_SECONDS}s. Aborting." >&2
    exit 1
  fi
  sleep 1
done

# --- Execution: local disposable test infrastructure only. ---
# This script is not Neon, not production, and not automatic backend
# activation — it exists solely to make the existing pg_isolated_dsn/
# pg_isolated_connection test fixtures runnable, and every invocation
# still requires its own explicit human approval. Only the one
# documented test-password variable is exported, only for the caller
# command below, never anything else (no EDGE_DB_BACKEND,
# EDGE_STATE_DB_URL, EEVA_HOSTED_VALIDATION_DSN, or any DSN).
env "${PASSWORD_VAR}=${TEST_PASSWORD}" "$@"
