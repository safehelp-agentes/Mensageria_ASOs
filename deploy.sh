#!/bin/bash
# deploy.sh — Atualiza o projeto SafeWork no servidor
set -euo pipefail

PROJECT_DIR="/opt/safework/envio_ASO"
VENV="$PROJECT_DIR/.venv"

echo "╔══════════════════════════════════════════╗"
echo "║        Deploy SafeWork — Envio ASO       ║"
echo "╚══════════════════════════════════════════╝"

# ── 1. Atualiza código ────────────────────────────────────────────────────────
echo ""
echo "==> [1/2] Atualizando código (branch: main)..."
cd "$PROJECT_DIR"

git pull origin main

# ── 2. Instala dependências ───────────────────────────────────────────────────
echo ""
echo "==> [2/2] Instalando dependências Python..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt

echo ""
echo "✓ Deploy concluído em $(date '+%d/%m/%Y %H:%M:%S')"
