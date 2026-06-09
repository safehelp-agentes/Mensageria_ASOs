import json
import time
import requests

from config import (
    SOC_URL, SOC_EMPRESA_PRINCIPAL, SOC_CHAVE_EMPRESAS, SOC_CHAVE_GED,
    CODIGO_EXPORTA_EMPRESAS, CODIGO_EXPORTA_GED, CODIGO_EXPORTA_CONTATOS,
    CODIGO_TIPO_GED_ASO, SOC_CHAVE_CONTATOS,
    SOC_EXPORTA_CONTATOS_USUARIO, SOC_EXPORTA_CONTATOS_IDENTIFICACAO,
    SOC_EXPORTA_CONTATOS_CODIGO_PERFIL,
    IGNORAR_EMPRESA_PRINCIPAL, EMPRESAS_PERMITIDAS, LIMITE_EMPRESAS,
)
from src.utils.helpers import (
    _requisicao_com_retry, registrar_erro,
    normalizar_numero_whatsapp, numero_parece_valido,
)


def esta_assinado_digitalmente(reg: dict) -> bool:
    valor = str(reg.get("ASSINADO_DIGITALMENTE", "")).strip().lower()
    return valor in {"1", "true", "s", "sim"}


def chamar_exporta_dados(parametro: dict, timeout: int = 60):
    response = _requisicao_com_retry(
        requests.get,
        SOC_URL,
        params={"parametro": json.dumps(parametro, separators=(",", ":"))},
        timeout=timeout,
    )
    response.raise_for_status()

    texto = (response.text or "").strip()
    if texto.lower() in {"sem resultado.", "sem resultado"}:
        return []

    try:
        data = response.json()
    except Exception as e:
        raise RuntimeError(f"Resposta não é JSON válido: {e}\nTexto: {response.text[:500]}")

    if isinstance(data, dict) and data.get("erro") is True:
        raise RuntimeError(f"Erro lógico do SOC: {data.get('mensagem Erro')}")

    return data


def buscar_empresas() -> list:
    if not SOC_EMPRESA_PRINCIPAL:
        raise RuntimeError("SOC_EMPRESA não definido no .env")
    if not SOC_CHAVE_EMPRESAS:
        raise RuntimeError("SOC_CHAVE_EMPRESAS não definido no .env")

    parametro = {
        "empresa":   SOC_EMPRESA_PRINCIPAL,
        "codigo":    CODIGO_EXPORTA_EMPRESAS,
        "chave":     SOC_CHAVE_EMPRESAS,
        "tipoSaida": "json",
    }

    data = chamar_exporta_dados(parametro, timeout=30)
    if not isinstance(data, list):
        return []

    empresas = []
    for emp in data:
        codigo = str(emp.get("CODIGO", "")).strip()
        ativo  = str(emp.get("ATIVO", "")).strip()

        if not codigo:
            continue
        if ativo != "1":
            continue
        if IGNORAR_EMPRESA_PRINCIPAL and codigo == SOC_EMPRESA_PRINCIPAL:
            continue
        if EMPRESAS_PERMITIDAS and codigo not in EMPRESAS_PERMITIDAS:
            continue

        empresas.append(emp)

    if LIMITE_EMPRESAS is not None:
        empresas = empresas[:LIMITE_EMPRESAS]

    return empresas


def buscar_todos_asos_empresa(codigo_empresa_cliente: str, data_inicio: str, data_fim: str) -> list:
    if not SOC_CHAVE_GED:
        raise RuntimeError("SOC_CHAVE_GED não definido no .env")

    parametro = {
        "empresa":             str(codigo_empresa_cliente),
        "codigo":              CODIGO_EXPORTA_GED,
        "chave":               SOC_CHAVE_GED,
        "tipoSaida":           "json",
        "tipoBusca":           "0",
        "filtraPorTipoSocged": True,
        "codigoTipoSocged":    CODIGO_TIPO_GED_ASO,
        "dataEmissaoInicio":   data_inicio,
        "dataEmissaoFim":      data_fim,
    }

    data = chamar_exporta_dados(parametro, timeout=60)
    return data if isinstance(data, list) else []


def buscar_asos_empresa(codigo_empresa_cliente: str, data_inicio: str, data_fim: str) -> list:
    try:
        data = buscar_todos_asos_empresa(codigo_empresa_cliente, data_inicio, data_fim)

        assinados     = [r for r in data if esta_assinado_digitalmente(r)]
        nao_assinados = [r for r in data if not esta_assinado_digitalmente(r)]

        print(
            f"    -> total: {len(data)} | "
            f"assinados: {len(assinados)} | "
            f"sem assinatura: {len(nao_assinados)}"
        )

        return data

    except Exception as e:
        msg = f"Erro ao buscar ASOs da empresa {codigo_empresa_cliente}: {e}"
        print(f"    -> {msg}")
        registrar_erro(msg)
        return []


def buscar_contatos_empresa(codigo_empresa_cliente: str) -> list:
    if not SOC_CHAVE_CONTATOS:
        raise RuntimeError("SOC_CHAVE_CONTATOS não definido no .env")

    parametro = {
        "empresa":         SOC_EMPRESA_PRINCIPAL,
        "codigo":          CODIGO_EXPORTA_CONTATOS,
        "chave":           SOC_CHAVE_CONTATOS,
        "tipoSaida":       "json",
        "empresaTrabalho": str(codigo_empresa_cliente),
        "codigoPerfil":    SOC_EXPORTA_CONTATOS_CODIGO_PERFIL,
        "usuario":         SOC_EXPORTA_CONTATOS_USUARIO,
        "identificacao":   SOC_EXPORTA_CONTATOS_IDENTIFICACAO,
    }

    try:
        data = chamar_exporta_dados(parametro, timeout=30)
        return data if isinstance(data, list) else []
    except Exception as e:
        msg = f"Erro ao buscar contatos da empresa {codigo_empresa_cliente}: {e}"
        print(f"    -> {msg}")
        registrar_erro(msg)
        return []


def extrair_primeiro_numero_contato(contatos: list) -> dict:
    """
    Retorna o telefone do primeiro contato da empresa que tenha número de WhatsApp válido.
    """
    for contato in contatos:
        segundo  = contato.get("segundoTelefone", "")
        primeiro = contato.get("primeiroTelefone", "")

        if numero_parece_valido(segundo):
            return {"numero": normalizar_numero_whatsapp(segundo), "origem": "segundoTelefone", "contato": contato}
        if numero_parece_valido(primeiro):
            return {"numero": normalizar_numero_whatsapp(primeiro), "origem": "primeiroTelefone", "contato": contato}

    return {"numero": "", "origem": "", "contato": None, "sem_contato_aso": True}
