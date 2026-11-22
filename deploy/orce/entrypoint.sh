#!/usr/bin/env bash
# FAP-light ORCE entrypoint: /data is baked into the image (settings, flows,
# modules); we just make sure of the owner then delegate to the original
# ORCE entrypoint (which starts Node-RED with --userDir /data).
set -Eeuo pipefail
chown -R node-red:node-red /data 2>/dev/null || true
exec /usr/src/node-red/entrypoint.sh "$@"
