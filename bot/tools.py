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

# Exporta Dados 193037 — ASOs do Funcionário (tipo de exame)
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
    # Com múltiplas palavras (ex: "abimael soares"): TODAS devem aparecer no nome
    # Com uma palavra (ex: "abimael"): basta aparecer
    if len(palavras) >= 2:
        return all(p in f for p in palavras)
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


def _normalizar_data(data: str) -> str:
    """
    Normaliza datas para o formato DD/MM/YYYY.
    Suporta: "DD/MM/YYYY", "YYYY-MM-DD", "DD/MM/YYYY HH:MM:SS", "YYYY-MM-DDTHH:MM:SS"
    """
    if not data:
        return ""
    data = data.strip().split("T")[0].split(" ")[0]  # remove parte de hora
    if "-" in data:
        partes = data.split("-")
        if len(partes) == 3:
            return f"{partes[2]}/{partes[1]}/{partes[0]}"
    return data  # já está em DD/MM/YYYY


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
        vistos  = set()

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

            # Deduplica pelo par (nome, cargo)
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
    Busca tipos de ASO de um funcionário via exporta dados 193037.
    Tenta com empresa principal e depois com empresa cliente.
    Retorna dict indexado por data normalizada (DD/MM/YYYY).
    """
    if not _SOC_CHAVE_ASO_FUNCIONARIO or not codigo_funcionario:
        return {}

    for empresa, label in [(_SOC_EMPRESA_PRINCIPAL, "principal"), (codigo_empresa, "cliente")]:
        parametro = {
            "empresa":         empresa,
            "codigo":          _CODIGO_EXPORTA_ASO_FUNCIONARIO,
            "chave":           _SOC_CHAVE_ASO_FUNCIONARIO,
            "tipoSaida":       "json",
            "funcionario":     str(codigo_funcionario),
            "tipoASO":         "1,2,3,4,5,6",
            "paramFiltroData": "0",
            "dataInicio":      "",
            "dataFim":         "",
        }
        print(f"[BOT] 193037 tentativa empresa={empresa} ({label}), funcionario={codigo_funcionario}")

        try:
            data = chamar_exporta_dados(parametro, timeout=30)
            print(f"[BOT] 193037 ({label}): tipo={type(data).__name__}, qtd={len(data) if isinstance(data, list) else '-'}")

            if not isinstance(data, list) or not data:
                continue

            print(f"[BOT] 193037 primeiro item: {data[0]}")
            por_data: dict[str, dict] = {}
            for linha in data:
                dt_raw   = _campo(linha, "DTASO", "DATAFICHA")
                dt_aso   = _normalizar_data(dt_raw)
                # Indexa por MM/YYYY para tolerar diferença de 1-2 dias entre GED e 193037
                chave    = dt_aso[3:] if len(dt_aso) >= 7 else dt_aso
                tipo_cod = str(linha.get("TPASO") or "").strip()
                print(f"[BOT] 193037 linha: dt_raw={dt_raw!r} → dt_aso={dt_aso!r}, chave={chave!r}, tpaso={tipo_cod!r}")
                if chave and chave not in por_data:
                    por_data[chave] = {"tipo_aso": _TIPO_ASO.get(tipo_cod, "")}

            print(f"[BOT] 193037: {len(por_data)} ASO(s) — datas: {list(por_data.keys())}")
            return por_data

        except Exception as e:
            print(f"[BOT] 193037 erro ({label}): {e}")

    print(f"[BOT] 193037: nenhuma tentativa retornou dados para funcionario={codigo_funcionario}")
    return {}


def buscar_asos_por_funcionario(
    numero_whatsapp:    str,
    codigo_empresa:     str,
    nome_funcionario:   str,
    janela_dias:        int = 3650,
    codigo_funcionario: str = "",
) -> dict:
    data_fim    = datetime.now()
    data_inicio = data_fim - timedelta(days=janela_dias)
    fmt = "%d/%m/%Y"

    # ── Busca tipos de ASO via 193037 (indexado por data) ─────────────────────
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

    print(f"[BOT] GED: {len(asos)} documento(s) no período — empresa={codigo_empresa}")

    candidatos = []
    for aso in asos:
        nome_func = _campo(aso, "NOME_FUNCIONARIO", "NOME", "NOMEFUNCIONARIO")
        if not nome_func or not _nomes_batem(nome_funcionario, nome_func):
            continue

        if codigo_funcionario:
            cod_aso = _campo(aso, "CD_FUNCIONARIO", "CODIGO_FUNCIONARIO", "COD_FUNCIONARIO", "MATRICULA", "CODIGO")
            print(f"[BOT] GED match nome: {nome_func!r} | cd_func GED={cod_aso!r} | esperado={codigo_funcionario!r}")
            if cod_aso and cod_aso != str(codigo_funcionario):
                continue

        data_emissao = _normalizar_data(_campo(aso, "DT_EMISSAO"))
        # Busca o tipo pelo MM/YYYY para tolerar diferença de dias entre GED e 193037
        chave_mes    = data_emissao[3:] if len(data_emissao) >= 7 else data_emissao
        tipo_aso     = info_tipo.get(chave_mes, {}).get("tipo_aso", "")

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
