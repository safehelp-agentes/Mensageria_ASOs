import os
import requests

from src.utils.helpers import _requisicao_com_retry

SUPABASE_URL        = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "").strip()
SUPABASE_ATIVO      = bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _headers():
    return {
        "apikey":        SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type":  "application/json",
    }


def _url(tabela: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{tabela}"


def _data_para_iso(data_str: str) -> str | None:
    """Converte DD/MM/YYYY ou DD-MM-YYYY para YYYY-MM-DD."""
    if not data_str:
        return None
    try:
        partes = data_str.replace("/", "-").split("-")
        if len(partes) == 3 and len(partes[0]) == 2:
            return f"{partes[2]}-{partes[1]}-{partes[0]}"
        return data_str
    except Exception:
        return None


# ── Empresas ──────────────────────────────────────────────

def upsert_empresa(codigo: str, nome: str, cnpj: str = "", telefone: str = ""):
    if not SUPABASE_ATIVO:
        return
    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("empresas"),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "codigo"},
            json={"codigo": codigo, "nome": nome, "cnpj": cnpj, "telefone": telefone},
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro upsert empresa {codigo}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SUPABASE] Erro upsert empresa: {e}")


# ── ASOs enviados ─────────────────────────────────────────

def buscar_chaves_enviadas() -> set:
    """Retorna set de chaves de ASOs com enviado=True no Supabase."""
    if not SUPABASE_ATIVO:
        return set()
    try:
        resp = _requisicao_com_retry(
            requests.get,
            _url("asos_enviados"),
            headers=_headers(),
            params={"enviado": "eq.true", "select": "chave_aso"},
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro ao buscar chaves enviadas: {resp.text[:200]}")
            return set()
        return {row["chave_aso"] for row in resp.json() if row.get("chave_aso")}
    except Exception as e:
        print(f"[SUPABASE] Erro ao buscar chaves enviadas: {e}")
        return set()


def marcar_aso_enviado(
    chave:          str,
    codigo_empresa: str,
    nome_empresa:   str,
    data_envio:     str,
    data_emissao:   str,
    numero_destino: str,
    wamid:          str = "",
):
    """Upsert marcando ASO como enviado=True."""
    if not SUPABASE_ATIVO:
        return
    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("asos_enviados"),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "chave_aso"},
            json={
                "chave_aso":      chave,
                "codigo_empresa": codigo_empresa,
                "nome_empresa":   nome_empresa,
                "data_envio":     _data_para_iso(data_envio),
                "data_emissao":   _data_para_iso(data_emissao),
                "numero_destino": numero_destino,
                "wamid":          wamid,
                "enviado":        True,
                "assinado":       True,
                "status":         "enviado",
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro marcar enviado {chave}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SUPABASE] Erro marcar enviado: {e}")


def salvar_aso_pendente(
    chave:          str,
    codigo_empresa: str,
    nome_empresa:   str,
    data_emissao:   str,
    numero_destino: str = "",
):
    """Registra ASO como pendente (enviado=False) para retry na próxima execução."""
    if not SUPABASE_ATIVO:
        return
    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("asos_enviados"),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "chave_aso"},
            json={
                "chave_aso":      chave,
                "codigo_empresa": codigo_empresa,
                "nome_empresa":   nome_empresa,
                "data_emissao":   _data_para_iso(data_emissao),
                "numero_destino": numero_destino,
                "enviado":        False,
                "assinado":       False,
                "status":         "pendente",
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro salvar pendente {chave}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SUPABASE] Erro salvar pendente: {e}")


def buscar_asos_pendentes() -> list:
    """Retorna ASOs com enviado=False (não enviados) do Supabase."""
    if not SUPABASE_ATIVO:
        return []
    try:
        resp = _requisicao_com_retry(
            requests.get,
            _url("asos_enviados"),
            headers=_headers(),
            params={
                "enviado": "eq.false",
                "select":  "chave_aso,codigo_empresa,nome_empresa,data_emissao,numero_destino,status",
            },
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro buscar pendentes: {resp.text[:200]}")
            return []
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception as e:
        print(f"[SUPABASE] Erro buscar pendentes: {e}")
        return []


# ── Mensagens outbound ────────────────────────────────────

def registrar_mensagem_outbound(
    codigo_empresa: str,
    nome_empresa:   str,
    numero:         str,
    nome_arquivo:   str,
    wamid:          str = "",
):
    if not SUPABASE_ATIVO:
        return
    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("mensagens"),
            headers={**_headers(), "Prefer": "return=minimal"},
            json={
                "codigo_empresa":  codigo_empresa,
                "nome_empresa":    nome_empresa,
                "numero_whatsapp": numero,
                "direcao":         "outbound",
                "tipo":            "document",
                "conteudo":        f"ASO enviado: {nome_arquivo}",
                "nome_arquivo":    nome_arquivo,
                "wamid":           wamid,
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro registrar mensagem outbound: {resp.text[:200]}")
    except Exception as e:
        print(f"[SUPABASE] Erro registrar mensagem outbound: {e}")


# ── Mensagens inbound (chamado pelo n8n via HTTP) ─────────

def registrar_mensagem_inbound(
    numero:         str,
    conteudo:       str,
    wamid:          str = "",
    timestamp:      int = None,
    nome_empresa:   str = "",
    codigo_empresa: str = "",
):
    if not SUPABASE_ATIVO:
        return
    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("mensagens"),
            headers={**_headers(), "Prefer": "return=minimal"},
            json={
                "codigo_empresa":  codigo_empresa,
                "nome_empresa":    nome_empresa,
                "numero_whatsapp": numero,
                "direcao":         "inbound",
                "tipo":            "text",
                "conteudo":        conteudo,
                "wamid":           wamid,
                "timestamp_meta":  timestamp,
            },
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro registrar mensagem inbound: {resp.text[:200]}")
    except Exception as e:
        print(f"[SUPABASE] Erro registrar mensagem inbound: {e}")
