"""Inbox — serviço único (FastAPI): webhook da Meta + dashboard read-only.

Rotas:
  GET  /webhook            verificação da Meta (hub.challenge)
  POST /webhook            recebe mensagens dos clientes → grava inbound
  GET  /                   SPA (dashboard estilo CRM)
  GET  /api/conversas      JSON: lista de conversas (por número)
  GET  /api/conversa/{num} JSON: meta + timeline de uma conversa
  GET  /health             ok

O front (SPA) consome apenas a API JSON deste backend — nunca o Supabase direto.
A service_role fica server-side. Autenticação do dashboard é feita pelo Traefik
(Basic Auth); o path /webhook fica FORA do Basic Auth.

Rodar local:
    uvicorn inbox.app:app --host 0.0.0.0 --port 8002 --reload
"""
import os

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse, Response, FileResponse

from inbox import repo, webhook
from src.utils.helpers import registrar_erro

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "").strip()

_DIR   = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_DIR, "templates", "index.html")
app    = FastAPI(title="SafeWork Inbox", docs_url=None, redoc_url=None)


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


# ── Dashboard (SPA + API JSON) ───────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
def home():
    return FileResponse(_INDEX, media_type="text/html")


@app.get("/api/conversas")
def api_conversas():
    try:
        return JSONResponse(repo.listar_conversas())
    except Exception as e:
        registrar_erro(f"[inbox api conversas] {e}")
        return JSONResponse({"erro": str(e)}, status_code=500)


@app.get("/api/conversa/{numero}")
def api_conversa(numero: str):
    try:
        return JSONResponse(repo.obter_conversa(numero))
    except Exception as e:
        registrar_erro(f"[inbox api conversa] {e}")
        return JSONResponse({"erro": str(e)}, status_code=500)


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"
