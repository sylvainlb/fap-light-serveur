# AI stack — workstream 5 (spec app-ai-component-editor.md rev 5, §3.0):
# dedicated service of the fap-light-serveur repository, enabled by configuration
# (AI_ENABLED=1 in docker-compose), routed by caddy on /ai/*.
# J15: execution engine, POST /ai/run, text outputs.
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
