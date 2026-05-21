import sys
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from dotenv import load_dotenv
load_dotenv(os.path.join(_ROOT, ".env"))

from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

from bot import handler

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
