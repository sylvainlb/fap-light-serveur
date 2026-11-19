#!/usr/bin/env bash
# Entrypoint FAP-light ORCE : /data est bakiné dans l'image (settings, flows,
# modules) ; on s'assure juste du propriétaire puis on délègue à l'entrypoint
# ORCE d'origine (qui démarre Node-RED avec --userDir /data).
set -Eeuo pipefail
chown -R node-red:node-red /data 2>/dev/null || true
exec /usr/src/node-red/entrypoint.sh "$@"
