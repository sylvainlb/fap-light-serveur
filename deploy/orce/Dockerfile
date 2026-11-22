# ORCE (Node-RED) of the FAP-light server — custom image based on the official
# ORCE image, with the runtime modules of the light profile:
#   pg                 : PostgreSQL connections per role (gate/reader/admin)
#   ssh2-sftp-client   : SFTP ingestion flow (60 s cycle)
#   csv-parse          : CSV parser of the SFTP flow
#   seedrandom         : deterministic simulation
# /data is baked into the image (settings.js, flows.json, package.json,
# node_modules, dsp-config); a named volume mounted on /data preserves
# its content across restarts.
FROM ecofacis/xfsc-orce:2.0.13

USER root

# native build tools (ssh2) — the base image is Alpine
RUN apk add --no-cache python3 make g++

COPY data/package.json /data/package.json
RUN cd /data && npm install --omit=dev --no-audit --no-fund

COPY data/settings.js /data/settings.js
COPY data/flows.json /data/flows.json
COPY data/dsp-config /data/dsp-config

COPY entrypoint.sh /usr/local/bin/entrypoint-light.sh
RUN chmod +x /usr/local/bin/entrypoint-light.sh \
    && chown -R node-red:node-red /data

ENTRYPOINT ["/usr/local/bin/entrypoint-light.sh"]
