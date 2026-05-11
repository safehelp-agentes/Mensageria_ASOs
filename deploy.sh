#!/bin/bash
set -e

PROJECT_DIR="/opt/safework"
VENV="$PROJECT_DIR/.venv"

echo "==> Atualizando código..."
cd "$PROJECT_DIR"
git pull origin main

echo "==> Instalando dependências..."
"$VENV/bin/pip" install -r requirements.txt -q

echo "==> Reiniciando webhook..."
docker compose -f /docker/n8n/docker-compose.yml restart webhook-aso

echo "==> Deploy concluído!"
