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

from fastapi import FastAPI, Request, Header
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse, Response, FileResponse

from inbox import repo, webhook
from src.utils.helpers import registrar_erro

VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "").strip()
INTERNAL_TOKEN = os.getenv("INBOX_INTERNAL_TOKEN", "").strip()

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


@app.post("/api/internal/mensagem")
async def registrar_mensagem_interna(
    request: Request,
    authorization: str | None = Header(default=None),
    x_internal_token: str | None = Header(default=None),
):
    if not INTERNAL_TOKEN:
        return JSONResponse({"erro": "INBOX_INTERNAL_TOKEN nao configurado"}, status_code=503)

    bearer = ""
    if authorization and authorization.lower().startswith("bearer "):
        bearer = authorization[7:].strip()
    token = x_internal_token or bearer
    if token != INTERNAL_TOKEN:
        return JSONResponse({"erro": "unauthorized"}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"erro": "json invalido"}, status_code=400)

    numero = str(body.get("numero") or body.get("numero_whatsapp") or "").strip()
    if not numero:
        return JSONResponse({"erro": "numero obrigatorio"}, status_code=400)

    try:
        status = repo.inserir_mensagem({
            "direcao": body.get("direcao") or "inbound",
            "numero": numero,
            "nome_perfil": body.get("nome_perfil") or body.get("nome_empresa"),
            "tipo": body.get("tipo") or "text",
            "conteudo": body.get("conteudo"),
            "nome_arquivo": body.get("nome_arquivo"),
            "wamid": body.get("wamid"),
            "timestamp_ms": body.get("timestamp_ms") or body.get("timestamp_meta"),
        })
        return JSONResponse({"ok": True, "status": status})
    except Exception as e:
        registrar_erro(f"[inbox internal mensagem] {e}")
        return JSONResponse({"erro": str(e)}, status_code=500)


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


@app.get("/api/dashboard")
def api_dashboard(inicio: str = "", fim: str = "", empresa: str = ""):
    try:
        return JSONResponse(repo.dashboard_dados(inicio, fim, empresa))
    except Exception as e:
        registrar_erro(f"[inbox api dashboard] {e}")
        return JSONResponse({"erro": str(e)}, status_code=500)


@app.get("/api/asos")
def api_asos(status: str = "enviados", q: str = ""):
    try:
        return JSONResponse(repo.listar_asos(status, q))
    except Exception as e:
        registrar_erro(f"[inbox api asos] {e}")
        return JSONResponse({"erro": str(e)}, status_code=500)


@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"
