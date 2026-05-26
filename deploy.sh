#!/bin/bash
# deploy.sh — Atualiza o projeto SafeWork no servidor
set -euo pipefail

PROJECT_DIR="/opt/safework/envio_ASO"
VENV="$PROJECT_DIR/.venv"
BRANCH="${1:-SecretariaEletronica}"   # branch padrão, pode passar outra como argumento
BOT_SERVICE="envio-aso"               # nome do serviço systemd do bot

echo "╔══════════════════════════════════════════╗"
echo "║        Deploy SafeWork — ASO Bot         ║"
echo "╚══════════════════════════════════════════╝"

# ── 1. Atualiza código ────────────────────────────────────────────────────────
echo ""
echo "==> [1/4] Atualizando código (branch: $BRANCH)..."
cd "$PROJECT_DIR"

# Protege alterações locais não commitadas
if ! git diff --quiet; then
    echo "     AVISO: arquivos locais modificados. Guardando com stash..."
    git stash push -m "deploy-auto-$(date +%Y%m%d-%H%M%S)"
fi

git pull origin "$BRANCH"

# ── 2. Instala dependências ───────────────────────────────────────────────────
echo ""
echo "==> [2/4] Instalando dependências Python..."
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet -r requirements.txt
"$VENV/bin/pip" install --quiet -r bot_requirements.txt

# ── 3. Reinicia o bot ─────────────────────────────────────────────────────────
echo ""
echo "==> [3/4] Reiniciando serviço do bot..."
if systemctl is-active --quiet "$BOT_SERVICE"; then
    systemctl restart "$BOT_SERVICE"
    echo "     Serviço '$BOT_SERVICE' reiniciado com sucesso."
else
    echo "     AVISO: serviço '$BOT_SERVICE' não está ativo. Iniciando..."
    systemctl start "$BOT_SERVICE"
fi

# ── 4. Verifica saúde ─────────────────────────────────────────────────────────
echo ""
echo "==> [4/4] Verificando saúde do bot..."
sleep 3
if curl -sf http://127.0.0.1:8001/health > /dev/null 2>&1; then
    echo "     Bot respondendo em :8001 ✓"
else
    echo "     AVISO: bot não respondeu em /health — verifique os logs:"
    echo "     journalctl -u $BOT_SERVICE -n 30 --no-pager"
fi

echo ""
echo "✓ Deploy concluído em $(date '+%d/%m/%Y %H:%M:%S')"
