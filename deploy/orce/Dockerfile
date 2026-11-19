# ORCE (Node-RED) du serveur FAP-light — image custom basée sur l'image ORCE
# officielle, avec les modules runtime du profil light :
#   pg                 : connexions PostgreSQL par rôle (gate/reader/admin)
#   ssh2-sftp-client   : flow d'ingestion SFTP (cycle 60 s)
#   csv-parse          : parser CSV du flow SFTP
#   seedrandom         : simulation déterministe
# /data est bakiné dans l'image (settings.js, flows.json, package.json,
# node_modules, dsp-config) ; un volume nommé monté sur /data en préserve
# le contenu entre les redémarrages.
FROM ecofacis/xfsc-orce:2.0.13

USER root

# outils de build natifs (ssh2) — l'image de base est Alpine
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
