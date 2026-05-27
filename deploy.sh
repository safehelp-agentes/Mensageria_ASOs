#!/bin/bash
# deploy.sh — Atualiza o projeto SafeWork no servidor
set -euo pipefail

PROJECT_DIR="/opt/safework/envio_ASO"
VENV="$PROJECT_DIR/.venv"
CRM_DIR="/opt/safework/crm"
BOT_SERVICE="envio-aso"

echo "╔══════════════════════════════════════════╗"
echo "║        Deploy SafeWork — ASO Bot         ║"
echo "╚══════════════════════════════════════════╝"

# ── 1. Atualiza código ────────────────────────────────────────────────────────
echo ""
echo "==> [1/4] Atualizando código (branch: main)..."
cd "$PROJECT_DIR"

# Garante que o index.html está limpo (sem build anterior com credenciais)
git checkout index.html 2>/dev/null || true

git pull origin main

# ── 2. Instala dependências ───────────────────────────────────────────────────
echo ""
echo "==> [2/4] Instalando dependências Python..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt
"$VENV/bin/pip" install --quiet -r bot_requirements.txt

# ── 3. Atualiza o CRM ────────────────────────────────────────────────────────
echo ""
echo "==> [3/4] Atualizando CRM..."

if [ ! -f "$PROJECT_DIR/.env" ]; then
    echo "     AVISO: .env não encontrado — pulando build do CRM."
else
    # Carrega variáveis do .env (ignora comentários e linhas vazias)
    set -a
    # shellcheck disable=SC1090
    source <(grep -v '^\s*#' "$PROJECT_DIR/.env" | grep -v '^\s*$')
    set +a

    "$VENV/bin/python" build.py

    mkdir -p "$CRM_DIR"
    cp index.html "$CRM_DIR/"
    # Restaura o template limpo para não commitar credenciais acidentalmente
    git checkout index.html

    echo "     CRM atualizado em $CRM_DIR/index.html ✓"
fi

# ── 4. Reinicia o bot ─────────────────────────────────────────────────────────
echo ""
echo "==> [4/4] Reiniciando serviço do bot..."
if systemctl is-active --quiet "$BOT_SERVICE"; then
    systemctl restart "$BOT_SERVICE"
    echo "     Serviço '$BOT_SERVICE' reiniciado com sucesso."
else
    echo "     AVISO: serviço '$BOT_SERVICE' não está ativo. Iniciando..."
    systemctl start "$BOT_SERVICE"
fi

# ── Verificação final ─────────────────────────────────────────────────────────
echo ""
sleep 3
if curl -sf http://127.0.0.1:8001/bot/health > /dev/null 2>&1; then
    echo "     Bot respondendo em :8001/bot/health ✓"
else
    echo "     AVISO: bot não respondeu em /bot/health — verifique os logs:"
    echo "     journalctl -u $BOT_SERVICE -n 30 --no-pager"
fi

echo ""
echo "✓ Deploy concluído em $(date '+%d/%m/%Y %H:%M:%S')"
