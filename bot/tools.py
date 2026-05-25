import os
import io
import re
import sys
import zipfile
import tempfile
from datetime import datetime, timedelta
from unicodedata import normalize as _unorm

from src.soc.api import buscar_todos_asos_empresa, chamar_exporta_dados
from src.soc.downloader import baixar_documento
from src.meta.whatsapp import _fazer_upload_pdf, _enviar_documento_simples
from bot.state import buscar_estado, salvar_estado, resetar_estado

_SOC_EMPRESA_PRINCIPAL       = os.getenv("SOC_EMPRESA", "").strip()
_SOC_CHAVE_FUNCIONARIOS      = os.getenv("SOC_CHAVE_FUNCIONARIOS", "").strip()
_CODIGO_EXPORTA_FUNCIONARIOS = "192399"

# Exporta Dados 215872 — Contatos das Empresas (validação de acesso via WhatsApp)
_CODIGO_EXPORTA_CONTATOS_WA = "215872"
_SOC_CHAVE_CONTATOS_WA      = os.getenv("SOC_CHAVE_CONTATOS_WA", "cf3265cee0cb1dfeca54").strip()

# Exporta Dados 193037 — ASOs do Funcionário (dados clínicos detalhados)
_CODIGO_EXPORTA_ASO_FUNCIONARIO = "193037"
_SOC_CHAVE_ASO_FUNCIONARIO      = os.getenv("SOC_CHAVE_ASO_FUNCIONARIO", "").strip()

_TIPO_ASO = {
    "0": "Admissional",
    "1": "Periódico",
    "2": "Retorno ao Trabalho",
    "3": "Mudança de Função",
    "4": "Monitoração Pontual",
    "8": "Demissional",
}


def _so_digitos(s: str) -> str:
    return re.sub(r"\D", "", str(s or ""))


def buscar_contato_soc_por_numero(telefone: str) -> dict | None:
    """
    Consulta o SOC (exportadados 215872) e retorna o contato cujo TEL1 ou TEL2
    bate com o número de WhatsApp recebido. Retorna None se não encontrar.
    O campo CODIGOEMPRESA do retorno é usado como restrição nas buscas.
    """
    sufixo = _so_digitos(telefone)[-11:]
    if not sufixo or not _SOC_EMPRESA_PRINCIPAL:
        return None

    parametro = {
        "empresa":   _SOC_EMPRESA_PRINCIPAL,
        "codigo":    _CODIGO_EXPORTA_CONTATOS_WA,
        "chave":     _SOC_CHAVE_CONTATOS_WA,
        "tipoSaida": "json",
    }

    try:
        dados = chamar_exporta_dados(parametro, timeout=30)
    except Exception as e:
        print(f"[BOT] Erro ao validar número no SOC: {e}")
        return None

    if not isinstance(dados, list):
        return None

    for contato in dados:
        tel1 = _so_digitos(contato.get("TEL1") or "")
        tel2 = _so_digitos(contato.get("TEL2") or "")
        if (tel1 and sufixo in tel1) or (tel2 and sufixo in tel2):
            return contato

    return None


def _normalizar_nome(nome: str) -> str:
    sem_acento = _unorm("NFD", nome).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip()


def _nomes_batem(busca: str, funcionario: str) -> bool:
    b = _normalizar_nome(busca)
    f = _normalizar_nome(funcionario)
    palavras = [p for p in b.split() if len(p) > 2]
    if not palavras:
        return False
    # Basta qualquer palavra da busca aparecer no nome do funcionário
    return any(p in f for p in palavras)


def _campo(aso: dict, *chaves) -> str:
    for chave in chaves:
        val = str(aso.get(chave) or "").strip()
        if val:
            return val
    return ""


def _extrair_pdf_de_zip(zip_bytes: bytes) -> tuple[bytes, str]:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        pdfs = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
        if not pdfs:
            raise RuntimeError("ZIP não contém arquivos PDF")
        nome = pdfs[0]
        return zf.read(nome), os.path.basename(nome)


# ── Tools chamadas pelo Claude ─────────────────────────────────────────────────

def buscar_funcionarios(codigo_empresa: str, nome_parcial: str) -> dict:
    """Lista funcionários de uma empresa filtrando por nome parcial."""
    if not _SOC_CHAVE_FUNCIONARIOS:
        return {"erro": "SOC_CHAVE_FUNCIONARIOS não configurado", "funcionarios": []}

    try:
        parametro = {
            "empresa":        _SOC_EMPRESA_PRINCIPAL,
            "codigo":         _CODIGO_EXPORTA_FUNCIONARIOS,
            "chave":          _SOC_CHAVE_FUNCIONARIOS,
            "tipoSaida":      "json",
            "empresaTrabalho": str(codigo_empresa),
            "cpf":            "",
            "parametroData":  "0",
            "dataInicio":     "",
            "dataFim":        "",
        }
        data = chamar_exporta_dados(parametro, timeout=30)

        if not isinstance(data, list):
            return {"total": 0, "funcionarios": []}

        matches = []
        vistos  = set()  # deduplicação por nome + cargo

        for f in data:
            nome = (f.get("NOME") or "").strip()
            if not nome or not _nomes_batem(nome_parcial, nome):
                continue

            # Ignora funcionários inativos/demitidos — SOC retorna "Ativo" ou "Inativo"
            situacao = (f.get("SITUACAO") or "").strip()
            if situacao.lower() not in {"ativo", ""}:
                continue

            cargo  = (f.get("NOMECARGO") or "").strip()
            setor  = (f.get("NOMESETOR") or "").strip()
            codigo = str(f.get("CODIGO") or "")

            # Deduplica pelo par (nome, cargo) — mesmo funcionário pode ter múltiplos registros
            chave = (nome.upper(), cargo.upper())
            if chave in vistos:
                continue
            vistos.add(chave)

            matches.append({
                "nome":     nome,
                "cargo":    cargo,
                "setor":    setor,
                "situacao": situacao,
                "codigo":   codigo,
            })

        # Limita a 20 para não estourar o limite de 4096 chars do WhatsApp
        matches = matches[:20]
        return {"total": len(matches), "funcionarios": matches}

    except Exception as e:
        return {"erro": str(e), "funcionarios": []}


def buscar_empresa(telefone: str) -> dict:
    empresa = _buscar_empresa(telefone)
    if empresa:
        return {"encontrada": True, "empresa": empresa}
    return {"encontrada": False, "empresa": None}


def _buscar_asos_193037(codigo_empresa: str, codigo_funcionario: str) -> dict[str, dict]:
    """
    Busca dados clínicos dos ASOs de um funcionário via exporta dados 193037.
    Retorna dict indexado por data de emissão (DTASO) para cruzar com os dados do GED.
    Ex: {"28/10/2025": {"tipo_aso": "Periódico", "resultado": "✅ Apto", "validade": "28/10/2026"}}
    Retorna dict vazio se a chave não estiver configurada ou ocorrer erro.
    """
    if not _SOC_CHAVE_ASO_FUNCIONARIO or not codigo_funcionario:
        return {}

    parametro = {
        "empresa":         _SOC_EMPRESA_PRINCIPAL,
        "codigo":          _CODIGO_EXPORTA_ASO_FUNCIONARIO,
        "chave":           _SOC_CHAVE_ASO_FUNCIONARIO,
        "tipoSaida":       "json",
        "funcionario":     str(codigo_funcionario),
        "tipoASO":         "1,2,3,4,5,6",
        "paramFiltroData": "0",
        "dataInicio":      "",
        "dataFim":         "",
    }

    try:
        data = chamar_exporta_dados(parametro, timeout=30)
        if not isinstance(data, list):
            return {}

        # Cada linha pode ser um exame dentro do mesmo ASO — agrupa por DTASO + TPASO
        por_data: dict[str, dict] = {}
        for linha in data:
            dt_aso   = _campo(linha, "DTASO", "DATAFICHA")
            tipo_cod = str(linha.get("TPASO") or "").strip()
            res_cod  = str(linha.get("RESASOSOC") or "").strip()
            validade = _campo(linha, "DSVALIDADEASO")

            if not dt_aso:
                continue

            # Mantém apenas o registro mais recente por data (evita duplicatas de exames)
            if dt_aso not in por_data:
                por_data[dt_aso] = {
                    "tipo_aso":  _TIPO_ASO.get(tipo_cod, ""),
                    "resultado": _RESULTADO_ASO.get(res_cod, ""),
                    "validade":  validade,
                }

        print(f"[BOT] 193037: {len(por_data)} ASO(s) encontrado(s) para funcionário {codigo_funcionario}")
        return por_data

    except Exception as e:
        print(f"[BOT] Erro ao chamar exporta dados 193037: {e}")
        return {}


def buscar_asos_por_funcionario(
    numero_whatsapp:    str,
    codigo_empresa:     str,
    nome_funcionario:   str,
    janela_dias:        int = 365,
    codigo_funcionario: str = "",
) -> dict:
    data_fim    = datetime.now()
    data_inicio = data_fim - timedelta(days=janela_dias)
    fmt = "%d/%m/%Y"

    # ── Busca tipos de ASO via 193037 (indexado por data) ─────────────────────
    # Retorna dict: {"28/10/2025": {"tipo_aso": "Periódico"}, ...}
    info_tipo = _buscar_asos_193037(codigo_empresa, codigo_funcionario)

    # ── Busca documentos no GED (fonte dos arquivos PDF) ──────────────────────
    try:
        asos = buscar_todos_asos_empresa(
            codigo_empresa,
            data_inicio.strftime(fmt),
            data_fim.strftime(fmt),
        )
    except Exception as e:
        return {"erro": str(e), "candidatos": []}

    candidatos = []
    for aso in asos:
        nome_func = _campo(aso, "NOME_FUNCIONARIO", "NOME", "NOMEFUNCIONARIO")
        if not nome_func or not _nomes_batem(nome_funcionario, nome_func):
            continue

        # Filtra por código do funcionário quando informado (evita homônimos)
        if codigo_funcionario:
            cod_aso = _campo(aso, "CD_FUNCIONARIO", "CODIGO_FUNCIONARIO", "COD_FUNCIONARIO", "MATRICULA", "CODIGO")
            if cod_aso and cod_aso != str(codigo_funcionario):
                continue

        data_emissao = _campo(aso, "DT_EMISSAO")

        # Enriquece com o tipo de ASO do 193037 (se disponível para essa data)
        tipo_aso = info_tipo.get(data_emissao, {}).get("tipo_aso", "")

        candidatos.append({
            "cd_empresa":       _campo(aso, "CD_EMPRESA") or codigo_empresa,
            "cd_ged":           _campo(aso, "CD_GED"),
            "cd_arquivo":       _campo(aso, "CD_ARQUIVO_GED"),
            "nome_funcionario": nome_func,
            "data_emissao":     data_emissao,
            "nome_arquivo":     _campo(aso, "NM_ARQUIVOS_GED", "NM_GED"),
            "tipo_aso":         tipo_aso,
        })

    candidatos = sorted(candidatos, key=lambda x: x["data_emissao"], reverse=True)[:5]

    if candidatos:
        estado = buscar_estado(numero_whatsapp)
        salvar_estado(
            numero=numero_whatsapp,
            fase="aguardando_confirmacao",
            codigo_empresa=(estado or {}).get("codigo_empresa") or codigo_empresa,
            candidatos=candidatos,
            nome_buscado=nome_funcionario,
        )

    return {"total": len(candidatos), "candidatos": candidatos}


def baixar_e_enviar_aso(
    numero_whatsapp:  str,
    cd_empresa:       str,
    cd_ged:           str,
    cd_arquivo:       str,
    nome_funcionario: str = "",
    data_emissao:     str = "",
) -> dict:
    try:
        payload, tipo, nome_retorno = baixar_documento(cd_empresa, cd_ged, cd_arquivo)

        if tipo == "zip":
            payload, nome_retorno = _extrair_pdf_de_zip(payload)

        nome_arquivo = nome_retorno or f"ASO_{nome_funcionario}_{data_emissao}.pdf".replace("/", "-")
        if not nome_arquivo.lower().endswith(".pdf"):
            nome_arquivo += ".pdf"

        with tempfile.NamedTemporaryFile(suffix=".pdf", prefix="aso_bot_", delete=False) as tmp:
            tmp.write(payload)
            tmp_path = tmp.name

        try:
            media_id = _fazer_upload_pdf(tmp_path)
            _enviar_documento_simples(numero_whatsapp, media_id, nome_arquivo)
            resetar_estado(numero_whatsapp)
            return {"sucesso": True, "nome_arquivo": nome_arquivo}
        finally:
            os.unlink(tmp_path)

    except Exception as e:
        return {"sucesso": False, "erro": str(e)}


def escalar_para_humano(numero: str, motivo: str) -> dict:
    estado = buscar_estado(numero)
    salvar_estado(
        numero=numero,
        fase="escalado",
        codigo_empresa=(estado or {}).get("codigo_empresa", ""),
        nome_buscado=motivo,
    )
    return {"escalado": True, "motivo": motivo}
