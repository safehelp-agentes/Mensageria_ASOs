import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from fastapi import FastAPI, BackgroundTasks, Request, Response
from pydantic import BaseModel

from bot import handler
from src.integrations import chatwoot as _chatwoot

_CHATWOOT_WEBHOOK_TOKEN = os.getenv("CHATWOOT_WEBHOOK_TOKEN", "").strip()

app = FastAPI(title="SafeWork Bot", version="1.0")

_BOT_ATIVO = os.getenv("BOT_ATIVO", "true").strip().lower() == "true"

# Quando preenchido, o bot só responde para esses números (modo teste).
# Deixe vazio para liberar para todos.
_BOT_NUMEROS_TESTE: set[str] = {
    n.strip() for n in os.getenv("BOT_NUMEROS_TESTE", "").split(",") if n.strip()
}


def _normalizar(numero: str) -> str:
    import re
    return re.sub(r"\D", "", numero or "")


def _numero_permitido(numero: str) -> bool:
    if not _BOT_NUMEROS_TESTE:
        return True
    numero_norm = _normalizar(numero)
    return any(numero_norm == _normalizar(n) for n in _BOT_NUMEROS_TESTE)


class MensagemEntrada(BaseModel):
    numero:    str
    mensagem:  str
    wamid:     str = ""
    timestamp: int = None


@app.post("/bot/mensagem")
async def receber_mensagem(msg: MensagemEntrada, background_tasks: BackgroundTasks):
    # Espelha no Chatwoot independente do estado do bot
    background_tasks.add_task(_chatwoot.espelhar_inbound, msg.numero, msg.mensagem)

    if not _BOT_ATIVO:
        return {"status": "bot_inativo"}

    if not _numero_permitido(msg.numero):
        return {"status": "numero_nao_permitido"}

    background_tasks.add_task(
        handler.processar_mensagem,
        numero=msg.numero,
        mensagem=msg.mensagem,
        wamid=msg.wamid,
        timestamp=msg.timestamp,
    )
    return {"status": "recebido"}


@app.get("/bot/health")
async def health():
    return {"status": "ok", "bot_ativo": _BOT_ATIVO}


# ── Webhook Chatwoot (agente humano responde no Chatwoot → envia ao WhatsApp) ──

def _enviar_resposta_agente(phone: str, content: str):
    from src.meta.whatsapp import enviar_texto_meta
    from bot.state import registrar_mensagem_bot
    try:
        enviar_texto_meta(phone, content, chatwoot_mirror=False)
        registrar_mensagem_bot(phone, content, tipo="agente")
        print(f"[CHATWOOT] Agente → {phone}: {content[:80]}")
    except Exception as e:
        print(f"[CHATWOOT] Erro ao encaminhar resposta do agente: {e}")


@app.post("/chatwoot/webhook")
async def chatwoot_webhook(request: Request, background_tasks: BackgroundTasks, token: str = ""):
    # Verificação de token (configura CHATWOOT_WEBHOOK_TOKEN no .env e na URL do webhook)
    if _CHATWOOT_WEBHOOK_TOKEN and token != _CHATWOOT_WEBHOOK_TOKEN:
        return Response(status_code=401)

    try:
        data = await request.json()
    except Exception:
        return Response(status_code=400)

    if data.get("event") != "message_created":
        return {"status": "ignored"}

    if data.get("message_type") != "outgoing":
        return {"status": "ignored"}

    # Mensagens criadas pelo pipeline (envio automático de ASOs) — não reencaminhar ao WhatsApp
    if (data.get("additional_attributes") or {}).get("source") == "safework_pipeline":
        return {"status": "ignored"}

    msg_id = data.get("id", 0)

    # Mensagem criada pelo próprio bot — ignora para não criar loop
    if _chatwoot.is_bot_message(msg_id):
        _chatwoot.consume_bot_message(msg_id)
        return {"status": "ignored_echo"}

    phone = (
        data.get("conversation", {})
            .get("meta", {})
            .get("sender", {})
            .get("phone_number", "")
    )
    if not phone:
        return {"status": "no_phone"}

    content = (data.get("content") or "").strip()
    if not content:
        return {"status": "no_content"}

    # Remove o + para o formato esperado pelo Meta
    phone_meta = phone.lstrip("+")

    background_tasks.add_task(_enviar_resposta_agente, phone_meta, content)
    return {"status": "queued"}
