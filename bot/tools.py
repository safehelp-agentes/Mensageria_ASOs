import os
import io
import sys
import zipfile
import tempfile
from datetime import datetime, timedelta
from unicodedata import normalize as _unorm

from src.soc.api import buscar_todos_asos_empresa, chamar_exporta_dados
from src.soc.downloader import baixar_documento
from src.meta.whatsapp import _fazer_upload_pdf, _enviar_documento_simples
from bot.state import (
    buscar_empresa_por_telefone as _buscar_empresa,
    buscar_estado, salvar_estado, resetar_estado,
)

_SOC_EMPRESA_PRINCIPAL       = os.getenv("SOC_EMPRESA", "").strip()
_SOC_CHAVE_FUNCIONARIOS      = os.getenv("SOC_CHAVE_FUNCIONARIOS", "").strip()
_CODIGO_EXPORTA_FUNCIONARIOS = "192399"


def _normalizar_nome(nome: str) -> str:
    sem_acento = _unorm("NFD", nome).encode("ascii", "ignore").decode("ascii")
    return sem_acento.lower().strip()


def _nomes_batem(busca: str, funcionario: str) -> bool:
    b = _normalizar_nome(busca)
    f = _normalizar_nome(funcionario)
    return all(p in f for p in b.split() if len(p) > 2)


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
        for f in data:
            nome = (f.get("NOME") or "").strip()
            if not nome or not _nomes_batem(nome_parcial, nome):
                continue
            matches.append({
                "nome":     nome,
                "cargo":    (f.get("NOMECARGO") or "").strip(),
                "setor":    (f.get("NOMESETOR") or "").strip(),
                "situacao": (f.get("SITUACAO") or "").strip(),
                "codigo":   str(f.get("CODIGO") or ""),
            })

        return {"total": len(matches), "funcionarios": matches}

    except Exception as e:
        return {"erro": str(e), "funcionarios": []}


def buscar_empresa(telefone: str) -> dict:
    empresa = _buscar_empresa(telefone)
    if empresa:
        return {"encontrada": True, "empresa": empresa}
    return {"encontrada": False, "empresa": None}


def buscar_asos_por_funcionario(
    numero_whatsapp:  str,
    codigo_empresa:   str,
    nome_funcionario: str,
    janela_dias:      int = 365,
) -> dict:
    data_fim    = datetime.now()
    data_inicio = data_fim - timedelta(days=janela_dias)
    fmt = "%d/%m/%Y"

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
        nome_func = _campo(aso, "NOME", "NOME_FUNCIONARIO", "NOMEFUNCIONARIO")
        if not nome_func or not _nomes_batem(nome_funcionario, nome_func):
            continue

        candidatos.append({
            "cd_empresa":       _campo(aso, "CD_EMPRESA") or codigo_empresa,
            "cd_ged":           _campo(aso, "CD_GED"),
            "cd_arquivo":       _campo(aso, "CD_ARQUIVO_GED"),
            "nome_funcionario": nome_func,
            "data_emissao":     _campo(aso, "DATA_EMISSAO", "DATAEMISSAO"),
            "nome_arquivo":     _campo(aso, "NOME_ARQUIVO", "NOMEARQUIVO"),
        })

    candidatos = sorted(candidatos, key=lambda x: x["data_emissao"], reverse=True)[:8]

    if candidatos:
        estado = buscar_estado(numero_whatsapp)
        salvar_estado(
            numero=numero_whatsapp,
            fase="aguardando_confirmacao" if len(candidatos) > 1 else "livre",
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
