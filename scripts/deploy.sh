#!/usr/bin/env bash
# ============================================================================
# FAP-light server deployment (1 VM): docker compose up + health wait.
# Usage: ./scripts/deploy.sh
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "[deploy] .env missing — copying .env.example (edit it afterwards)"
  cp .env.example .env
  echo "[deploy] Edit .env then re-run ./scripts/deploy.sh"
  exit 0
fi
set -a; . ./.env; set +a

# Test SSH key for the SFTP drop (key auth on the TCK client side):
# the key pair is generated in deploy/sftp/keys and the public key installed
# in deploy/sftp/authorized_keys/<user> (mounted into the sftp container).
SFTP_USER="${SFTP_USER:-fap}"
KEYS_DIR="deploy/sftp/keys"
mkdir -p "$KEYS_DIR" deploy/sftp/authorized_keys
if [ ! -f "$KEYS_DIR/tck_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N '' -C 'tck-fap-light' -f "$KEYS_DIR/tck_ed25519"
  cp "$KEYS_DIR/tck_ed25519.pub" "deploy/sftp/authorized_keys/$SFTP_USER"
  echo "[deploy] SFTP key pair generated in $KEYS_DIR (private key for test use only)"
fi

echo "[deploy] build + up"
docker compose up -d --build

echo "[deploy] waiting for service health…"
./scripts/wait-healthy.sh

# Installs the TCK public key into the SFTP chroot (sshd reads .ssh/authorized_keys
# in the %h chroot; mounting /etc/ssh/authorized_keys alone is not enough).
if docker compose ps sftp --format json 2>/dev/null | grep -q Running; then
  docker compose exec -T sftp sh -c \
    "mkdir -p /home/$SFTP_USER/.ssh && cp /etc/ssh/authorized_keys/$SFTP_USER /home/$SFTP_USER/.ssh/authorized_keys && chown -R 1000:100 /home/$SFTP_USER/.ssh && chmod 700 /home/$SFTP_USER/.ssh && chmod 600 /home/$SFTP_USER/.ssh/authorized_keys" \
    && echo "[deploy] SFTP public key installed for user $SFTP_USER"
fi

echo
echo "[deploy] FAP-light started:"
echo "  ORCE (Node-RED)     : http://localhost:${ORCE_PORT:-1880}"
echo "  PostgreSQL          : localhost:${PG_PORT:-5432} (db fap, roles admin/gate/reader/app)"
echo "  DSP data plane      : POST http://localhost:${ORCE_PORT:-1880}/dsp/data (HMAC, see tck/)"
echo "  DSP catalog         : POST http://localhost:${ORCE_PORT:-1880}/dsp/catalogue/request"
echo "  MQTT                : localhost:${MQTT_PORT:-1883}"
echo "  SFTP (source)       : localhost:${SFTP_PUBLISHED_PORT:-2222} (user ${SFTP_USER}, dirs /ingest /archive)"
echo "  Keycloak (IdP)      : http://localhost:${KEYCLOAK_PORT:-8080} (realm fap-light)"
echo "  caddy (TLS front end): https://${FAP_DOMAIN:-localhost}"
echo
echo "[deploy] TCK: cd tck && ./run-tck.sh --properties tck.properties (see tck/README.md)"
