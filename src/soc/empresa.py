import requests
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from config import (
    SOC_IMPORTACAO_EMPRESA_URL,
    SOC_CHAVE_IMPORTACAO_EMPRESA,
    SOC_HOMOLOGACAO,
    CODIGO_EMPRESA_PRINCIPAL,
    CODIGO_RESPONSAVEL,
    CODIGO_USUARIO,
)
from src.soc.downloader import gerar_wsse_password_digest


@dataclass
class ResultadoWs:
    codigo: str
    sucesso: bool
    mensagem: str
    detalhes: list = field(default_factory=list)
    avisos: list = field(default_factory=list)


def _linhas_xml(pares: list, indent: int) -> str:
    """Gera linhas XML para cada (tag, valor) ignorando valores vazios/None."""
    pad = " " * indent
    return "".join(
        f"{pad}<{c}>{escape(str(v).strip())}</{c}>\n"
        for c, v in pares
        if v is not None and str(v).strip()
    )


def _montar_envelope_alterar_empresa(dados: dict, wsse: dict) -> str:
    # WS-Security
    usuario = wsse["usuario"]
    if not usuario.startswith("U"):
        usuario = f"U{usuario}"
    homologacao = "true" if SOC_HOMOLOGACAO else "false"

    # identificacaoWsVo — chaveAcesso é opcional (minOccurs=0 no WSDL)
    linhas_id = ""
    if SOC_CHAVE_IMPORTACAO_EMPRESA:
        linhas_id += f"            <chaveAcesso>{escape(SOC_CHAVE_IMPORTACAO_EMPRESA)}</chaveAcesso>\n"
    linhas_id += _linhas_xml([
        ("codigoEmpresaPrincipal", CODIGO_EMPRESA_PRINCIPAL),
        ("codigoResponsavel",      CODIGO_RESPONSAVEL),
    ], indent=12)
    linhas_id += f"            <homologacao>{homologacao}</homologacao>\n"
    linhas_id += f"            <codigoUsuario>{escape(CODIGO_USUARIO)}</codigoUsuario>\n"

    # enderecoWsVo (nested dentro de dadosEmpresaWsVo)
    end = dados.get("endereco") or {}
    linhas_end = _linhas_xml([
        ("bairro",          end.get("bairro")),
        ("cep",             end.get("cep")),
        ("cidade",          end.get("cidade")),
        ("codigoMunicipio", end.get("codigoMunicipio")),
        ("complemento",     end.get("complemento")),
        ("endereco",        end.get("endereco")),
        ("estado",          end.get("estado")),
        ("numero",          end.get("numero")),
    ], indent=16)
    bloco_end = f"            <endereco>\n{linhas_end}            </endereco>\n" if linhas_end else ""

    # dadosEmpresaWsVo
    linhas_dados = _linhas_xml([
        ("nomeAbreviado",      dados.get("nomeAbreviado")),
        ("razaoSocial",        dados.get("razaoSocial")),
        ("cnpjCeiCpf",         dados.get("cnpjCeiCpf")),
        ("numeroCnpj",         dados.get("numeroCnpj")),
        ("numeroCpf",          dados.get("numeroCpf")),
        ("numeroCei",          dados.get("numeroCei")),
        ("inscricaoEstadual",  dados.get("inscricaoEstadual")),
        ("inscricaoMunicipal", dados.get("inscricaoMunicipal")),
        ("observacao",         dados.get("observacao")),
        ("telefoneCat",        dados.get("telefoneCat")),
        ("codigoCliente",      dados.get("codigoCliente")),
        ("codigoCnae",         dados.get("codigoCnae")),
        ("tipoCnae",           dados.get("tipoCnae")),
        ("tipoEmpresa",        dados.get("tipoEmpresa")),
    ], indent=12)

    bloco_dados = linhas_dados + bloco_end

    # AlterarEmpresaWsVo — campos próprios da alteração
    codigo_empresa = escape(str(dados.get("codigo") or ""))
    tipo_busca     = escape(str(dados.get("tipoBusca") or "CODIGO_SOC"))
    ativo_val      = dados.get("ativo")
    linha_ativo    = f"          <ativo>{'true' if ativo_val else 'false'}</ativo>\n" if ativo_val is not None else ""

    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:tns="http://services.soc.age.com/">
  <soapenv:Header>
    <wsse:Security xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
                   xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
      <wsse:UsernameToken>
        <wsse:Username>{usuario}</wsse:Username>
        <wsse:Password Type="{wsse['password_type']}">{wsse['password_value']}</wsse:Password>
        <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{wsse['nonce']}</wsse:Nonce>
        <wsu:Created>{wsse['created']}</wsu:Created>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>
  <soapenv:Body>
    <tns:alterarEmpresa>
      <AlterarEmpresaWsVo>
          <identificacaoWsVo>
{linhas_id}          </identificacaoWsVo>
          <dadosEmpresaWsVo>
{bloco_dados}          </dadosEmpresaWsVo>
          <codigo>{codigo_empresa}</codigo>
          <tipoBusca>{tipo_busca}</tipoBusca>
{linha_ativo}      </AlterarEmpresaWsVo>
    </tns:alterarEmpresa>
  </soapenv:Body>
</soapenv:Envelope>"""


def _parsear_resposta(xml_text: str) -> ResultadoWs:
    try:
        root = ET.fromstring(xml_text)
    except Exception as e:
        return ResultadoWs(
            codigo="PARSE_ERROR",
            sucesso=False,
            mensagem=f"Falha ao parsear XML de resposta: {e}",
        )

    # codigoMensagem fica dentro de <informacaoGeral> conforme o WSDL
    info = root.find(".//informacaoGeral")
    if info is not None:
        codigo   = info.findtext("codigoMensagem") or ""
        mensagem = info.findtext("mensagem") or ""
        detalhes = [
            el.findtext("mensagem") or ""
            for el in info.findall("mensagemOperacaoDetalheList")
            if (el.findtext("mensagem") or "").strip()
        ]
    else:
        # Fallback para SOAPFault (erros de autenticação, etc.)
        codigo   = root.findtext(".//faultcode") or ""
        mensagem = root.findtext(".//faultstring") or root.findtext(".//mensagem") or ""
        detalhes = []

    avisos = [
        el.text
        for el in root.findall(".//avisos/aviso")
        if (el.text or "").strip()
    ]

    if codigo == "SOC-206":
        mensagem = "Empresa sem acesso ao WebService. Habilitar na tela 337 do SOC."
    elif codigo == "SOC-207":
        mensagem = "IP não autorizado nas configurações do WebService da empresa."

    return ResultadoWs(
        codigo=codigo,
        sucesso=(codigo == "SOC-100"),
        mensagem=mensagem,
        detalhes=detalhes,
        avisos=avisos,
    )


def _validar_configuracao() -> None:
    faltando = []
    if not CODIGO_EMPRESA_PRINCIPAL:
        faltando.append("SOC_EMPRESA (ou SOC_CODIGO_EMPRESA_PRINCIPAL)")
    if not CODIGO_RESPONSAVEL:
        faltando.append("SOC_CODIGO_RESPONSAVEL")
    if not CODIGO_USUARIO:
        faltando.append("SOC_CODIGO_USUARIO")
    if not SOC_IMPORTACAO_EMPRESA_URL:
        faltando.append("SOC_IMPORTACAO_EMPRESA_URL")
    if faltando:
        raise ValueError(f"Variáveis obrigatórias ausentes no .env: {', '.join(faltando)}")


def atualizar_empresa(dados_empresa: dict) -> ResultadoWs:
    """Atualiza dados cadastrais de uma Empresa/Cliente via alterarEmpresa (SOAP/WS-Security)."""
    _validar_configuracao()

    if not (dados_empresa.get("codigo") or "").strip():
        return ResultadoWs(
            codigo="ERRO_LOCAL",
            sucesso=False,
            mensagem="Campo 'codigo' ausente. Sem código o SOC não consegue identificar qual empresa alterar.",
        )

    wsse     = gerar_wsse_password_digest()
    envelope = _montar_envelope_alterar_empresa(dados_empresa, wsse)

    try:
        response = requests.post(
            SOC_IMPORTACAO_EMPRESA_URL,
            data=envelope.encode("utf-8"),
            headers={
                "Content-Type": "text/xml; charset=utf-8",
                "SOAPAction":   "",
            },
            timeout=60,
        )
    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
        return ResultadoWs(
            codigo="ERRO_CONEXAO",
            sucesso=False,
            mensagem=f"Falha de conexão com o SOC: {e}",
        )

    return _parsear_resposta(response.text)
