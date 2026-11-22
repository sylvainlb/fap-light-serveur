#!/usr/bin/env bash
# ============================================================================
# FAP-light server TCK — entry point (spec fap-light-spec-serveur.md §10)
#
# Usage:
#   ./run-tck.sh                          # all suites, tck.properties
#   ./run-tck.sh --suites CA-S1,CA-S4     # subset
#   ./run-tck.sh --list                   # list tests without running them
#   ./run-tck.sh --properties tck.properties.local
#
# Launcher-side prerequisites: python3, psql (or docker if pg.cmd is a
# docker compose wrapper), sftp/ssh client with the TCK key (sftp.key).
# The target (deployed FAP-light) is described by the properties / TCK_* env.
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

CONFIG="${TCK_PROPERTIES:-tck.properties}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 required." >&2; exit 2
fi
if ! command -v psql >/dev/null 2>&1 && ! grep -qE '^pg\.cmd=.*docker' "$CONFIG"; then
  echo "WARNING: psql not found and pg.cmd does not look like a docker wrapper." >&2
  echo "  Install postgresql-client, or route pg.cmd through docker compose exec." >&2
fi
if ! command -v sftp >/dev/null 2>&1; then
  echo "WARNING: sftp client not found — the CA-S2 suite will error out." >&2
fi

echo "== TCK FAP-light — $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
exec python3 harness/runner.py --properties "$CONFIG" "$@"
