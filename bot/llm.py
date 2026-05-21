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

1. No início de qualquer conversa, use `buscar_empresa_por_telefone` para identificar a empresa
2. Peça o nome do funcionário se não informado
3. Use `buscar_funcionarios` para encontrar o funcionário correto na empresa
4. Se houver múltiplos funcionários, apresente lista numerada para confirmação:

   "Encontrei X funcionários com esse nome em [empresa]. Qual deles?

   1. [Nome completo] — [Cargo]
   2. [Nome completo] — [Cargo]"

5. Após confirmar o funcionário, use `buscar_asos_por_funcionario` com o nome exato
6. Se houver múltiplos ASOs, apresente lista numerada:

   "Encontrei X ASOs para [nome]. Qual você precisa?

   1. [Nome] — [data]
   2. [Nome] — [data]"

7. Após confirmar o ASO, use `baixar_e_enviar_aso` com os dados do candidato confirmado

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
- Ao escalar, SEMPRE envie esta mensagem exata ao cliente:
  "Vou transferir seu atendimento para nossa equipe. Entre em contato com a SafeWork pelo número (43) 9182-1898.""""

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
