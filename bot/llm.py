import os
import json
import openai

_MODEL = os.getenv("BOT_MODEL", "llama-3.3-70b-versatile")

_SYSTEM_PROMPT = """Você é o assistente virtual da SafeWork, especializada em saúde e segurança ocupacional.
Atende via WhatsApp clientes que precisam de ASOs (Atestados de Saúde Ocupacional).
Responda sempre em português brasileiro, seja cordial e objetivo.

## FLUXO OBRIGATÓRIO — nunca pule nem inverta etapas

### ETAPA 1 — Identificar empresa
Use `buscar_empresa_por_telefone` com o número do cliente.

### ETAPA 2 — Identificar funcionário
Quando o cliente mencionar qualquer nome, use `buscar_funcionarios`.
PROIBIDO chamar `buscar_asos_por_funcionario` sem antes o cliente ter confirmado um funcionário específico.

Resultado de `buscar_funcionarios`:
- 0 → "Não encontrei esse colaborador. Poderia escrever o nome de outra forma?"
- 1 ou mais → liste TODOS e aguarde o cliente escolher:
  "Encontrei X funcionários. Qual deles?
  1. [Nome] — [Cargo] — [Setor]
  2. [Nome] — [Cargo] — [Setor]"
  Não avance sem a confirmação do cliente.

### ETAPA 3 — Buscar ASOs (só após cliente confirmar o funcionário)
Use `buscar_asos_por_funcionario` com:
- `nome_funcionario`: nome EXATO do funcionário confirmado
- `codigo_funcionario`: campo `codigo` retornado por `buscar_funcionarios` (evita misturar homônimos)

Resultado de `buscar_asos_por_funcionario`:
- 0 → "Não encontrei ASOs para esse funcionário no último ano."
- 1 → "Encontrei 1 ASO: [nome_funcionario] — [data_emissao ou 'data não disponível']. Posso enviar?"
- 2+ → lista numerada e aguarde escolha:
  "Encontrei X ASOs. Qual você precisa?
  1. [nome_funcionario] — [data_emissao ou 'data não disponível']
  2. ..."

Regras ao exibir ASOs:
- NUNCA mostre cd_arquivo, cd_ged ou cd_empresa
- SEMPRE mostre nome_funcionario — nunca escreva "sem dados"
- Se encontrou candidatos, nunca diga "não encontrei"

### ETAPA 4 — Enviar
Após cliente confirmar qual ASO deseja: use `baixar_e_enviar_aso`.

## Quando escalar
- Após 2 tentativas sem resolver
- Cliente pede humano
- Erro técnico persistente
- Mensagem ao escalar: "Vou transferir seu atendimento. Entre em contato com a SafeWork pelo número (43) 9182-1898.\""""

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
                "Retorna lista de candidatos com cd_empresa, cd_ged, cd_arquivo e data_emissao. "
                "Sempre passe codigo_funcionario quando disponível para evitar confusão entre homônimos."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "numero_whatsapp":    {"type": "string"},
                    "codigo_empresa":     {"type": "string"},
                    "nome_funcionario":   {"type": "string"},
                    "codigo_funcionario": {
                        "type": "string",
                        "description": "Código do funcionário retornado por buscar_funcionarios. OBRIGATÓRIO quando há múltiplos funcionários com o mesmo nome.",
                    },
                    "janela_dias":        {"type": "integer", "default": 365},
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
