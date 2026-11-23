# Pile IA — chantier 5 (spec app-ai-component-editor.md rev 5, §3.0) :
# service dédié du dépôt fap-light-serveur, activé par configuration
# (AI_ENABLED=1 dans docker-compose), routé par caddy sur /ai/*.
# J15 : moteur d'exécution, POST /ai/run, sorties texte.
FROM python:3.14-slim

WORKDIR /srv/ai
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY schema ./schema

ENV AI_PORT=8080 \
    AI_STATE_DIR=/data \
    LDP_BASE_URL=http://caddy \
    AI_MODELS_CONTAINER=/ai_models/ \
    AI_JWT_VERIFY=0

EXPOSE 8080
VOLUME /data

CMD ["python", "-m", "app.server"]
