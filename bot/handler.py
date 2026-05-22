import os
import re
import json
import time

import openai

from bot import llm, tools, state
from src.meta.whatsapp import enviar_texto_meta

_RE_FUNC_CALL = re.compile(r'<function=(\w+)>([\s\S]*?)</function>', re.DOTALL)
_RE_NUMERO    = re.compile(r'^\s*(\d+)\s*$')

_NUMEROS_TESTE: set[str] = {
    n.strip() for n in os.getenv("BOT_NUMEROS_TESTE", "").split(",") if n.strip()
}


def _interceptar_comando_teste(numero: str, mensagem: str) -> bool:
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


def _enviar_e_registrar(numero: str, texto: str, codigo_empresa: str = ""):
    enviar_texto_meta(numero, texto)
    state.registrar_mensagem_bot(numero, texto, codigo_empresa)


# ── Seleção de funcionário por número ─────────────────────────────────────────

def _processar_selecao_funcionario(numero: str, mensagem: str, estado: dict) -> bool:
    """Se o usuário digitou um número e estamos aguardando escolha de funcionário, processa."""
    m = _RE_NUMERO.match(mensagem.strip())
    if not m:
        return False

    funcionarios = estado.get("candidatos", [])
    idx = int(m.group(1)) - 1
    cod_empresa = estado.get("codigo_empresa", "")

    if idx < 0 or idx >= len(funcionarios):
        _enviar_e_registrar(
            numero,
            f"Por favor, escolha um número entre 1 e {len(funcionarios)}.",
            cod_empresa,
        )
        return True

    func = funcionarios[idx]
    print(f"[BOT] Funcionário selecionado: {func['nome']} (cod={func.get('codigo')})")

    resultado = tools.buscar_asos_por_funcionario(
        numero_whatsapp=numero,
        codigo_empresa=cod_empresa,
        nome_funcionario=func["nome"],
        codigo_funcionario=func.get("codigo", ""),
    )
    candidatos = resultado.get("candidatos", [])

    if not candidatos:
        _enviar_e_registrar(
            numero,
            f"Não encontrei nenhum ASO para {func['nome']} no último ano.",
            cod_empresa,
        )
        state.salvar_estado(numero, fase="livre", codigo_empresa=cod_empresa)
        return True

    if len(candidatos) == 1:
        aso  = candidatos[0]
        data = aso.get("data_emissao") or "data não disponível"
        _enviar_e_registrar(
            numero,
            f"Encontrei 1 ASO para {func['nome']} — {data}. Posso enviar agora?",
            cod_empresa,
        )
        # estado já salvo por buscar_asos_por_funcionario com fase=aguardando_confirmacao
        return True

    linhas = [f"Encontrei {len(candidatos)} ASOs para {func['nome']}. Qual você precisa?\n"]
    for i, aso in enumerate(candidatos, 1):
        data = aso.get("data_emissao") or "data não disponível"
        linhas.append(f"{i}. {func['nome']} — {data}")
    _enviar_e_registrar(numero, "\n".join(linhas), cod_empresa)
    return True


# ── Seleção de ASO por número ──────────────────────────────────────────────────

def _processar_selecao_aso(numero: str, mensagem: str, estado: dict) -> bool:
    """Se o usuário digitou um número e estamos aguardando escolha de ASO, processa."""
    # Aceita número puro ou confirmações quando há 1 candidato
    candidatos = estado.get("candidatos", [])
    cod_empresa = estado.get("codigo_empresa", "")

    m = _RE_NUMERO.match(mensagem.strip())
    if m:
        idx = int(m.group(1)) - 1
    elif len(candidatos) == 1 and re.match(
        r'^(sim|s|ok|pode|isso|esse|envia|manda|quero).*', mensagem.strip(), re.IGNORECASE
    ):
        idx = 0
    else:
        return False

    if idx < 0 or idx >= len(candidatos):
        _enviar_e_registrar(
            numero,
            f"Por favor, escolha um número entre 1 e {len(candidatos)}.",
            cod_empresa,
        )
        return True

    aso = candidatos[idx]
    print(f"[BOT] ASO selecionado: {aso.get('nome_funcionario')} {aso.get('data_emissao')}")

    resultado = tools.baixar_e_enviar_aso(
        numero_whatsapp=numero,
        cd_empresa=aso["cd_empresa"],
        cd_ged=aso["cd_ged"],
        cd_arquivo=aso["cd_arquivo"],
        nome_funcionario=aso.get("nome_funcionario", ""),
        data_emissao=aso.get("data_emissao", ""),
    )

    if not resultado.get("sucesso"):
        _enviar_e_registrar(
            numero,
            f"Não consegui baixar o ASO: {resultado.get('erro', 'erro desconhecido')}. "
            "Entre em contato com a SafeWork pelo número (43) 9182-1898.",
            cod_empresa,
        )

    return True


# ── Helpers do loop LLM ───────────────────────────────────────────────────────

def _normalizar_historico(msgs: list) -> list:
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
            # Salva lista de funcionários no estado para seleção por número
            funcionarios = resultado.get("funcionarios", [])
            if funcionarios:
                estado_atual = state.buscar_estado(numero)
                state.salvar_estado(
                    numero=numero,
                    fase="aguardando_funcionario",
                    codigo_empresa=(estado_atual or {}).get("codigo_empresa") or inputs["codigo_empresa"],
                    candidatos=funcionarios,
                    nome_buscado=inputs["nome_parcial"],
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


# ── Ponto de entrada ──────────────────────────────────────────────────────────

def processar_mensagem(numero: str, mensagem: str, wamid: str = "", timestamp: int = None):
    print(f"[BOT] {numero}: {mensagem[:80]}")

    if _interceptar_comando_teste(numero, mensagem):
        return

    estado = state.buscar_estado(numero)
    fase   = (estado or {}).get("fase", "livre")

    # Seleção por número: funcionário
    if fase == "aguardando_funcionario":
        if _processar_selecao_funcionario(numero, mensagem, estado):
            return

    # Seleção por número (ou confirmação): ASO
    if fase == "aguardando_confirmacao":
        if _processar_selecao_aso(numero, mensagem, estado):
            return

    # Fluxo LLM ----------------------------------------------------------------
    historico = state.buscar_historico(numero, limite=10)

    if (historico
            and historico[-1]["direcao"] == "inbound"
            and historico[-1].get("conteudo") == mensagem):
        historico = historico[:-1]

    contexto: dict = {}
    if estado:
        if estado.get("codigo_empresa"):
            contexto["empresa_codigo"] = estado["codigo_empresa"]
        # Informa o LLM sobre o estado atual para ele gerar a resposta adequada
        if fase == "aguardando_funcionario" and estado.get("candidatos"):
            contexto["lista_funcionarios"] = estado["candidatos"]
            contexto["nome_buscado"]       = estado.get("nome_buscado")
        elif fase == "aguardando_confirmacao" and estado.get("candidatos"):
            contexto["lista_asos"]   = estado["candidatos"]
            contexto["nome_buscado"] = estado.get("nome_buscado")

    raw = []
    for msg in historico:
        role    = "user" if msg["direcao"] == "inbound" else "assistant"
        conteudo = (msg.get("conteudo") or "").strip()
        if conteudo:
            raw.append({"role": role, "content": conteudo})

    messages = _normalizar_historico(raw)
    messages.append({"role": "user", "content": mensagem})

    resposta_final = None
    for _ in range(5):
        try:
            resposta = llm.chamar_llm(messages, contexto)
        except openai.RateLimitError:
            print("[BOT] Rate limit — aguardando 15s e tentando novamente")
            time.sleep(15)
            try:
                resposta = llm.chamar_llm(messages, contexto)
            except Exception as e2:
                print(f"[BOT] Erro após retry: {e2}")
                resposta_final = "Desculpe, ocorreu um erro interno. Entre em contato com a SafeWork pelo número (43) 9182-1898."
                break
        except Exception as e:
            print(f"[BOT] Erro na chamada ao LLM: {e}")
            resposta_final = "Desculpe, ocorreu um erro interno. Entre em contato com a SafeWork pelo número (43) 9182-1898."
            break

        choice = resposta.choices[0]

        if choice.finish_reason == "stop":
            content      = choice.message.content or ""
            inline_tools = _extrair_inline_tools(content)
            if inline_tools:
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
            for tc in msg_assistente.tool_calls:
                print(f"[BOT] Tool: {tc.function.name}")
                inputs    = json.loads(tc.function.arguments)
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
        _enviar_e_registrar(
            numero,
            resposta_final,
            (estado or {}).get("codigo_empresa", ""),
        )
    except Exception as e:
        print(f"[BOT] Erro ao enviar resposta: {e}")
