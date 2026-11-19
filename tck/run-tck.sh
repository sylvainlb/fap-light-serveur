#!/usr/bin/env bash
# ============================================================================
# TCK serveur FAP-light — point d'entrée (spec fap-light-spec-serveur.md §10)
#
# Usage :
#   ./run-tck.sh                          # toutes les suites, tck.properties
#   ./run-tck.sh --suites CA-S1,CA-S4     # sous-ensemble
#   ./run-tck.sh --list                   # liste les tests sans les exécuter
#   ./run-tck.sh --properties tck.properties.local
#
# Prérequis côté lanceur : python3, psql (ou docker si pg.cmd est un wrapper
# docker compose), client sftp/ssh avec la clé TCK (sftp.key).
# La cible (FAP-light déployée) est décrite par les propriétés / env TCK_*.
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")"

CONFIG="${TCK_PROPERTIES:-tck.properties}"

if ! command -v python3 >/dev/null 2>&1; then
  echo "ERREUR: python3 requis." >&2; exit 2
fi
if ! command -v psql >/dev/null 2>&1 && ! grep -qE '^pg\.cmd=.*docker' "$CONFIG"; then
  echo "ATTENTION: psql introuvable et pg.cmd ne semble pas être un wrapper docker." >&2
  echo "  Installer postgresql-client, ou passer pg.cmd par docker compose exec." >&2
fi
if ! command -v sftp >/dev/null 2>&1; then
  echo "ATTENTION: client sftp introuvable — la suite CA-S2 sera en erreur." >&2
fi

echo "== TCK FAP-light — $(date -u +%Y-%m-%dT%H:%M:%SZ) =="
exec python3 harness/runner.py --properties "$CONFIG" "$@"
