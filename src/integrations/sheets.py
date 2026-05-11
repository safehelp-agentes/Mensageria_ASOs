import os
import json
import base64
import time
import requests

from config import (
    SHEETS_CREDENTIALS_FILE, SHEETS_SPREADSHEET_ID, SHEETS_ABA, SHEETS_ENVIAR,
)
from src.utils.helpers import _requisicao_com_retry, registrar_erro


# ── Autenticação Google ────────────────────────────────────────────────────────

def _obter_token_google() -> str:
    if not os.path.exists(SHEETS_CREDENTIALS_FILE):
        raise FileNotFoundError(f"Credenciais não encontradas: {SHEETS_CREDENTIALS_FILE}")

    with open(SHEETS_CREDENTIALS_FILE, "r", encoding="utf-8") as f:
        creds = json.load(f)

    now     = int(time.time())
    header  = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss":   creds["client_email"],
        "scope": "https://www.googleapis.com/auth/spreadsheets",
        "aud":   "https://oauth2.googleapis.com/token",
        "iat":   now,
        "exp":   now + 3600,
    }

    def b64url(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")

    header_b64  = b64url(json.dumps(header,  separators=(",", ":")).encode())
    payload_b64 = b64url(json.dumps(payload, separators=(",", ":")).encode())
    msg_to_sign = f"{header_b64}.{payload_b64}".encode("utf-8")

    from cryptography.hazmat.primitives            import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    private_key = serialization.load_pem_private_key(
        creds["private_key"].encode("utf-8"), password=None
    )
    signature = private_key.sign(msg_to_sign, padding.PKCS1v15(), hashes.SHA256())
    jwt_token = f"{header_b64}.{payload_b64}.{b64url(signature)}"

    resp = _requisicao_com_retry(
        requests.post,
        "https://oauth2.googleapis.com/token",
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion":  jwt_token,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Escrita na planilha ────────────────────────────────────────────────────────

def registrar_no_sheets(linhas: list):
    if not SHEETS_ENVIAR:
        print("[SHEETS] Integração desabilitada.")
        return
    if not SHEETS_SPREADSHEET_ID:
        registrar_erro("[SHEETS] SHEETS_SPREADSHEET_ID não configurado.")
        return
    if not linhas:
        print("[SHEETS] Nenhuma linha para registrar.")
        return

    try:
        token = _obter_token_google()
        url   = (
            f"https://sheets.googleapis.com/v4/spreadsheets/{SHEETS_SPREADSHEET_ID}"
            f"/values/{requests.utils.quote(SHEETS_ABA)}!A1:F1"
            f":append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS"
        )
        resp = _requisicao_com_retry(
            requests.post, url,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"values": linhas},
            timeout=30,
        )
        resp.raise_for_status()
        updates = resp.json().get("updates", {})
        print(f"[SHEETS] {updates.get('updatedRows', len(linhas))} linha(s) inserida(s).")
    except Exception as e:
        registrar_erro(f"[SHEETS] Erro: {e}")
        print(f"[SHEETS] ERRO: {e}")


# ── Montagem de linhas ─────────────────────────────────────────────────────────

def montar_linhas_sheets(resumo: list, data_execucao: str) -> list:
    linhas = []
    for resultado in resumo:
        linhas.append([
            data_execucao,
            resultado.get("empresa", ""),
            resultado.get("nome_empresa", ""),
            resultado.get("meta_enviados_ok", 0),
            0,  # pendentes agora gerenciados via Supabase
            resultado.get("erros", 0) + resultado.get("meta_enviados_erro", 0),
        ])
    return linhas
