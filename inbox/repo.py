"""Acesso ao Supabase para o Inbox (visualizador read-only).

- Gravação: apenas inbound na tabela `mensagens` (idempotente por wamid, no código).
- Leitura:  junta envios (`asos_enviados`) e recebidas (`mensagens`) por NÚMERO
            de telefone (estilo WhatsApp). Empresa é só um rótulo resolvido aqui.

Usa a service_role (bypassa RLS) — este módulo roda SEMPRE no backend, nunca no browser.
"""
import os
import requests
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from src.utils.helpers import _requisicao_com_retry, normalizar_numero_whatsapp, sanitizar_nome

BRT = timezone(timedelta(hours=-3))

# Corpo do template aprovado na Meta (o que o cliente efetivamente recebe).
# {{1}} = nome da empresa, {{2}} = data de emissão.
CORPO_TEMPLATE = (
    "Prezado(a), segue em anexo o(s) ASO(s) (Atestado de Saúde Ocupacional) "
    "referente(s) ao(s) exame(s) realizado(s).\n\n"
    "Empresa: {empresa}\n"
    "Data de emissão: {data}\n\n"
    "Este documento é de caráter oficial. Em caso de dúvidas, entre em contato "
    "com o setor de saúde ocupacional responsável."
)

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


def _get_all(tabela: str, params: dict, tamanho_pagina: int = 50000) -> list:
    """Pagina via Range até puxar todas as linhas. Este projeto Supabase não
    limita a 1000/req, então uma página grande resolve em 1 requisição; se um dia
    limitar, o laço continua paginando normalmente."""
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


def _iso_para_br(iso: str) -> str:
    """YYYY-MM-DD (ou DD/MM/YYYY) -> DD/MM/YYYY."""
    if not iso:
        return ""
    p = str(iso).strip().replace("/", "-").split("-")
    if len(p) == 3 and len(p[0]) == 4:
        return f"{p[2]}/{p[1]}/{p[0]}"
    return str(iso).strip()


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


def _evento_lote_envio(linhas: list) -> dict:
    """Um lote de ASOs enviados juntos (1 PDF combinado) → 1 evento de timeline."""
    nome = ""
    for x in linhas:
        nome = (x.get("nome_empresa") or "").strip() or nome
    datas = sorted({_iso_para_br(x.get("data_emissao")) for x in linhas if x.get("data_emissao")})
    data_lbl = datas[0] if len(datas) == 1 else "múltiplas datas"
    arquivo = linhas[0].get("nome_arquivo") or (
        f"ASOs_{sanitizar_nome(nome) or 'empresa'}"
        + (f"_{datas[0].replace('/', '-')}" if len(datas) == 1 else "") + ".pdf")
    return {
        "dir":     "out",
        "ts":      _iso_para_ms(linhas[0].get("created_at")),
        "tipo":    "document",
        "texto":   CORPO_TEMPLATE.format(empresa=nome or "—", data=data_lbl or "—"),
        "arquivo": arquivo,
        "count":   len(linhas),
        "status":  linhas[0].get("status"),
    }


def obter_conversa(numero: str) -> dict:
    """Retorna {meta, eventos} de uma conversa (um número), em ordem cronológica."""
    filtro = _filtro_in(numero)

    enviados  = _get_all("asos_enviados", {
        "numero_destino": filtro,
        "select": "nome_empresa,codigo_empresa,created_at,data_envio,data_emissao,status,nome_arquivo"})
    recebidos = _get_all("mensagens", {
        "direcao": "eq.inbound", "numero_whatsapp": filtro,
        "select": "nome_empresa,conteudo,tipo,timestamp_meta,created_at,nome_arquivo"})

    eventos, empresas, perfil, codigo, cnpj = [], set(), None, "", ""

    # Agrupa os envios por LOTE: o pipeline une todos os ASOs de uma empresa num
    # PDF único e manda 1 mensagem — mas isso vira N linhas em asos_enviados (1 por
    # ASO). Aqui juntamos as linhas do mesmo envio (mesma empresa + created_at
    # próximos) numa única bolha, com a contagem de ASOs.
    por_empresa = defaultdict(list)
    for r in enviados:
        nome = (r.get("nome_empresa") or "").strip()
        if nome:
            empresas.add(nome)
        cod = (r.get("codigo_empresa") or "").strip()
        if cod and "_" not in cod and not codigo:
            codigo = cod
        por_empresa[r.get("codigo_empresa") or nome].append(r)

    LOTE_MS = 300_000   # 5 min: separa runs distintos; o mesmo envio fica em segundos
    for linhas in por_empresa.values():
        linhas.sort(key=lambda x: _iso_para_ms(x.get("created_at")) or 0)
        atual = []
        for r in linhas:
            ts = _iso_para_ms(r.get("created_at")) or 0
            if atual and ts - (_iso_para_ms(atual[-1].get("created_at")) or 0) > LOTE_MS:
                eventos.append(_evento_lote_envio(atual))
                atual = []
            atual.append(r)
        if atual:
            eventos.append(_evento_lote_envio(atual))

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


# ── Dashboard e lista de ASOs (abas do CRM) ──────────────────────────────────

def _hoje_br() -> str:
    return datetime.now(BRT).strftime("%Y-%m-%d")


def dashboard_dados(inicio: str = "", fim: str = "", empresa: str = "") -> dict:
    """KPIs, séries e recentes calculados de asos_enviados. Filtros opcionais
    por período (sobre data_envio) e por empresa (nome ou código)."""
    rows = _get_all("asos_enviados", {
        "select": "codigo_empresa,nome_empresa,data_emissao,data_envio,enviado,numero_destino,created_at"})
    total_registros = len(rows)

    empresa = (empresa or "").lower().strip()
    if empresa:
        rows = [a for a in rows
                if empresa in (a.get("nome_empresa") or "").lower()
                or empresa in (a.get("codigo_empresa") or "")]

    def _no_periodo(d):
        if inicio and (not d or d < inicio):
            return False
        if fim and (not d or d > fim):
            return False
        return True

    enviados = [a for a in rows if a.get("enviado") and _no_periodo(a.get("data_envio"))]

    hoje          = _hoje_br()
    enviados_hoje = sum(1 for a in enviados if a.get("data_envio") == hoje)
    empresas      = {a.get("codigo_empresa") for a in enviados if a.get("codigo_empresa")}
    dias          = {a.get("data_envio") for a in enviados if a.get("data_envio")}
    tem_filtro    = bool(inicio or fim or empresa)

    # série dos últimos 30 dias (por data_envio)
    contagem = {}
    for a in enviados:
        d = a.get("data_envio")
        if d:
            contagem[d] = contagem.get(d, 0) + 1
    hoje_dt = datetime.now(BRT).date()
    serie = []
    for i in range(29, -1, -1):
        d = (hoje_dt - timedelta(days=i)).strftime("%Y-%m-%d")
        serie.append({"dia": f"{d[8:10]}/{d[5:7]}", "valor": contagem.get(d, 0)})

    # top empresas por volume
    por_emp = {}
    for a in enviados:
        nome = a.get("nome_empresa") or a.get("codigo_empresa") or "—"
        por_emp[nome] = por_emp.get(nome, 0) + 1
    top_empresas = [{"empresa": k, "valor": v}
                    for k, v in sorted(por_emp.items(), key=lambda x: x[1], reverse=True)[:10]]

    recentes_src = sorted(enviados, key=lambda a: a.get("created_at") or "", reverse=True)[:12]
    recentes = [{
        "empresa":      a.get("nome_empresa") or a.get("codigo_empresa") or "—",
        "data_emissao": _iso_para_br(a.get("data_emissao")),
        "data_envio":   _iso_para_br(a.get("data_envio")),
        "numero":       a.get("numero_destino") or "—",
    } for a in recentes_src]

    return {
        "kpis": {
            "total":         len(enviados),
            "total_sub":     (f"filtrado de {total_registros} registros" if tem_filtro
                              else f"{total_registros} registros no total"),
            "enviados_hoje": enviados_hoje,
            "media_empresa": round(len(enviados) / len(empresas), 1) if empresas else "—",
            "media_diaria":  round(len(enviados) / len(dias), 1) if dias else "—",
            "dias_envio":    len(dias),
            "empresas":      len(empresas),
        },
        "serie":        serie,
        "top_empresas": top_empresas,
        "recentes":     recentes,
    }


def listar_asos(status: str = "enviados", q: str = "", limite: int = 1000) -> dict:
    """Lista ASOs de asos_enviados. status: enviados | pendentes | todos."""
    params = {
        "select": "nome_empresa,codigo_empresa,data_emissao,data_envio,enviado,status,numero_destino,created_at",
        "order":  "created_at.desc",
    }
    if status == "enviados":
        params["enviado"] = "eq.true"
    elif status == "pendentes":
        params["enviado"] = "eq.false"

    rows = _get_all("asos_enviados", params)
    total = len(rows)

    q = (q or "").lower().strip()
    if q:
        rows = [a for a in rows
                if q in (a.get("nome_empresa") or "").lower()
                or q in (a.get("codigo_empresa") or "")
                or q in (a.get("numero_destino") or "")]

    itens = [{
        "empresa":      a.get("nome_empresa") or a.get("codigo_empresa") or "—",
        "codigo":       a.get("codigo_empresa") or "",
        "data_emissao": _iso_para_br(a.get("data_emissao")),
        "data_envio":   _iso_para_br(a.get("data_envio")),
        "numero":       a.get("numero_destino") or "—",
        "enviado":      bool(a.get("enviado")),
        "status":       a.get("status") or ("enviado" if a.get("enviado") else "pendente"),
    } for a in rows[:limite]]

    return {"itens": itens, "total": total, "exibidos": len(itens), "filtrados": len(rows)}
