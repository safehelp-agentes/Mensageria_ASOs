import os
import requests

from src.utils.helpers import _requisicao_com_retry

SUPABASE_URL         = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY  = os.getenv("SUPABASE_SECRET_KEY", "").strip()   # anon/publishable — usado no front
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()  # service_role — bypassa RLS
SUPABASE_ATIVO       = bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _headers_read():
    """Leituras: usa a chave anon (respeita RLS de SELECT)."""
    return {
        "apikey":        SUPABASE_SECRET_KEY,
        "Authorization": f"Bearer {SUPABASE_SECRET_KEY}",
        "Content-Type":  "application/json",
    }


def _headers_write():
    """Escritas: usa service_role para bypassar RLS de INSERT/UPDATE."""
    key = SUPABASE_SERVICE_KEY or SUPABASE_SECRET_KEY
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
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


# ── Health check ─────────────────────────────────────────

def verificar_conectividade() -> tuple[bool, str]:
    """
    Testa se o Supabase está acessível e a chave de serviço é válida.
    Retorna (ok: bool, mensagem: str).
    """
    if not SUPABASE_ATIVO:
        return False, "SUPABASE_URL ou SUPABASE_SECRET_KEY não configurados no .env"
    if not SUPABASE_SERVICE_KEY:
        return False, "SUPABASE_SERVICE_KEY não configurada no .env"
    try:
        resp = _requisicao_com_retry(
            requests.get,
            _url("empresas"),
            headers=_headers_write(),
            params={"select": "codigo", "limit": "1"},
            timeout=10,
        )
        if resp.status_code < 300:
            return True, "OK"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


# ── Empresas ──────────────────────────────────────────────

def upsert_empresa(codigo: str, nome: str, cnpj: str = "", telefone: str = ""):
    if not SUPABASE_ATIVO:
        return
    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("empresas"),
            headers={**_headers_write(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "codigo"},
            json={"codigo": codigo, "nome": nome, "cnpj": cnpj, "telefone": telefone},
            timeout=10,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro upsert empresa {codigo}: {resp.text[:200]}")
    except Exception as e:
        print(f"[SUPABASE] Erro upsert empresa: {e}")


def sincronizar_empresas_soc(empresas: list) -> None:
    """Insere no Supabase as empresas do SOC que ainda não existem. Nunca altera registros existentes."""
    if not SUPABASE_ATIVO or not empresas:
        return

    registros = []
    for emp in empresas:
        codigo = str(emp.get("CODIGO", "")).strip()
        if not codigo:
            continue
        registros.append({
            "codigo": codigo,
            "nome":   (emp.get("RAZAOSOCIAL") or emp.get("NOMEABREVIADO") or "").strip(),
            "cnpj":   str(emp.get("CNPJ", "")).strip(),
        })

    if not registros:
        return

    try:
        resp = _requisicao_com_retry(
            requests.post,
            _url("empresas"),
            headers={**_headers_write(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "codigo"},
            json=registros,
            timeout=30,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro ao sincronizar empresas: {resp.text[:200]}")
        else:
            print(f"[SUPABASE] Sincronização de empresas concluída ({len(registros)} verificadas)")
    except Exception as e:
        print(f"[SUPABASE] Erro ao sincronizar empresas: {e}")


# ── ASOs enviados ─────────────────────────────────────────

def buscar_chaves_enviadas() -> set:
    """Retorna set de chaves de ASOs com enviado=True no Supabase."""
    if not SUPABASE_ATIVO:
        return set()
    try:
        resp = _requisicao_com_retry(
            requests.get,
            _url("asos_enviados"),
            headers=_headers_write(),
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
            headers={**_headers_write(), "Prefer": "resolution=merge-duplicates,return=minimal"},
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
            headers={**_headers_write(), "Prefer": "resolution=merge-duplicates,return=minimal"},
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
            headers=_headers_write(),
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


def buscar_config_empresas() -> dict:
    """Retorna {codigo: {bloqueada, telefone_escolhido}} para cada empresa cadastrada."""
    if not SUPABASE_ATIVO:
        return {}
    try:
        resp = _requisicao_com_retry(
            requests.get,
            _url("empresas"),
            headers=_headers_write(),
            params={"select": "codigo,bloqueada,telefone_escolhido"},
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro buscar config empresas: {resp.text[:200]}")
            return {}
        return {
            row["codigo"]: {
                "bloqueada":          bool(row.get("bloqueada", False)),
                "telefone_escolhido": (row.get("telefone_escolhido") or "").strip(),
            }
            for row in resp.json()
            if row.get("codigo")
        }
    except Exception as e:
        print(f"[SUPABASE] Erro buscar config empresas: {e}")
        return {}


def buscar_dados_empresas() -> dict:
    """Retorna {codigo: {nome, cnpj, telefone, bloqueada, telefone_escolhido}} para todas as empresas."""
    if not SUPABASE_ATIVO:
        return {}
    try:
        resp = _requisicao_com_retry(
            requests.get,
            _url("empresas"),
            headers=_headers_write(),
            params={"select": "codigo,nome,cnpj,telefone,bloqueada,telefone_escolhido"},
            timeout=15,
        )
        if resp.status_code >= 300:
            print(f"[SUPABASE] Erro buscar dados empresas: {resp.text[:200]}")
            return {}
        return {
            row["codigo"]: {
                "nome":               (row.get("nome") or "").strip(),
                "cnpj":               (row.get("cnpj") or "").strip(),
                "telefone":           (row.get("telefone") or "").strip(),
                "bloqueada":          bool(row.get("bloqueada", False)),
                "telefone_escolhido": (row.get("telefone_escolhido") or "").strip(),
            }
            for row in resp.json()
            if row.get("codigo")
        }
    except Exception as e:
        print(f"[SUPABASE] Erro buscar dados empresas: {e}")
        return {}


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
            headers={**_headers_write(), "Prefer": "return=minimal"},
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
            headers={**_headers_write(), "Prefer": "return=minimal"},
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
