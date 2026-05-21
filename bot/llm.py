import os
import json
import openai

_MODEL = os.getenv("BOT_MODEL", "llama-3.3-70b-versatile")

_SYSTEM_PROMPT = """Você é o assistente virtual da SafeWork, especializada em saúde e segurança ocupacional.

Seu papel é atender via WhatsApp clientes que precisam de ASOs (Atestados de Saúde Ocupacional).

## Capacidades
- Buscar e enviar ASOs específicos de funcionários
- Responder dúvidas sobre documentos enviados
- Escalar para atendente humano quando necessário

## Fluxo para busca de ASO

IMPORTANTE: Siga esta ordem sempre, sem pular etapas.

1. Identifique a empresa: use `buscar_empresa_por_telefone` com o número do cliente
2. Peça o nome do funcionário se não informado
3. SEMPRE use `buscar_funcionarios` com o nome informado — mesmo que seja só o primeiro nome
4. Com o resultado de `buscar_funcionarios`:
   - Se retornar 1 funcionário: confirme com o cliente antes de buscar o ASO
   - Se retornar 2 ou mais: apresente TODOS em lista numerada para o cliente escolher:

     "Encontrei X funcionários com esse nome. Qual deles?

     1. [Nome completo] — [Cargo]
     2. [Nome completo] — [Cargo]"

   - Se retornar 0: responda exatamente: "Não encontrei esse colaborador registrado na empresa. Poderia tentar escrever o nome de outra forma ou confirmar se esse funcionário está realmente cadastrado?"
5. Após o cliente confirmar o funcionário, use `buscar_asos_por_funcionario` com o nome EXATO retornado pela busca
6. Se houver múltiplos ASOs, apresente lista numerada usando SEMPRE o campo data_emissao:

   "Encontrei X ASOs para [nome]. Qual você precisa?

   1. [Nome completo] — [data_emissao]
   2. [Nome completo] — [data_emissao]"

   NUNCA mostre cd_arquivo, cd_ged ou cd_empresa ao cliente. Se data_emissao estiver vazio, mostre "data não disponível".

7. Após o cliente confirmar o ASO, use `baixar_e_enviar_aso`

## Regras
- Seja cordial e objetivo
- Interprete confirmações informais corretamente ("o primeiro", "esse aí", "o da construtora")
- Se não encontrar o funcionário, informe e ofereça buscar com nome diferente
- Em caso de erro técnico, informe e escale para humano
- Nunca invente informações sobre ASOs
- Responda sempre em português brasileiro

## Quando escalar
- Situação não resolvida após 2 tentativas
- Cliente pede atendimento humano
- Erro técnico persistente
- Ao escalar, SEMPRE envie esta mensagem ao cliente:
  Vou transferir seu atendimento para nossa equipe. Entre em contato com a SafeWork pelo número (43) 9182-1898."""

_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "buscar_funcionarios",
            "description": (
                "Busca funcionários de uma empresa pelo nome parcial. "
                "Use antes de buscar ASOs para confirmar qual funcionário o cliente quer. "
                "Retorna lista com nome, cargo, setor e situação."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "codigo_empresa": {"type": "string", "description": "Código da empresa no SOC"},
                    "nome_parcial":   {"type": "string", "description": "Nome ou parte do nome do funcionário"},
                },
                "required": ["codigo_empresa", "nome_parcial"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_empresa_por_telefone",
            "description": "Identifica a empresa do cliente pelo número de telefone. Use sempre no início da conversa.",
            "parameters": {
                "type": "object",
                "properties": {
                    "telefone": {"type": "string", "description": "Número de telefone do cliente"},
                },
                "required": ["telefone"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "buscar_asos_por_funcionario",
            "description": (
                "Busca ASOs de um funcionário no SOC pelo nome exato. "
                "Use após confirmar qual funcionário o cliente quer. "
                "Retorna lista de candidatos com cd_empresa, cd_ged, cd_arquivo e data_emissao."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "numero_whatsapp":  {"type": "string"},
                    "codigo_empresa":   {"type": "string"},
                    "nome_funcionario": {"type": "string"},
                    "janela_dias":      {"type": "integer", "default": 365},
                },
                "required": ["numero_whatsapp", "codigo_empresa", "nome_funcionario"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "baixar_e_enviar_aso",
            "description": (
                "Baixa o ASO do SOC e envia ao cliente via WhatsApp. "
                "Use após o cliente confirmar qual ASO deseja."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "numero_whatsapp":  {"type": "string"},
                    "cd_empresa":       {"type": "string"},
                    "cd_ged":           {"type": "string"},
                    "cd_arquivo":       {"type": "string"},
                    "nome_funcionario": {"type": "string"},
                    "data_emissao":     {"type": "string"},
                },
                "required": ["numero_whatsapp", "cd_empresa", "cd_ged", "cd_arquivo"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "escalar_para_humano",
            "description": "Sinaliza que esta conversa precisa de atenção de um atendente humano.",
            "parameters": {
                "type": "object",
                "properties": {
                    "numero": {"type": "string"},
                    "motivo": {"type": "string"},
                },
                "required": ["numero", "motivo"],
            },
        },
    },
]


def chamar_llm(messages: list, contexto: dict = None):
    client = openai.OpenAI(
        api_key=os.getenv("GROQ_API_KEY", ""),
        base_url="https://api.groq.com/openai/v1",
    )

    system = _SYSTEM_PROMPT
    if contexto:
        system += f"\n\n## Contexto da conversa\n{json.dumps(contexto, ensure_ascii=False, indent=2)}"

    full_messages = [{"role": "system", "content": system}] + messages

    return client.chat.completions.create(
        model=_MODEL,
        max_tokens=1024,
        tools=_TOOLS,
        messages=full_messages,
    )
