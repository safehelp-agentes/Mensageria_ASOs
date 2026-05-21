"""
Gerenciamento de estado das conversas do bot no Supabase.

Execute este SQL no Supabase SQL Editor para criar a tabela:

    create table conversas_bot (
        numero_whatsapp text primary key,
        fase            text not null default 'livre',
        codigo_empresa  text,
        candidatos      jsonb default '[]',
        nome_buscado    text,
        updated_at      timestamptz default now()
    );
"""
import os
import re
import requests
from datetime import datetime, timezone


def _apenas_digitos(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def _headers():
    key = os.getenv("SUPABASE_SERVICE_KEY", "").strip()
    return {
        "apikey":        key,
        "Authorization": f"Bearer {key}",
        "Content-Type":  "application/json",
    }


def _url(tabela: str) -> str:
    return f"{os.getenv('SUPABASE_URL', '').strip()}/rest/v1/{tabela}"


# ── Estado da conversa ─────────────────────────────────────────────────────────

def buscar_estado(numero: str) -> dict | None:
    try:
        resp = requests.get(
            _url("conversas_bot"),
            headers=_headers(),
            params={"numero_whatsapp": f"eq.{numero}", "limit": "1"},
            timeout=10,
        )
        data = resp.json()
        if isinstance(data, list) and data:
            return data[0]
        return None
    except Exception:
        return None


def salvar_estado(
    numero:         str,
    fase:           str,
    codigo_empresa: str  = None,
    candidatos:     list = None,
    nome_buscado:   str  = None,
):
    payload: dict = {
        "numero_whatsapp": numero,
        "fase":            fase,
        "updated_at":      datetime.now(timezone.utc).isoformat(),
    }
    if codigo_empresa is not None:
        payload["codigo_empresa"] = codigo_empresa
    if candidatos is not None:
        payload["candidatos"] = candidatos
    if nome_buscado is not None:
        payload["nome_buscado"] = nome_buscado

    try:
        requests.post(
            _url("conversas_bot"),
            headers={**_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
            params={"on_conflict": "numero_whatsapp"},
            json=payload,
            timeout=10,
        )
    except Exception:
        pass


def resetar_estado(numero: str):
    salvar_estado(numero, fase="livre", candidatos=[], nome_buscado="")


# ── Empresas ───────────────────────────────────────────────────────────────────

def buscar_empresa_por_telefone(telefone: str) -> dict | None:
    """Busca empresa pelo telefone comparando os últimos 11 dígitos."""
    sufixo = _apenas_digitos(telefone)[-11:]
    if not sufixo:
        return None

    try:
        resp = requests.get(
            _url("empresas"),
            headers=_headers(),
            params={"select": "codigo,nome,telefone,telefone_escolhido,bloqueada"},
            timeout=10,
        )
        empresas = resp.json()
        if not isinstance(empresas, list):
            return None

        for emp in empresas:
            tel1 = _apenas_digitos(emp.get("telefone") or "")
            tel2 = _apenas_digitos(emp.get("telefone_escolhido") or "")
            if sufixo in tel1 or sufixo in tel2:
                return emp
        return None
    except Exception:
        return None


# ── Mensagens ──────────────────────────────────────────────────────────────────

def buscar_historico(numero: str, limite: int = 10) -> list:
    """Retorna as últimas mensagens em ordem cronológica."""
    try:
        resp = requests.get(
            _url("mensagens"),
            headers=_headers(),
            params={
                "numero_whatsapp": f"eq.{numero}",
                "select":          "direcao,tipo,conteudo",
                "order":           "id.desc",
                "limit":           str(limite),
            },
            timeout=10,
        )
        data = resp.json()
        if isinstance(data, list):
            return list(reversed(data))
        return []
    except Exception:
        return []


def registrar_mensagem_bot(
    numero:         str,
    conteudo:       str,
    codigo_empresa: str = "",
    nome_empresa:   str = "",
):
    try:
        requests.post(
            _url("mensagens"),
            headers={**_headers(), "Prefer": "return=minimal"},
            json={
                "codigo_empresa":  codigo_empresa,
                "nome_empresa":    nome_empresa,
                "numero_whatsapp": numero,
                "direcao":         "outbound",
                "tipo":            "bot",
                "conteudo":        conteudo,
            },
            timeout=10,
        )
    except Exception:
        pass
