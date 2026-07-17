"""Inbox — serviço único (FastAPI): webhook da Meta + dashboard read-only.

Rotas:
  GET  /webhook          verificação da Meta (hub.challenge)
  POST /webhook          recebe mensagens dos clientes → grava inbound
  GET  /                 lista de conversas (por número)
  GET  /conversa/{num}   timeline de uma conversa (envios + recebidas)

Autenticação do dashboard é feita pelo Traefik (Basic Auth), fora deste app.
O path /webhook fica FORA do Basic Auth (a Meta não manda credenciais).

Rodar local:
    uvicorn inbox.app:app --host 0.0.0.0 --port 8002 --reload
"""
import os
from datetime import datetime, timezone, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, Response
from fastapi.templating import Jinja2Templates

from inbox import repo, webhook
from src.utils.helpers import registrar_erro

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "").strip()

_DIR         = os.path.dirname(os.path.abspath(__file__))
app          = FastAPI(title="SafeWork Inbox", docs_url=None, redoc_url=None)
templates    = Jinja2Templates(directory=os.path.join(_DIR, "templates"))

_BRT = timezone(timedelta(hours=-3))


def _dtfmt(ms) -> str:
    if not ms:
        return ""
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(_BRT).strftime("%d/%m/%Y %H:%M")


templates.env.filters["dtfmt"] = _dtfmt


# ── Webhook ──────────────────────────────────────────────────────────────────

@app.get("/webhook", response_class=PlainTextResponse)
def verificar_webhook(request: Request):
    p = request.query_params
    if p.get("hub.mode") == "subscribe" and p.get("hub.verify_token") == VERIFY_TOKEN:
        return PlainTextResponse(p.get("hub.challenge", ""))
    return PlainTextResponse("forbidden", status_code=403)


@app.post("/webhook")
async def receber_webhook(request: Request):
    # Responde 200 sempre que possível — evita reenvio em massa da Meta.
    try:
        body = await request.json()
    except Exception:
        return Response(status_code=200)

    try:
        for msg in webhook.parse_webhook(body):
            if msg.get("numero"):
                repo.inserir_inbound(msg)
    except Exception as e:
        registrar_erro(f"[inbox webhook] {e}")

    return Response(status_code=200)


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    try:
        conversas = repo.listar_conversas()
        erro = None
    except Exception as e:
        conversas, erro = [], str(e)
    return templates.TemplateResponse(
        request, "conversas.html", {"conversas": conversas, "erro": erro})


@app.get("/conversa/{numero}", response_class=HTMLResponse)
def conversa(request: Request, numero: str):
    try:
        meta, eventos = repo.obter_conversa(numero)
        erro = None
    except Exception as e:
        meta, eventos, erro = {"numero": numero, "empresas": [], "perfil": None,
                               "enviadas": 0, "recebidas": 0}, [], str(e)
    return templates.TemplateResponse(
        request, "conversa.html", {"meta": meta, "eventos": eventos, "erro": erro})


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"
