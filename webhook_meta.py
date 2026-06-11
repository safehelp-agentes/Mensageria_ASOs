import os
import json
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

SUPABASE_URL  = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_KEY  = os.getenv("SUPABASE_SECRET_KEY", "").strip()
VERIFY_TOKEN  = os.getenv("WEBHOOK_VERIFY_TOKEN", "safework_meta_prospec_v1_b7k2p9x")
BOT_URL       = os.getenv("BOT_URL", "http://172.17.0.1:8001/bot/mensagem")
PORT          = int(os.getenv("PORT", "8001"))


def _supabase_headers():
    return {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
    }


def _registrar_inbound(numero: str, conteudo: str, wamid: str = ""):
    try:
        requests.post(
            f"{SUPABASE_URL}/rest/v1/mensagens",
            headers={**_supabase_headers(), "Prefer": "return=minimal"},
            json={
                "numero_whatsapp": numero,
                "direcao":         "inbound",
                "tipo":            "texto",
                "conteudo":        conteudo,
                "wamid":           wamid,
            },
            timeout=10,
        )
    except Exception as e:
        print(f"[SUPABASE] Erro ao registrar: {e}")


def _encaminhar_bot(numero: str, mensagem: str, wamid: str = "", timestamp: int = None):
    try:
        resp = requests.post(
            BOT_URL,
            json={"numero": numero, "mensagem": mensagem, "wamid": wamid, "timestamp": timestamp},
            timeout=30,
        )
        print(f"[BOT] {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"[BOT] Erro ao encaminhar: {e}")


class WebhookHandler(BaseHTTPRequestHandler):

    def log_message(self, fmt, *args):
        print(f"[HTTP] {fmt % args}")

    def do_GET(self):
        params    = parse_qs(urlparse(self.path).query)
        mode      = params.get("hub.mode",         [None])[0]
        token     = params.get("hub.verify_token", [None])[0]
        challenge = params.get("hub.challenge",    [None])[0]

        if mode == "subscribe" and token == VERIFY_TOKEN and challenge:
            print("[WEBHOOK] Verificação Meta OK")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(challenge.encode())
        else:
            print(f"[WEBHOOK] Verificação falhou — token={token!r}")
            self.send_response(403)
            self.end_headers()

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)

        # Responde 200 imediatamente (obrigatório pela Meta)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"OK")

        try:
            data = json.loads(body)
        except Exception as e:
            print(f"[WEBHOOK] JSON inválido: {e}")
            return

        if data.get("object") != "whatsapp_business_account":
            return

        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value", {})

                for msg in value.get("messages", []):
                    tipo   = msg.get("type", "")
                    numero = msg.get("from", "")
                    wamid  = msg.get("id", "")
                    ts     = int(msg.get("timestamp") or 0) or None

                    if tipo == "text":
                        texto = msg.get("text", {}).get("body", "")
                    else:
                        print(f"[WEBHOOK] Tipo não tratado: {tipo} — ignorando")
                        continue

                    print(f"[WEBHOOK] {numero}: {texto[:80]}")
                    _registrar_inbound(numero, texto, wamid)
                    _encaminhar_bot(numero, texto, wamid, ts)


if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", PORT), WebhookHandler)
    print(f"[WEBHOOK] Rodando na porta {PORT}")
    server.serve_forever()
