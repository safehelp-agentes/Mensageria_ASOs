import os
import re
import json
import openai

_MODEL = os.getenv("BOT_MODEL", "llama-3.3-70b-versatile")


def _client():
    return openai.OpenAI(
        api_key=os.getenv("GROQ_API_KEY", ""),
        base_url="https://api.groq.com/openai/v1",
    )


def _chamar(system: str, user: str, max_tokens: int = 150) -> str:
    resp = _client().chat.completions.create(
        model=_MODEL,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user",   "content": user},
        ],
    )
    return (resp.choices[0].message.content or "").strip()


def _parse_json(texto: str) -> dict:
    """Extrai JSON mesmo que venha dentro de bloco markdown ou com texto ao redor."""
    texto = re.sub(r"```(?:json)?", "", texto).strip().strip("`").strip()
    # Tenta direto primeiro
    try:
        return json.loads(texto)
    except json.JSONDecodeError:
        pass
    # Fallback: extrai o primeiro objeto JSON encontrado no texto
    match = re.search(r'\{[^{}]*\}', texto, re.DOTALL)
    if match:
        return json.loads(match.group())
    raise ValueError(f"Nenhum JSON encontrado em: {texto[:200]}")


# ── Funções de interpretação ───────────────────────────────────────────────────

def interpretar_mensagem_inicial(mensagem: str) -> dict:
    """
    Analisa a primeira mensagem do usuário para detectar intenção e nome.
    Retorna {"quer_aso": bool, "nome": str | None}
    """
    system = (
        "Analise a mensagem de um cliente que acessa um sistema de ASOs (Atestados de Saúde Ocupacional).\n"
        "Identifique:\n"
        "1. Se o cliente quer buscar um ASO (quer_aso: true ou false)\n"
        "2. Se ele mencionou o nome de um funcionário (nome: \"nome\" ou null)\n"
        "Responda APENAS JSON. Exemplos:\n"
        "  'bom dia, gostaria de ver asos do adilson' → {\"quer_aso\": true, \"nome\": \"adilson\"}\n"
        "  'quero buscar um aso' → {\"quer_aso\": true, \"nome\": null}\n"
        "  'oi tudo bem' → {\"quer_aso\": false, \"nome\": null}"
    )
    try:
        data = _parse_json(_chamar(system, mensagem))
        return {
            "quer_aso": bool(data.get("quer_aso")),
            "nome":     data.get("nome") or None,
        }
    except Exception:
        return {"quer_aso": False, "nome": None}


def interpretar_opcao_menu(mensagem: str) -> int | None:
    """Retorna 1 (buscar ASO), 0 (finalizar) ou None se não entendeu."""
    system = (
        "Identifique qual opção de menu o usuário escolheu.\n"
        "Menu: 1=Buscar ASO, 0=Finalizar atendimento.\n"
        "Responda APENAS JSON: {\"opcao\": 1} ou {\"opcao\": 0} ou {\"opcao\": null}."
    )
    try:
        data  = _parse_json(_chamar(system, mensagem))
        opcao = data.get("opcao")
        return opcao if opcao in (0, 1) else None
    except Exception:
        return None


def interpretar_nome_funcionario(mensagem: str) -> str | None:
    """Extrai o nome do funcionário mencionado. Retorna None se não encontrar."""
    system = (
        "Extraia o nome de funcionário mencionado na mensagem.\n"
        "Responda APENAS JSON: {\"nome\": \"João Silva\"} ou {\"nome\": null}.\n"
        "Exemplos:\n"
        "  'quero o aso do adilson' → {\"nome\": \"adilson\"}\n"
        "  'pode ser o de João Silva' → {\"nome\": \"João Silva\"}\n"
        "  'quero buscar' → {\"nome\": null}"
    )
    try:
        data = _parse_json(_chamar(system, mensagem))
        nome = data.get("nome")
        return nome.strip() if isinstance(nome, str) and nome.strip() else None
    except Exception:
        return None


def interpretar_selecao_lista(mensagem: str, total: int, itens: list[str]) -> int | None:
    """
    Identifica qual item da lista o usuário escolheu.
    Retorna índice 0-based, -1 para voltar, ou None se não entendeu.
    """
    lista = "\n".join(f"{i+1}. {item}" for i, item in enumerate(itens))
    system = (
        f"Identifique qual item o usuário quer escolher desta lista:\n{lista}\n"
        f"Responda APENAS JSON: {{\"selecao\": N}} onde N é 1 a {total}, "
        f"0 para voltar/cancelar, ou null se não souber."
    )
    try:
        data = _parse_json(_chamar(system, mensagem))
        sel  = data.get("selecao")
        if sel is None:
            return None
        sel = int(sel)
        if sel == 0:
            return -1
        if 1 <= sel <= total:
            return sel - 1  # converte para 0-based
        return None
    except Exception:
        return None
