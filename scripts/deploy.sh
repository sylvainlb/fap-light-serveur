#!/usr/bin/env bash
# ============================================================================
# Déploiement du serveur FAP-light (1 VM) : docker compose up + attente santé.
# Usage : ./scripts/deploy.sh
# ============================================================================
set -Eeuo pipefail
cd "$(dirname "$0")/.."

if [ ! -f .env ]; then
  echo "[deploy] .env absent — copie de .env.example (à éditer ensuite)"
  cp .env.example .env
  echo "[deploy] Éditez .env puis relancez ./scripts/deploy.sh"
  exit 0
fi
set -a; . ./.env; set +a

# Clé SSH de test pour le dépôt SFTP (auth par clé côté client TCK) :
# la paire est générée dans deploy/sftp/keys et la clé publique installée
# dans deploy/sftp/authorized_keys/<user> (montée dans le conteneur sftp).
SFTP_USER="${SFTP_USER:-fap}"
KEYS_DIR="deploy/sftp/keys"
mkdir -p "$KEYS_DIR" deploy/sftp/authorized_keys
if [ ! -f "$KEYS_DIR/tck_ed25519" ]; then
  ssh-keygen -q -t ed25519 -N '' -C 'tck-fap-light' -f "$KEYS_DIR/tck_ed25519"
  cp "$KEYS_DIR/tck_ed25519.pub" "deploy/sftp/authorized_keys/$SFTP_USER"
  echo "[deploy] paire de clés SFTP générée dans $KEYS_DIR (clé privée à usage test uniquement)"
fi

echo "[deploy] build + up"
docker compose up -d --build

echo "[deploy] attente de la santé des services…"
./scripts/wait-healthy.sh

# Installe la clé publique TCK dans le chroot SFTP (sshd lit .ssh/authorized_keys
# dans le chroot %h ; le montage /etc/ssh/authorized_keys seul ne suffit pas).
if docker compose ps sftp --format json 2>/dev/null | grep -q Running; then
  docker compose exec -T sftp sh -c \
    "mkdir -p /home/$SFTP_USER/.ssh && cp /etc/ssh/authorized_keys/$SFTP_USER /home/$SFTP_USER/.ssh/authorized_keys && chown -R 1000:100 /home/$SFTP_USER/.ssh && chmod 700 /home/$SFTP_USER/.ssh && chmod 600 /home/$SFTP_USER/.ssh/authorized_keys" \
    && echo "[deploy] clé publique SFTP installée pour l'utilisateur $SFTP_USER"
fi

echo
echo "[deploy] FAP-light démarré :"
echo "  ORCE (Node-RED)     : http://localhost:${ORCE_PORT:-1880}"
echo "  PostgreSQL          : localhost:${PG_PORT:-5432} (db fap, rôles admin/gate/reader/app)"
echo "  Data plane DSP      : POST http://localhost:${ORCE_PORT:-1880}/dsp/data (HMAC, voir tck/)"
echo "  Catalogue DSP       : POST http://localhost:${ORCE_PORT:-1880}/dsp/catalogue/request"
echo "  MQTT                : localhost:${MQTT_PORT:-1883}"
echo "  SFTP (source)       : localhost:${SFTP_PUBLISHED_PORT:-2222} (user ${SFTP_USER}, dirs /ingest /archive)"
echo "  Keycloak (IdP)      : http://localhost:${KEYCLOAK_PORT:-8080} (realm fap-light)"
echo "  caddy (TLS frontal) : https://${FAP_DOMAIN:-localhost}"
echo
echo "[deploy] TCK : cd tck && ./run-tck.sh --properties tck.properties (voir tck/README.md)"
