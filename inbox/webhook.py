"""Parsing do payload do webhook da Meta Cloud API (WhatsApp).

Converte o corpo bruto do POST da Meta numa lista de mensagens normalizadas,
prontas para gravar na tabela `mensagens` (direcao='inbound').

Não faz I/O — só transforma o dict recebido. Ver inbox/repo.py para a gravação.
"""
from src.utils.helpers import normalizar_numero_whatsapp


# Tipos de mensagem que carregam mídia (o conteúdo textual vem na legenda/caption).
_TIPOS_MIDIA = ("image", "video", "audio", "document", "sticker")


def _extrair_conteudo(m: dict) -> tuple[str | None, str | None, str | None]:
    """Retorna (conteudo, nome_arquivo, media_id) conforme o tipo da mensagem."""
    tipo = m.get("type", "unknown")

    if tipo == "text":
        return (m.get("text") or {}).get("body"), None, None

    if tipo in _TIPOS_MIDIA:
        obj = m.get(tipo) or {}
        return obj.get("caption"), obj.get("filename"), obj.get("id")

    if tipo == "button":
        return (m.get("button") or {}).get("text"), None, None

    if tipo == "interactive":
        inter = m.get("interactive") or {}
        resp  = inter.get("button_reply") or inter.get("list_reply") or {}
        return resp.get("title"), None, None

    if tipo == "location":
        loc = m.get("location") or {}
        return f"{loc.get('latitude')}, {loc.get('longitude')}", None, None

    if tipo == "reaction":
        return (m.get("reaction") or {}).get("emoji"), None, None

    return None, None, None


def _extrair_mensagem(m: dict, perfis: dict) -> dict:
    frm                       = m.get("from", "")
    tipo                      = m.get("type", "unknown")
    conteudo, nome_arq, media = _extrair_conteudo(m)

    ts_raw = m.get("timestamp")
    ts_ms  = int(ts_raw) * 1000 if ts_raw and str(ts_raw).isdigit() else None

    return {
        "numero":       normalizar_numero_whatsapp(frm),
        "nome_perfil":  perfis.get(frm),
        "tipo":         tipo,
        "conteudo":     conteudo,
        "nome_arquivo": nome_arq,
        "media_id":     media,          # a tabela `mensagens` não tem coluna p/ isso — descartado na gravação
        "wamid":        m.get("id"),
        "timestamp_ms": ts_ms,
    }


def parse_webhook(body: dict) -> list[dict]:
    """Extrai todas as mensagens de clientes do payload da Meta.

    Ignora blocos de `statuses` (recibos de entrega/leitura dos nossos envios).
    """
    mensagens = []
    for entry in (body.get("entry") or []):
        for change in (entry.get("changes") or []):
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            if not value.get("messages"):
                continue

            perfis = {
                c.get("wa_id"): (c.get("profile") or {}).get("name")
                for c in (value.get("contacts") or [])
            }
            for m in value["messages"]:
                mensagens.append(_extrair_mensagem(m, perfis))

    return mensagens
