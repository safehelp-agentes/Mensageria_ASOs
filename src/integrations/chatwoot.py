import os
import requests

_BASE_URL    = os.getenv("CHATWOOT_BASE_URL", "").strip().rstrip("/")
_API_TOKEN   = os.getenv("CHATWOOT_API_TOKEN", "").strip()
_ACCOUNT_ID  = os.getenv("CHATWOOT_ACCOUNT_ID", "1").strip()
_INBOX_ID    = int(os.getenv("CHATWOOT_INBOX_ID", "0") or 0)
_ATIVO       = os.getenv("CHATWOOT_ATIVO", "false").strip().lower() == "true"
_TIMEOUT     = 10

# IDs de mensagens criadas pelo bot — evita reencaminhar ao WhatsApp mensagens
# que o bot já enviou (elas são espelhadas no Chatwoot mas NÃO devem ser reenviadas).
_bot_message_ids: set[int] = set()


def _ok() -> bool:
    return _ATIVO and bool(_BASE_URL) and bool(_API_TOKEN) and _INBOX_ID > 0


def _headers() -> dict:
    return {"api_access_token": _API_TOKEN, "Content-Type": "application/json"}


def _api(path: str) -> str:
    return f"{_BASE_URL}/api/v1/accounts/{_ACCOUNT_ID}{path}"


# ── Contatos e conversas ───────────────────────────────────────────────────────

def _get_or_create_contact(phone: str, name: str = "") -> int | None:
    phone_fmt = phone if phone.startswith("+") else f"+{phone}"

    try:
        resp = requests.get(
            _api("/contacts/search"),
            headers=_headers(),
            params={"q": phone_fmt, "include_contacts": True},
            timeout=_TIMEOUT,
        )
        if resp.ok:
            results = resp.json().get("payload", {}).get("contacts", [])
            if results:
                return results[0]["id"]
    except Exception as e:
        print(f"[CHATWOOT] Erro ao buscar contato: {e}")

    try:
        resp = requests.post(
            _api("/contacts"),
            headers=_headers(),
            json={"phone_number": phone_fmt, "name": name or phone_fmt},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception as e:
        print(f"[CHATWOOT] Erro ao criar contato: {e}")

    return None


def _get_or_create_conversation(contact_id: int) -> int | None:
    try:
        resp = requests.get(
            _api(f"/contacts/{contact_id}/conversations"),
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        if resp.ok:
            for conv in resp.json().get("payload", []):
                if conv.get("inbox_id") == _INBOX_ID and conv.get("status") == "open":
                    return conv["id"]
    except Exception as e:
        print(f"[CHATWOOT] Erro ao buscar conversas: {e}")

    try:
        resp = requests.post(
            _api("/conversations"),
            headers=_headers(),
            json={"inbox_id": _INBOX_ID, "contact_id": contact_id},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception as e:
        print(f"[CHATWOOT] Erro ao criar conversa: {e}")

    return None


def _conversation_for(phone: str) -> int | None:
    contact_id = _get_or_create_contact(phone)
    if not contact_id:
        return None
    return _get_or_create_conversation(contact_id)


# ── Mensagens ──────────────────────────────────────────────────────────────────

def _post_message(conv_id: int, content: str, message_type: str) -> int | None:
    """Cria mensagem no Chatwoot e retorna o ID gerado."""
    try:
        resp = requests.post(
            _api(f"/conversations/{conv_id}/messages"),
            headers=_headers(),
            json={"content": content, "message_type": message_type, "private": False},
            timeout=_TIMEOUT,
        )
        if resp.status_code in (200, 201):
            return resp.json().get("id")
    except Exception as e:
        print(f"[CHATWOOT] Erro ao criar mensagem: {e}")
    return None


def espelhar_inbound(phone: str, content: str) -> None:
    """Espelha mensagem recebida do WhatsApp para o Chatwoot (direcao: inbound)."""
    if not _ok():
        return
    try:
        conv_id = _conversation_for(phone)
        if conv_id:
            _post_message(conv_id, content, "incoming")
    except Exception as e:
        print(f"[CHATWOOT] Erro ao espelhar inbound: {e}")


def espelhar_outbound(phone: str, content: str) -> None:
    """
    Espelha mensagem enviada pelo bot para o Chatwoot (direcao: outbound).
    O ID gerado é rastreado para evitar loop quando o webhook Chatwoot disparar.
    """
    if not _ok():
        return
    try:
        conv_id = _conversation_for(phone)
        if not conv_id:
            return
        msg_id = _post_message(conv_id, content, "outgoing")
        if msg_id:
            _bot_message_ids.add(msg_id)
            if len(_bot_message_ids) > 500:
                _bot_message_ids.clear()
    except Exception as e:
        print(f"[CHATWOOT] Erro ao espelhar outbound: {e}")


# ── Prevenção de loop ──────────────────────────────────────────────────────────

def is_bot_message(msg_id: int) -> bool:
    """True se esta mensagem foi criada pelo bot (não deve ser reenviada ao WhatsApp)."""
    return msg_id in _bot_message_ids


def consume_bot_message(msg_id: int) -> None:
    """Remove o ID do rastreio após identificar o echo."""
    _bot_message_ids.discard(msg_id)
