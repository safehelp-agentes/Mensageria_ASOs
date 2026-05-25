import os
import re

from bot import llm, tools, state
from src.meta.whatsapp import enviar_texto_meta

_NUMEROS_TESTE: set[str] = {
    n.strip() for n in os.getenv("BOT_NUMEROS_TESTE", "").split(",") if n.strip()
}

_MSG_BOAS_VINDAS = (
    "Olá! Sou o robô responsável pela medicina na SafeWork. "
    "Esse é um canal oficial, mas sem atendimento humano. "
    "Caso necessite falar com uma pessoa, entre em contato pelo (43) 9182-1898."
)

_MSG_MENU = (
    "O que você deseja?\n"
    "1. Buscar ASO\n"
    "0. Finalizar atendimento"
)

_RE_NUMERO = re.compile(r'^\s*(\d+)\s*$')


_WA_MAX_CHARS = 4096


def _enviar(numero: str, texto: str, cod_empresa: str = ""):
    """Envia texto ao WhatsApp, dividindo em partes se ultrapassar 4096 chars."""
    if len(texto) <= _WA_MAX_CHARS:
        enviar_texto_meta(numero, texto)
        state.registrar_mensagem_bot(numero, texto, cod_empresa)
        return

    # Divide por linhas sem cortar no meio de uma linha
    linhas = texto.splitlines(keepends=True)
    parte  = ""
    for linha in linhas:
        if len(parte) + len(linha) > _WA_MAX_CHARS:
            if parte:
                enviar_texto_meta(numero, parte.rstrip())
                state.registrar_mensagem_bot(numero, parte.rstrip(), cod_empresa)
                parte = ""
        parte += linha
    if parte.strip():
        enviar_texto_meta(numero, parte.rstrip())
        state.registrar_mensagem_bot(numero, parte.rstrip(), cod_empresa)


def _extrair_numero(mensagem: str) -> int | None:
    m = _RE_NUMERO.match(mensagem.strip())
    return int(m.group(1)) if m else None


def _interceptar_comando_teste(numero: str, mensagem: str) -> bool:
    if numero not in _NUMEROS_TESTE:
        return False
    match = re.match(r"^empresa\s+(\d+)\s*$", mensagem.strip(), re.IGNORECASE)
    if not match:
        return False
    codigo = match.group(1)
    state.salvar_estado(numero=numero, fase="livre", codigo_empresa=codigo)
    _enviar(numero, f"Modo teste ativo. Empresa definida como {codigo}.")
    print(f"[BOT] Teste: empresa {codigo} definida para {numero}")
    return True


# ── Fases ─────────────────────────────────────────────────────────────────────

def _fase_nova_conversa(numero: str, mensagem: str, cod_empresa: str):
    _enviar(numero, _MSG_BOAS_VINDAS, cod_empresa)

    intencao = llm.interpretar_mensagem_inicial(mensagem)
    print(f"[BOT] Intenção inicial: {intencao}")

    if intencao["quer_aso"] and intencao["nome"]:
        # Fast path: já sabe que quer ASO e qual funcionário — pula menu e "qual nome?"
        nome         = intencao["nome"]
        resultado    = tools.buscar_funcionarios(codigo_empresa=cod_empresa, nome_parcial=nome)
        funcionarios = resultado.get("funcionarios", [])

        if funcionarios:
            state.salvar_estado(
                numero=numero,
                fase="aguardando_funcionario",
                codigo_empresa=cod_empresa,
                candidatos=funcionarios,
                nome_buscado=nome,
            )
            linhas = [f"Encontrei {len(funcionarios)} funcionário(s) com esse nome:\n"]
            for i, f in enumerate(funcionarios, 1):
                linhas.append(f"{i}. {f['nome']} — {f['cargo']} — {f['setor']}")
            linhas.append("\n0. Voltar")
            _enviar(numero, "\n".join(linhas), cod_empresa)
            return

        # Nome não encontrado — cai no fluxo normal abaixo

    elif intencao["quer_aso"]:
        # Sabe que quer ASO mas não mencionou nome — pula menu
        _enviar(numero, "Qual o nome do funcionário que você deseja buscar?", cod_empresa)
        state.salvar_estado(numero, fase="aguardando_nome_funcionario", codigo_empresa=cod_empresa)
        return

    # Sem intenção clara — mostra menu
    _enviar(numero, _MSG_MENU, cod_empresa)
    state.salvar_estado(numero, fase="menu_principal", codigo_empresa=cod_empresa)


def _fase_menu_principal(numero: str, mensagem: str, estado: dict):
    cod = estado.get("codigo_empresa", "")

    n = _extrair_numero(mensagem)
    if n is None:
        n = llm.interpretar_opcao_menu(mensagem)

    if n == 1:
        _enviar(numero, "Qual o nome do funcionário que você deseja buscar?", cod)
        state.salvar_estado(numero, fase="aguardando_nome_funcionario", codigo_empresa=cod)
        return

    if n == 0:
        _enviar(numero, "Atendimento finalizado. Até logo!", cod)
        state.resetar_estado(numero)
        return

    _enviar(numero, f"Não entendi. Por favor, responda com:\n{_MSG_MENU}", cod)


def _fase_aguardando_nome(numero: str, mensagem: str, estado: dict):
    cod = estado.get("codigo_empresa", "")

    nome = llm.interpretar_nome_funcionario(mensagem)
    if not nome:
        _enviar(numero, "Não identifiquei um nome. Por favor, informe o nome do funcionário:", cod)
        return

    print(f"[BOT] Nome extraído: {nome}")
    resultado    = tools.buscar_funcionarios(codigo_empresa=cod, nome_parcial=nome)
    funcionarios = resultado.get("funcionarios", [])

    if not funcionarios:
        _enviar(
            numero,
            f"Não encontrei nenhum funcionário com o nome \"{nome}\". Poderia verificar o nome?\n"
            "0. Voltar ao menu",
            cod,
        )
        return

    state.salvar_estado(
        numero=numero,
        fase="aguardando_funcionario",
        codigo_empresa=cod,
        candidatos=funcionarios,
        nome_buscado=nome,
    )

    linhas = [f"Encontrei {len(funcionarios)} funcionário(s) com esse nome:\n"]
    for i, f in enumerate(funcionarios, 1):
        linhas.append(f"{i}. {f['nome']} — {f['cargo']} — {f['setor']}")
    linhas.append("\n0. Voltar")
    _enviar(numero, "\n".join(linhas), cod)


def _fase_aguardando_funcionario(numero: str, mensagem: str, estado: dict):
    cod          = estado.get("codigo_empresa", "")
    funcionarios = estado.get("candidatos", [])

    n = _extrair_numero(mensagem)
    if n is None:
        nomes = [f"{f['nome']} — {f['cargo']}" for f in funcionarios]
        idx   = llm.interpretar_selecao_lista(mensagem, len(funcionarios), nomes)
        if idx == -1:
            n = 0
        elif idx is not None:
            n = idx + 1  # volta para 1-based

    if n == 0:
        _enviar(numero, _MSG_MENU, cod)
        state.salvar_estado(numero, fase="menu_principal", codigo_empresa=cod)
        return

    if n is None or n < 1 or n > len(funcionarios):
        _enviar(numero, f"Por favor, escolha um número de 1 a {len(funcionarios)} ou 0 para voltar.", cod)
        return

    func = funcionarios[n - 1]
    print(f"[BOT] Funcionário selecionado: {func['nome']} (cod={func.get('codigo')})")

    resultado  = tools.buscar_asos_por_funcionario(
        numero_whatsapp=numero,
        codigo_empresa=cod,
        nome_funcionario=func["nome"],
        codigo_funcionario=func.get("codigo", ""),
    )
    candidatos = resultado.get("candidatos", [])

    if not candidatos:
        _enviar(numero, f"Não encontrei nenhum ASO para {func['nome']} no último ano.", cod)
        _enviar(numero, _MSG_MENU, cod)
        state.salvar_estado(numero, fase="menu_principal", codigo_empresa=cod)
        return

    linhas = [f"Qual ASO você deseja?\n"]
    for i, aso in enumerate(candidatos, 1):
        data = aso.get("data_emissao") or "data não disponível"
        linhas.append(f"{i}. {aso['nome_funcionario']} — {data}")
    linhas.append("\n0. Voltar")
    _enviar(numero, "\n".join(linhas), cod)
    # Estado com ASOs já salvo por buscar_asos_por_funcionario (fase=aguardando_confirmacao)


def _fase_aguardando_aso(numero: str, mensagem: str, estado: dict):
    cod        = estado.get("codigo_empresa", "")
    candidatos = estado.get("candidatos", [])

    n = _extrair_numero(mensagem)

    if n is None:
        # Aceita confirmações verbais quando há 1 candidato
        if len(candidatos) == 1 and re.match(
            r'^(sim|s|ok|pode|isso|esse|envia|manda|quero).*',
            mensagem.strip(), re.IGNORECASE
        ):
            n = 1
        else:
            nomes = [
                f"{a['nome_funcionario']} — {a.get('data_emissao') or 'data não disponível'}"
                for a in candidatos
            ]
            idx = llm.interpretar_selecao_lista(mensagem, len(candidatos), nomes)
            if idx == -1:
                n = 0
            elif idx is not None:
                n = idx + 1

    if n == 0:
        _enviar(numero, _MSG_MENU, cod)
        state.salvar_estado(numero, fase="menu_principal", codigo_empresa=cod)
        return

    if n is None or n < 1 or n > len(candidatos):
        _enviar(numero, f"Por favor, escolha um número de 1 a {len(candidatos)} ou 0 para voltar.", cod)
        return

    aso = candidatos[n - 1]
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
        _enviar(
            numero,
            f"Não consegui enviar o ASO: {resultado.get('erro', 'erro desconhecido')}. "
            "Entre em contato com a SafeWork pelo número (43) 9182-1898.",
            cod,
        )

    _enviar(numero, _MSG_MENU, cod)
    state.salvar_estado(numero, fase="menu_principal", codigo_empresa=cod)


# ── Ponto de entrada ──────────────────────────────────────────────────────────

def processar_mensagem(numero: str, mensagem: str, wamid: str = "", timestamp: int = None):
    print(f"[BOT] {numero}: {mensagem[:80]}")

    if _interceptar_comando_teste(numero, mensagem):
        return

    estado = state.buscar_estado(numero)
    fase   = (estado or {}).get("fase", "livre")
    cod    = (estado or {}).get("codigo_empresa", "")

    # Se ainda não há empresa associada ao número, valida no SOC
    if not cod:
        contato = tools.buscar_contato_soc_por_numero(numero)
        if not contato:
            _enviar(
                numero,
                "Não foi encontrado registro no SOC sobre o número utilizado. "
                "Entre em contato com o atendimento humano pelo (43) 9182-1898.",
            )
            return
        cod = str(contato.get("CODIGOEMPRESA", "")).strip()
        print(f"[BOT] Contato SOC: {contato.get('NOMECONTATO')} — empresa {cod}")

    if not estado or fase == "livre":
        _fase_nova_conversa(numero, mensagem, cod)
        return

    if fase == "menu_principal":
        _fase_menu_principal(numero, mensagem, estado)
        return

    if fase == "aguardando_nome_funcionario":
        _fase_aguardando_nome(numero, mensagem, estado)
        return

    if fase == "aguardando_funcionario":
        _fase_aguardando_funcionario(numero, mensagem, estado)
        return

    if fase == "aguardando_confirmacao":
        _fase_aguardando_aso(numero, mensagem, estado)
        return

    if fase == "escalado":
        _enviar(
            numero,
            "Seu atendimento já foi transferido para nossa equipe. "
            "Entre em contato pelo (43) 9182-1898.",
            cod,
        )
        return

    # Fallback
    _fase_nova_conversa(numero, mensagem, cod)
