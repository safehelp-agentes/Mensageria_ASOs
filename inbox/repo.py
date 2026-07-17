"""Acesso ao Supabase para o Inbox (visualizador read-only).

- Gravação: apenas inbound na tabela `mensagens` (idempotente por wamid, no código).
- Leitura:  junta envios (`asos_enviados`) e recebidas (`mensagens`) por NÚMERO
            de telefone (estilo WhatsApp). Empresa é só um rótulo resolvido aqui.

Usa a service_role (bypassa RLS) — este módulo roda SEMPRE no backend, nunca no browser.
"""
import os
import requests
from datetime import datetime, timezone

from src.utils.helpers import _requisicao_com_retry, normalizar_numero_whatsapp

SUPABASE_URL         = os.getenv("SUPABASE_URL", "").strip()
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY", "").strip()


def _headers() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
    }


def _url(tabela: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{tabela}"


def _get_all(tabela: str, params: dict, tamanho_pagina: int = 1000) -> list:
    """Pagina via Range até puxar todas as linhas (PostgREST limita a 1000/req)."""
    resultados = []
    offset = 0
    while True:
        headers = {**_headers(), "Range-Unit": "items",
                   "Range": f"{offset}-{offset + tamanho_pagina - 1}"}
        resp = _requisicao_com_retry(requests.get, _url(tabela),
                                     headers=headers, params=params, timeout=30)
        if resp.status_code >= 300:
            raise RuntimeError(f"Supabase {tabela}: HTTP {resp.status_code} — {resp.text[:200]}")
        lote = resp.json()
        resultados.extend(lote)
        if len(lote) < tamanho_pagina:
            return resultados
        offset += tamanho_pagina


# ── Número de telefone: variantes e chave de conversa ────────────────────────
# A Meta às vezes entrega o número sem o 9º dígito; o cadastro (SOC) costuma ter
# com o 9. Geramos as duas formas para casar envios e recebidas na mesma conversa.

def variantes_numero(numero: str) -> list:
    n = normalizar_numero_whatsapp(numero)
    if not n:
        return []
    variantes = {n}
    if n.startswith("55") and len(n) >= 12:
        ddd, local = n[2:4], n[4:]
        if len(local) == 9 and local.startswith("9"):
            variantes.add(f"55{ddd}{local[1:]}")   # tira o 9
        elif len(local) == 8:
            variantes.add(f"55{ddd}9{local}")       # coloca o 9
    return sorted(variantes)


def chave_conversa(numero: str) -> str:
    """Chave estável de agrupamento — prefere a forma com 9 (mais longa)."""
    variantes = variantes_numero(numero)
    return max(variantes, key=len) if variantes else ""


def _filtro_in(numero: str) -> str:
    variantes = variantes_numero(numero)
    return "in.(" + ",".join(variantes) + ")"


# ── Tempo ────────────────────────────────────────────────────────────────────
_BRT = timezone.utc  # exibição faz o ajuste; aqui trabalhamos em epoch ms UTC

def _iso_para_ms(iso: str) -> int | None:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    except Exception:
        return None


# ── Gravação (webhook) ───────────────────────────────────────────────────────

def wamid_existe(wamid: str) -> bool:
    if not wamid:
        return False
    resp = _requisicao_com_retry(
        requests.get, _url("mensagens"), headers=_headers(),
        params={"wamid": f"eq.{wamid}", "select": "id", "limit": "1"}, timeout=10,
    )
    return resp.status_code < 300 and len(resp.json()) > 0


def inserir_inbound(msg: dict) -> str:
    """Grava uma mensagem recebida. Idempotente: pula se o wamid já existe.
    Retorna 'inserido' | 'duplicado'."""
    if msg.get("wamid") and wamid_existe(msg["wamid"]):
        return "duplicado"

    resp = _requisicao_com_retry(
        requests.post, _url("mensagens"),
        headers={**_headers(), "Prefer": "return=minimal"},
        json={
            "direcao":         "inbound",
            "numero_whatsapp": msg["numero"],
            "nome_empresa":    msg.get("nome_perfil"),   # push name (mesma convenção do legado)
            "codigo_empresa":  None,
            "tipo":            msg.get("tipo", "unknown"),
            "conteudo":        msg.get("conteudo"),
            "nome_arquivo":    msg.get("nome_arquivo"),
            "wamid":           msg.get("wamid"),
            "timestamp_meta":  msg.get("timestamp_ms"),
        },
        timeout=10,
    )
    if resp.status_code >= 300:
        raise RuntimeError(f"insert mensagens: HTTP {resp.status_code} — {resp.text[:200]}")
    return "inserido"


# ── Leitura (dashboard) ──────────────────────────────────────────────────────

def listar_conversas() -> list:
    """Uma entrada por número, juntando envios e recebidas, ordenada por atividade recente."""
    enviados  = _get_all("asos_enviados", {
        "select": "numero_destino,nome_empresa,codigo_empresa,created_at,data_envio"})
    recebidos = _get_all("mensagens", {
        "direcao": "eq.inbound",
        "select":  "numero_whatsapp,nome_empresa,conteudo,tipo,timestamp_meta,created_at"})
    empresas  = _get_all("empresas", {"select": "codigo,nome,telefone"})

    # índice telefone (chave de conversa) -> nomes de empresa
    idx_emp = {}
    for e in empresas:
        tel = e.get("telefone")
        if tel:
            nome = (e.get("nome") or e.get("codigo") or "").strip()
            if nome:
                idx_emp.setdefault(chave_conversa(tel), set()).add(nome)

    conversas = {}

    def _slot(numero):
        chave = chave_conversa(numero)
        if not chave:
            return None
        return conversas.setdefault(chave, {
            "numero": chave, "codigo": "", "empresas": set(), "perfil": None,
            "enviadas": 0, "recebidas": 0,
            "ultimo_ts": None, "ultimo_preview": "", "ultimo_direcao": None,
        })

    def _marcar(slot, ts, preview, direcao):
        if ts is not None and (slot["ultimo_ts"] is None or ts > slot["ultimo_ts"]):
            slot["ultimo_ts"]      = ts
            slot["ultimo_preview"] = preview or ""
            slot["ultimo_direcao"] = direcao

    for r in enviados:
        slot = _slot(r.get("numero_destino", ""))
        if slot is None:
            continue
        slot["enviadas"] += 1
        nome = (r.get("nome_empresa") or "").strip()
        if nome:
            slot["empresas"].add(nome)
        cod = (r.get("codigo_empresa") or "").strip()
        if cod and "_" not in cod and not slot["codigo"]:
            slot["codigo"] = cod
        _marcar(slot, _iso_para_ms(r.get("created_at")), "ASO enviado", "outbound")

    for r in recebidos:
        slot = _slot(r.get("numero_whatsapp", ""))
        if slot is None:
            continue
        slot["recebidas"] += 1
        slot["perfil"] = slot["perfil"] or (r.get("nome_empresa") or "").strip() or None
        ts = r.get("timestamp_meta") or _iso_para_ms(r.get("created_at"))
        preview = (r.get("conteudo") or f"[{r.get('tipo', 'mensagem')}]")[:80]
        _marcar(slot, ts, preview, "inbound")

    saida = []
    for chave, slot in conversas.items():
        slot["empresas"] |= idx_emp.get(chave, set())
        empresas_lst = sorted(slot["empresas"])
        saida.append({
            "numero":      slot["numero"],
            "codigo":      slot["codigo"],
            "nome":        slot["perfil"] or (empresas_lst[0] if empresas_lst else slot["numero"]),
            "empresas":    empresas_lst,
            "tem_empresa": bool(empresas_lst),
            "enviadas":    slot["enviadas"],
            "recebidas":   slot["recebidas"],
            "ts":          slot["ultimo_ts"],
            "preview":     slot["ultimo_preview"],
            "preview_dir": slot["ultimo_direcao"],
        })

    return sorted(saida, key=lambda c: c["ts"] or 0, reverse=True)


def obter_conversa(numero: str) -> dict:
    """Retorna {meta, eventos} de uma conversa (um número), em ordem cronológica."""
    filtro = _filtro_in(numero)

    enviados  = _get_all("asos_enviados", {
        "numero_destino": filtro,
        "select": "nome_empresa,codigo_empresa,created_at,data_envio,status,nome_arquivo"})
    recebidos = _get_all("mensagens", {
        "direcao": "eq.inbound", "numero_whatsapp": filtro,
        "select": "nome_empresa,conteudo,tipo,timestamp_meta,created_at,nome_arquivo"})
    emp_rows  = _get_all("empresas", {"telefone": filtro, "select": "codigo,nome,cnpj"})

    eventos, empresas, perfil, codigo, cnpj = [], set(), None, "", ""

    for r in enviados:
        nome = (r.get("nome_empresa") or "").strip()
        if nome:
            empresas.add(nome)
        cod = (r.get("codigo_empresa") or "").strip()
        if cod and "_" not in cod and not codigo:
            codigo = cod
        eventos.append({
            "dir":     "out",
            "ts":      _iso_para_ms(r.get("created_at")),
            "tipo":    "document",                       # envio = template com PDF
            "texto":   None,
            "arquivo": r.get("nome_arquivo") or "ASO.pdf",
            "status":  r.get("status"),
        })

    for r in recebidos:
        perfil  = perfil or (r.get("nome_empresa") or "").strip() or None
        ts      = r.get("timestamp_meta") or _iso_para_ms(r.get("created_at"))
        eventos.append({
            "dir":     "in",
            "ts":      ts,
            "tipo":    r.get("tipo") or "text",
            "texto":   r.get("conteudo"),
            "arquivo": r.get("nome_arquivo"),
            "status":  None,
        })

    for e in emp_rows:
        nome = (e.get("nome") or "").strip()
        if nome:
            empresas.add(nome)
        if not codigo and (e.get("codigo") or "").strip():
            codigo = e["codigo"].strip()
        if not cnpj and (e.get("cnpj") or "").strip():
            cnpj = e["cnpj"].strip()

    eventos.sort(key=lambda ev: ev["ts"] or 0)
    empresas_lst = sorted(empresas)

    return {
        "meta": {
            "numero":    chave_conversa(numero),
            "nome":      perfil or (empresas_lst[0] if empresas_lst else chave_conversa(numero)),
            "empresas":  empresas_lst,
            "codigo":    codigo,
            "cnpj":      cnpj,
            "perfil":    perfil,
            "enviadas":  len(enviados),
            "recebidas": len(recebidos),
        },
        "eventos": eventos,
    }
