import os
import uuid
import json
import base64
import hashlib
import email
import re
import time
import requests
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

from config import (
    SOC_DOWNLOAD_URL, SOC_WS_USUARIO, SOC_WS_PASSWORD,
    CODIGO_EMPRESA_PRINCIPAL, CODIGO_RESPONSAVEL, CODIGO_USUARIO,
    PASTA_DEBUG,
)
from src.utils.helpers import formatar_data_ws, payload_tipo


# ── WS-Security ────────────────────────────────────────────────────────────────

def gerar_wsse_password_digest(usuario: str | None = None) -> dict:
    usuario_ws = usuario or SOC_WS_USUARIO

    nonce_bytes = os.urandom(16)
    nonce_b64   = base64.b64encode(nonce_bytes).decode("utf-8")

    agora   = datetime.now(timezone.utc)
    created = formatar_data_ws(agora)
    expires = formatar_data_ws(agora + timedelta(minutes=1))

    digest = hashlib.sha1(
        nonce_bytes + created.encode("utf-8") + SOC_WS_PASSWORD.encode("utf-8")
    ).digest()

    return {
        "usuario":        usuario_ws,
        "nonce":          nonce_b64,
        "created":        created,
        "expires":        expires,
        "password_value": base64.b64encode(digest).decode("utf-8"),
        "password_type":  "http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest",
        "timestamp_id":   f"TS-{uuid.uuid4().hex}",
        "token_id":       f"UsernameToken-{uuid.uuid4().hex}",
    }


def montar_xml_download_por_lote(cd_empresa: str, cd_ged: str, cd_arquivo: str, wsse: dict) -> str:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                  xmlns:ser="http://services.soc.age.com/"
                  xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"
                  xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">
  <soapenv:Header>
    <wsse:Security>
      <wsu:Timestamp wsu:Id="{wsse['timestamp_id']}">
        <wsu:Created>{wsse['created']}</wsu:Created>
        <wsu:Expires>{wsse['expires']}</wsu:Expires>
      </wsu:Timestamp>
      <wsse:UsernameToken wsu:Id="{wsse['token_id']}">
        <wsse:Username>{wsse['usuario']}</wsse:Username>
        <wsse:Password Type="{wsse['password_type']}">{wsse['password_value']}</wsse:Password>
        <wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">{wsse['nonce']}</wsse:Nonce>
        <wsu:Created>{wsse['created']}</wsu:Created>
      </wsse:UsernameToken>
    </wsse:Security>
  </soapenv:Header>
  <soapenv:Body>
    <ser:downloadArquivosGedPorLote>
      <downloadPorLote>
        <identificacaoWsVo>
          <codigoEmpresaPrincipal>{CODIGO_EMPRESA_PRINCIPAL}</codigoEmpresaPrincipal>
          <codigoResponsavel>{CODIGO_RESPONSAVEL}</codigoResponsavel>
          <codigoUsuario>{CODIGO_USUARIO}</codigoUsuario>
        </identificacaoWsVo>
        <codigoEmpresa>{cd_empresa}</codigoEmpresa>
        <codigosArquivosGed>{cd_arquivo}</codigosArquivosGed>
        <codigoGed>{cd_ged}</codigoGed>
      </downloadPorLote>
    </ser:downloadArquivosGedPorLote>
  </soapenv:Body>
</soapenv:Envelope>"""


# ── Parsing da resposta SOAP multipart ────────────────────────────────────────

def _normalizar_content_id(content_id: str | None) -> str | None:
    if not content_id:
        return None
    return content_id.strip().strip("<>").strip()


def _extrair_codigo_mensagem(xml_text: str | None) -> tuple[str | None, str | None]:
    if not xml_text:
        return None, None
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None, None
    return root.findtext(".//codigoMensagem"), root.findtext(".//mensagem")


def _extrair_soap_fault(xml_text: str | None) -> tuple[str | None, str | None]:
    if not xml_text:
        return None, None
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return None, None
    return root.findtext(".//faultcode"), root.findtext(".//faultstring")


def _eh_fault_seguranca(faultcode: str | None, faultstring: str | None) -> bool:
    texto = f"{faultcode or ''} {faultstring or ''}".lower()
    return any(
        trecho in texto
        for trecho in (
            "invalidsecurity",
            "failedauthentication",
            "username token",
            "security token",
            "replay attack",
        )
    )


def _extrair_href_cid_do_xml(xml_resp: str | None) -> str | None:
    if not xml_resp:
        return None
    match = re.search(r'href="cid:([^"]+)"', xml_resp)
    return match.group(1).strip() if match else None


def _extrair_multipart(response) -> tuple:
    content_type_header = response.headers.get("Content-Type", "")
    raw = (
        f"Content-Type: {content_type_header}\r\n"
        f"MIME-Version: 1.0\r\n\r\n"
    ).encode("utf-8") + response.content

    msg             = email.message_from_bytes(raw)
    xml_resp        = None
    partes_binarias = []

    for part in msg.walk():
        if part.get_content_maintype() == "multipart":
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue

        ctype    = part.get_content_type()
        cid      = _normalizar_content_id(part.get("Content-ID"))
        filename = part.get_filename()

        if ctype in ("application/xop+xml", "text/xml", "application/soap+xml"):
            xml_resp = payload.decode("utf-8", errors="ignore")
        else:
            partes_binarias.append({
                "content_id":   cid,
                "content_type": ctype,
                "filename":     filename,
                "payload":      payload,
            })

    href_cid          = _extrair_href_cid_do_xml(xml_resp)
    payload_escolhido = None
    nome_escolhido    = None

    if href_cid:
        href_cid_norm = _normalizar_content_id(href_cid)
        for parte in partes_binarias:
            if _normalizar_content_id(parte["content_id"]) == href_cid_norm:
                payload_escolhido = parte["payload"]
                nome_escolhido    = parte["filename"] or "arquivo_referenciado.bin"
                break

    if payload_escolhido is None:
        for parte in partes_binarias:
            if payload_tipo(parte["payload"]) in ("pdf", "zip"):
                payload_escolhido = parte["payload"]
                nome_escolhido    = parte["filename"] or "arquivo_referenciado.bin"
                break

    return xml_resp, nome_escolhido, payload_escolhido, partes_binarias


def _interpretar_resposta(response) -> tuple:
    content_type = response.headers.get("Content-Type", "").lower()
    if "multipart/related" in content_type:
        return _extrair_multipart(response)
    return response.text, None, None, []


def _mascarar_xml_request(xml: str) -> str:
    xml = re.sub(r"(<wsse:Username>).*?(</wsse:Username>)", r"\1***\2", xml, flags=re.S)
    xml = re.sub(r"(<wsse:Password[^>]*>).*?(</wsse:Password>)", r"\1***\2", xml, flags=re.S)
    xml = re.sub(r"(<wsse:Nonce[^>]*>).*?(</wsse:Nonce>)", r"\1***\2", xml, flags=re.S)
    return xml


def _salvar_debug(cd_empresa: str, cd_ged: str, cd_arquivo: str, xml_resp, partes: list, payload,
                  response=None, xml_request: str | None = None):
    os.makedirs(PASTA_DEBUG, exist_ok=True)
    base = f"{cd_empresa}_{cd_ged}_{cd_arquivo}"

    with open(os.path.join(PASTA_DEBUG, f"{base}_xml.txt"), "w", encoding="utf-8") as f:
        f.write(xml_resp or "")

    if xml_request:
        with open(os.path.join(PASTA_DEBUG, f"{base}_request.xml"), "w", encoding="utf-8") as f:
            f.write(_mascarar_xml_request(xml_request))

    if response is not None:
        http_meta = {
            "status_code": response.status_code,
            "reason":      response.reason,
            "url":         response.url,
            "headers":     dict(response.headers),
            "text_preview": response.text[:4000],
        }
        with open(os.path.join(PASTA_DEBUG, f"{base}_http.json"), "w", encoding="utf-8") as f:
            json.dump(http_meta, f, ensure_ascii=False, indent=2)

    meta = [
        {
            "idx":                   i,
            "content_id":            p["content_id"],
            "content_type":          p["content_type"],
            "filename":              p["filename"],
            "primeiros_bytes_hex":   p["payload"][:20].hex(),
            "primeiros_bytes_ascii": repr(p["payload"][:20]),
            "tamanho":               len(p["payload"]),
            "tipo_detectado":        payload_tipo(p["payload"]),
        }
        for i, p in enumerate(partes, start=1)
    ]

    with open(os.path.join(PASTA_DEBUG, f"{base}_partes.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    if payload:
        with open(os.path.join(PASTA_DEBUG, f"{base}_payload.bin"), "wb") as f:
            f.write(payload)


# ── Ponto de entrada público ───────────────────────────────────────────────────

def baixar_documento(cd_empresa: str, cd_ged: str, cd_arquivo: str) -> tuple[bytes, str, str | None]:
    """Faz o download do documento GED e retorna (payload, tipo, nome_retorno)."""
    ultimo_response = None
    ultimo_erro     = None
    ultimo_xml      = None

    for tentativa in range(1, 4):
        wsse = gerar_wsse_password_digest()
        xml  = montar_xml_download_por_lote(cd_empresa, cd_ged, cd_arquivo, wsse)
        ultimo_xml = xml

        try:
            response = requests.post(
                SOC_DOWNLOAD_URL,
                data=xml.encode("utf-8"),
                headers={"Content-Type": "text/xml; charset=UTF-8", "SOAPAction": "", "Accept": "*/*"},
                timeout=120,
            )
            ultimo_response = response
            if response.status_code < 500:
                break

            xml_resp, _, payload_debug, partes_debug = _interpretar_resposta(response)
            faultcode, faultstring = _extrair_soap_fault(xml_resp)
            if _eh_fault_seguranca(faultcode, faultstring):
                _salvar_debug(
                    cd_empresa, cd_ged, cd_arquivo,
                    xml_resp, partes_debug, payload_debug,
                    response=response, xml_request=xml,
                )
                raise RuntimeError(
                    f"Falha de segurança no SOAP SOC. faultcode={faultcode} "
                    f"faultstring={faultstring}. Debug salvo em {PASTA_DEBUG}."
                )

            ultimo_erro = RuntimeError(f"Servidor retornou HTTP {response.status_code}")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            response = None
            ultimo_erro = e

        if tentativa < 3:
            espera = 2.0 ** tentativa
            print(f"[RETRY {tentativa}/3] Aguardando {espera:.0f}s: {ultimo_erro}")
            time.sleep(espera)
    else:
        if ultimo_response is not None:
            xml_resp, _, payload_debug, partes_debug = _interpretar_resposta(ultimo_response)
            _salvar_debug(
                cd_empresa, cd_ged, cd_arquivo,
                xml_resp, partes_debug, payload_debug,
                response=ultimo_response, xml_request=ultimo_xml,
            )
            preview = (ultimo_response.text or "")[:300].replace("\n", " ")
            raise RuntimeError(
                f"Servidor retornou HTTP {ultimo_response.status_code}. "
                f"Debug salvo em {PASTA_DEBUG}. Resposta: {preview}"
            )
        raise ultimo_erro

    xml_resp, nome_retorno, payload, partes = _interpretar_resposta(response)
    codigo_msg, mensagem = _extrair_codigo_mensagem(xml_resp)

    if codigo_msg != "SOC-100":
        _salvar_debug(cd_empresa, cd_ged, cd_arquivo, xml_resp, partes, payload, response=response, xml_request=xml)
        raise RuntimeError(f"Retorno não foi sucesso. codigoMensagem={codigo_msg} mensagem={mensagem}")

    if not payload:
        _salvar_debug(cd_empresa, cd_ged, cd_arquivo, xml_resp, partes, payload, response=response, xml_request=xml)
        raise RuntimeError("Nenhum payload binário encontrado.")

    tipo = payload_tipo(payload)
    if tipo not in ("pdf", "zip"):
        _salvar_debug(cd_empresa, cd_ged, cd_arquivo, xml_resp, partes, payload, response=response, xml_request=xml)
        raise RuntimeError(f"Payload em formato inesperado. Tipo={tipo}. Primeiros bytes: {repr(payload[:20])}")

    return payload, tipo, nome_retorno
