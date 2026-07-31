import os
import requests

from src.utils.helpers import _requisicao_com_retry

SUPABASE_URL         = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SECRET_KEY  = os.getenv("SUPABASE_SECRET_KEY", "").strip()   # anon/publishable — usado no front
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()  # service_role — bypassa RLS
SUPABASE_ATIVO       = bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _headers():
    """Backend confiável: usa service_role (bypassa RLS) para leituras e escritas."""
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
            _url("asos_enviados"),
            headers=_headers(),
            params={"select": "id", "limit": "1"},
            timeout=10,
        )
        if resp.status_code < 300:
            return True, "OK"
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"
    except Exception as e:
        return False, str(e)


# ── ASOs enviados ─────────────────────────────────────────

def buscar_chaves_enviadas(data_inicio: str = None, data_fim: str = None) -> set:
    """Retorna set de chaves de ASOs com enviado=True no Supabase.

    Se data_inicio/data_fim forem informados (DD/MM/YYYY ou YYYY-MM-DD), filtra
    por data_emissao dentro dessa janela — a MESMA janela usada para consultar o
    SOC (JANELA_DIAS). Isso mantém a consulta sempre pequena e evita truncamento
    pelo limite padrão de linhas do Supabase (o total histórico de asos_enviados
    só cresce; sem esse filtro, a lista de "já enviados" pode vir incompleta e
    causar reenvio). Sem datas, mantém o comportamento antigo (sem filtro).
    """
    if not SUPABASE_ATIVO:
        return set()
    try:
        params = [("enviado", "eq.true"), ("select", "chave_aso")]
        if data_inicio:
            params.append(("data_emissao", f"gte.{_data_para_iso(data_inicio)}"))
        if data_fim:
            params.append(("data_emissao", f"lte.{_data_para_iso(data_fim)}"))

        resp = _requisicao_com_retry(
            requests.get,
            _url("asos_enviados"),
            headers=_headers(),
            params=params,
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


def buscar_asos_pendentes(data_inicio: str = None, data_fim: str = None) -> list:
    """Retorna ASOs com enviado=False (não enviados) do Supabase.

    Mesmo filtro de janela de data_inicio/data_fim de buscar_chaves_enviadas(),
    para manter a consulta pequena e evitar truncamento pelo limite do Supabase.
    """
    if not SUPABASE_ATIVO:
        return []
    try:
        params = [
            ("enviado", "eq.false"),
            ("select", "chave_aso,codigo_empresa,nome_empresa,data_emissao,numero_destino,status"),
        ]
        if data_inicio:
            params.append(("data_emissao", f"gte.{_data_para_iso(data_inicio)}"))
        if data_fim:
            params.append(("data_emissao", f"lte.{_data_para_iso(data_fim)}"))

        resp = _requisicao_com_retry(
            requests.get,
            _url("asos_enviados"),
            headers=_headers(),
            params=params,
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
