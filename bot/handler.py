import os
import re
import json

from bot import llm, tools, state
from src.meta.whatsapp import enviar_texto_meta

_RE_FUNC_CALL = re.compile(r'<function=(\w+)>([\s\S]*?)</function>', re.DOTALL)

_NUMEROS_TESTE: set[str] = {
    n.strip() for n in os.getenv("BOT_NUMEROS_TESTE", "").split(",") if n.strip()
}


def _interceptar_comando_teste(numero: str, mensagem: str) -> bool:
    """
    Permite que números de teste definam a empresa manualmente.
    Exemplo: enviar "empresa 1530555" associa aquele código ao número durante os testes.
    Retorna True se a mensagem foi interceptada (não deve ser processada pelo LLM).
    """
    if numero not in _NUMEROS_TESTE:
        return False

    match = re.match(r"^empresa\s+(\d+)\s*$", mensagem.strip(), re.IGNORECASE)
    if not match:
        return False

    codigo = match.group(1)
    state.salvar_estado(numero=numero, fase="livre", codigo_empresa=codigo)
    enviar_texto_meta(numero, f"Modo teste ativo. Empresa definida como {codigo}. Pode perguntar sobre ASOs.")
    print(f"[BOT] Teste: empresa {codigo} definida para {numero}")
    return True


def _normalizar_historico(msgs: list) -> list:
    """Garante alternância user/assistant e que começa com user."""
    normalizado = []
    for msg in msgs:
        if normalizado and normalizado[-1]["role"] == msg["role"]:
            normalizado[-1]["content"] += f"\n{msg['content']}"
        else:
            normalizado.append({"role": msg["role"], "content": msg["content"]})

    while normalizado and normalizado[0]["role"] != "user":
        normalizado.pop(0)

    return normalizado


def _extrair_inline_tools(content: str) -> list[tuple[str, dict]]:
    """Detecta chamadas <function=nome>{...}</function> geradas como texto pelo Llama."""
    resultado = []
    for nome, args_raw in _RE_FUNC_CALL.findall(content):
        try:
            resultado.append((nome, json.loads(args_raw.strip())))
        except json.JSONDecodeError:
            pass
    return resultado


def _executar_tool(nome: str, inputs: dict, numero: str) -> str:
    try:
        if nome == "buscar_funcionarios":
            resultado = tools.buscar_funcionarios(
                codigo_empresa=inputs["codigo_empresa"],
                nome_parcial=inputs["nome_parcial"],
            )
        elif nome == "buscar_empresa_por_telefone":
            resultado = tools.buscar_empresa(inputs["telefone"])
        elif nome == "buscar_asos_por_funcionario":
            resultado = tools.buscar_asos_por_funcionario(
                numero_whatsapp=numero,
                codigo_empresa=inputs["codigo_empresa"],
                nome_funcionario=inputs["nome_funcionario"],
                janela_dias=inputs.get("janela_dias", 365),
                codigo_funcionario=inputs.get("codigo_funcionario", ""),
            )
        elif nome == "baixar_e_enviar_aso":
            resultado = tools.baixar_e_enviar_aso(
                numero_whatsapp=numero,
                cd_empresa=inputs["cd_empresa"],
                cd_ged=inputs["cd_ged"],
                cd_arquivo=inputs["cd_arquivo"],
                nome_funcionario=inputs.get("nome_funcionario", ""),
                data_emissao=inputs.get("data_emissao", ""),
            )
        elif nome == "escalar_para_humano":
            resultado = tools.escalar_para_humano(inputs["numero"], inputs["motivo"])
        else:
            resultado = {"erro": f"Tool desconhecida: {nome}"}
    except Exception as e:
        resultado = {"erro": str(e)}

    return json.dumps(resultado, ensure_ascii=False)


def processar_mensagem(numero: str, mensagem: str, wamid: str = "", timestamp: int = None):
    print(f"[BOT] {numero}: {mensagem[:80]}")

    if _interceptar_comando_teste(numero, mensagem):
        return

    estado    = state.buscar_estado(numero)
    historico = state.buscar_historico(numero, limite=10)

    # Descarta mensagem atual se o n8n já a salvou no histórico antes de chamar o bot
    if (historico
            and historico[-1]["direcao"] == "inbound"
            and historico[-1].get("conteudo") == mensagem):
        historico = historico[:-1]

    # Contexto injetado no system prompt
    contexto = {}
    if estado:
        if estado.get("codigo_empresa"):
            contexto["empresa_codigo"] = estado["codigo_empresa"]
        if estado.get("fase") == "aguardando_confirmacao" and estado.get("candidatos"):
            contexto["aguardando_confirmacao"] = True
            contexto["candidatos_apresentados"] = estado["candidatos"]
            contexto["nome_buscado"] = estado.get("nome_buscado")

    # Monta histórico como lista de mensagens para o LLM
    raw = []
    for msg in historico:
        role    = "user" if msg["direcao"] == "inbound" else "assistant"
        conteudo = (msg.get("conteudo") or "").strip()
        if conteudo:
            raw.append({"role": role, "content": conteudo})

    messages = _normalizar_historico(raw)
    messages.append({"role": "user", "content": mensagem})

    # Loop de tool use (máx 5 iterações)
    resposta_final = None
    for _ in range(5):
        try:
            resposta = llm.chamar_llm(messages, contexto)
        except Exception as e:
            print(f"[BOT] Erro na chamada ao LLM: {e}")
            resposta_final = "Desculpe, ocorreu um erro interno. Entre em contato com a SafeWork pelo número (43) 9182-1898."
            break
        choice   = resposta.choices[0]

        if choice.finish_reason == "stop":
            content = choice.message.content or ""
            inline_tools = _extrair_inline_tools(content)
            if inline_tools:
                # Llama gerou tool calls como texto — executa e injeta resultado
                messages.append({"role": "assistant", "content": content})
                resultados = []
                for nome_tool, inputs_tool in inline_tools:
                    print(f"[BOT] Tool (inline): {nome_tool}")
                    res = _executar_tool(nome_tool, inputs_tool, numero)
                    resultados.append(f"Resultado de {nome_tool}: {res}")
                messages.append({"role": "user", "content": "\n".join(resultados)})
                continue
            resposta_final = content
            break

        if choice.finish_reason == "tool_calls":
            msg_assistente = choice.message

            # Adiciona mensagem do assistente com os tool_calls
            messages.append({
                "role":       "assistant",
                "content":    msg_assistente.content or "",
                "tool_calls": [
                    {
                        "id":       tc.id,
                        "type":     "function",
                        "function": {
                            "name":      tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg_assistente.tool_calls
                ],
            })

            # Executa cada tool e adiciona os resultados
            for tc in msg_assistente.tool_calls:
                print(f"[BOT] Tool: {tc.function.name}")
                inputs   = json.loads(tc.function.arguments)
                resultado = _executar_tool(tc.function.name, inputs, numero)
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      resultado,
                })
        else:
            break

    if not resposta_final:
        resposta_final = "Olá! Recebi sua mensagem. Em que posso ajudar?"

    try:
        enviar_texto_meta(numero, resposta_final)
        state.registrar_mensagem_bot(
            numero=numero,
            conteudo=resposta_final,
            codigo_empresa=(estado or {}).get("codigo_empresa", ""),
        )
    except Exception as e:
        print(f"[BOT] Erro ao enviar resposta: {e}")
