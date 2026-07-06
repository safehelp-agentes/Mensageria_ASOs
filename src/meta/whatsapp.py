import os
import time
import requests
from datetime import datetime, timedelta

from src.integrations import chatwoot as _chatwoot
from config import (
    META_WA_TOKEN, META_PHONE_NUMBER_ID, META_TEMPLATE_NAME,
    META_NUMERO_TESTE, META_TIMEOUT, META_API_VERSION,
    ENVIO_REAL_EMPRESAS, USAR_ONTEM,
)
from src.utils.helpers import (
    _requisicao_com_retry, registrar_erro, sanitizar_nome,
    normalizar_numero_whatsapp, numero_parece_valido,
)

# Texto do template `entrega_aso` aprovado na Meta. Espelhado no Chatwoot para
# que a conversa mostre exatamente a mensagem que a empresa recebeu.
_TEXTO_TEMPLATE_ENTREGA_ASO = (
    "Prezado(a), segue em anexo o(s) ASO(s) (Atestado de Saúde Ocupacional) "
    "referente(s) ao(s) exame(s) realizado(s).\n\n"
    "Empresa: {nome_empresa}\n"
    "Data de emissão: {data_emissao}\n\n"
    "Este documento é de caráter oficial. Em caso de dúvidas, entre em contato "
    "com o setor de saúde ocupacional responsável."
)


def _validar_config():
    if not META_WA_TOKEN:
        raise RuntimeError("META_WA_TOKEN não definido no .env")
    if not META_PHONE_NUMBER_ID:
        raise RuntimeError("META_PHONE_NUMBER_ID não definido no .env")


def _headers() -> dict:
    return {"Authorization": f"Bearer {META_WA_TOKEN}", "Content-Type": "application/json"}


def _url_messages() -> str:
    return f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/messages"


def _url_media() -> str:
    return f"https://graph.facebook.com/{META_API_VERSION}/{META_PHONE_NUMBER_ID}/media"


# ── Upload ─────────────────────────────────────────────────────────────────────

def _fazer_upload_pdf(caminho_pdf: str) -> str:
    """Faz upload do PDF para a Meta e retorna o media_id (válido por 30 dias)."""
    _validar_config()

    if not os.path.exists(caminho_pdf):
        raise FileNotFoundError(f"PDF não encontrado: {caminho_pdf}")

    with open(caminho_pdf, "rb") as f:
        conteudo = f.read()

    resp = _requisicao_com_retry(
        requests.post,
        _url_media(),
        headers={"Authorization": f"Bearer {META_WA_TOKEN}"},
        files={
            "file":              (os.path.basename(caminho_pdf), conteudo, "application/pdf"),
            "messaging_product": (None, "whatsapp"),
            "type":              (None, "application/pdf"),
        },
        timeout=META_TIMEOUT,
    )

    print(f"[META] Upload PDF status: {resp.status_code}")
    if resp.status_code >= 300:
        raise RuntimeError(f"Erro upload PDF Meta: HTTP {resp.status_code} — {resp.text[:300]}")

    media_id = resp.json().get("id")
    if not media_id:
        raise RuntimeError(f"Meta não retornou media_id. Resposta: {resp.text[:300]}")

    print(f"[META] PDF enviado. media_id: {media_id}")
    return media_id


# ── Envios ─────────────────────────────────────────────────────────────────────

def enviar_template_com_pdf(numero: str, media_id: str, nome_empresa: str,
                             data_emissao: str, nome_arquivo_pdf: str = "") -> dict:
    """Envia o template aprovado com PDF no header e variáveis nome_empresa + data_emissao."""
    _validar_config()

    payload = {
        "messaging_product": "whatsapp",
        "to":                normalizar_numero_whatsapp(numero),
        "type":              "template",
        "template": {
            "name":     META_TEMPLATE_NAME,
            "language": {"code": "pt_BR"},
            "components": [
                {
                    "type": "header",
                    "parameters": [{
                        "type":     "document",
                        "document": {
                            "id":       media_id,
                            "filename": nome_arquivo_pdf or f"ASO_{sanitizar_nome(nome_empresa)}.pdf",
                        },
                    }],
                },
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": nome_empresa},
                        {"type": "text", "text": data_emissao},
                    ],
                },
            ],
        },
    }

    resp = _requisicao_com_retry(
        requests.post, _url_messages(),
        headers=_headers(), json=payload, timeout=META_TIMEOUT,
    )

    print(f"[META] Envio template status: {resp.status_code}")
    if resp.status_code >= 300:
        raise RuntimeError(f"Erro envio template Meta: HTTP {resp.status_code} — {resp.text[:300]}")

    return resp.json()


def _enviar_documento_simples(numero: str, media_id: str, nome_arquivo: str) -> dict:
    """Envia documento simples dentro de uma conversa já aberta (sem custo de nova conversa)."""
    _validar_config()

    payload = {
        "messaging_product": "whatsapp",
        "to":                normalizar_numero_whatsapp(numero),
        "type":              "document",
        "document":          {"id": media_id, "filename": nome_arquivo},
    }

    resp = _requisicao_com_retry(
        requests.post, _url_messages(),
        headers=_headers(), json=payload, timeout=META_TIMEOUT,
    )

    print(f"[META] Envio documento status: {resp.status_code}")
    if resp.status_code >= 300:
        raise RuntimeError(f"Erro envio documento Meta: HTTP {resp.status_code} — {resp.text[:300]}")

    return resp.json()


def enviar_texto_meta(numero: str, mensagem: str, chatwoot_mirror: bool = True) -> dict:
    """Envia mensagem de texto simples.

    chatwoot_mirror=False quando o texto já está no Chatwoot (ex: resposta de agente humano)
    para não criar mensagem duplicada na conversa.
    """
    _validar_config()

    payload = {
        "messaging_product": "whatsapp",
        "to":                normalizar_numero_whatsapp(numero),
        "type":              "text",
        "text":              {"body": mensagem},
    }

    resp = _requisicao_com_retry(
        requests.post, _url_messages(),
        headers=_headers(), json=payload, timeout=META_TIMEOUT,
    )

    print(f"[META] Envio texto status: {resp.status_code}")
    if resp.status_code >= 300:
        raise RuntimeError(f"Erro envio texto Meta: HTTP {resp.status_code} — {resp.text[:300]}")

    if chatwoot_mirror:
        _chatwoot.espelhar_envio_sistema(numero, mensagem)

    return resp.json()


# ── Orquestrador de envio por empresa ─────────────────────────────────────────

def enviar_pdfs_empresa_meta(resultado: dict, numero_destino: str) -> dict:
    """
    Envia todos os PDFs de uma empresa usando apenas 1 conversa:
    - 1º PDF → template (abre a conversa)
    - demais → documento simples dentro da janela de 24h (sem custo extra)
    """
    _validar_config()

    pasta_empresa = resultado.get("pasta_pdfs")
    nome_empresa  = resultado.get("nome_empresa", "")
    data_ref      = resultado.get("data", "")
    data_emissao  = resultado.get("data_emissao") or data_ref.replace("-", "/")

    if not pasta_empresa or not os.path.exists(pasta_empresa):
        raise RuntimeError(f"Pasta de PDFs não encontrada para empresa {nome_empresa}")

    pdfs = sorted([
        os.path.join(pasta_empresa, f)
        for f in os.listdir(pasta_empresa)
        if f.lower().endswith(".pdf")
    ])

    if not pdfs:
        raise RuntimeError(f"Nenhum PDF encontrado para empresa {nome_empresa}")

    total_asos    = len(pdfs)
    respostas     = []
    enviados_ok   = 0
    enviados_erro = 0

    print(f"  [META] {total_asos} PDF(s) → {numero_destino} (1 conversa)")

    for idx, caminho_pdf in enumerate(pdfs):
        nome_arquivo = os.path.basename(caminho_pdf)
        eh_primeiro  = idx == 0

        print(f"    [META] {'[TEMPLATE]' if eh_primeiro else '[DOCUMENTO]'} {nome_arquivo}")

        try:
            media_id = _fazer_upload_pdf(caminho_pdf)

            if eh_primeiro:
                resp = enviar_template_com_pdf(
                    numero=numero_destino,
                    media_id=media_id,
                    nome_empresa=nome_empresa,
                    data_emissao=data_emissao,
                    nome_arquivo_pdf=nome_arquivo,
                )
                if total_asos > 1:
                    print("    [META] Aguardando 3s para conversa ser estabelecida...")
                    time.sleep(3)
            else:
                resp = _enviar_documento_simples(numero_destino, media_id, nome_arquivo)

            respostas.append({
                "arquivo": nome_arquivo,
                "tipo":    "template" if eh_primeiro else "documento",
                "sucesso": True,
                "resposta": resp,
            })
            enviados_ok += 1
            print(f"    [META] Enviado com sucesso: {nome_arquivo}")
            time.sleep(1)

        except Exception as e:
            respostas.append({
                "arquivo": nome_arquivo,
                "tipo":    "template" if eh_primeiro else "documento",
                "sucesso": False,
                "erro":    str(e),
            })
            enviados_erro += 1
            registrar_erro(f"[META] Erro ao enviar {nome_arquivo} para {numero_destino}: {e}")

    if enviados_ok > 0:
        _chatwoot.espelhar_envio_sistema(
            numero_destino,
            _TEXTO_TEMPLATE_ENTREGA_ASO.format(
                nome_empresa=nome_empresa,
                data_emissao=data_emissao,
            ),
        )

    return {
        "empresa":       nome_empresa,
        "total":         total_asos,
        "enviados_ok":   enviados_ok,
        "enviados_erro": enviados_erro,
        "respostas":     respostas,
    }


def enviar_teste_sem_aso_meta(data_consulta: str) -> dict:
    """Envia mensagem de texto para o número de teste quando não há ASOs no dia."""
    data_mostrar = (
        (datetime.today() - timedelta(days=1)).strftime("%d/%m/%Y")
        if USAR_ONTEM
        else data_consulta
    )
    return enviar_texto_meta(
        META_NUMERO_TESTE,
        f"Automação ASOs — SafeWork\n"
        f"Data consultada: {data_mostrar}\n"
        f"Nenhum ASO encontrado para as empresas filtradas.\n"
        f"Se esta mensagem chegou, a integração está funcionando.",
    )


def resolver_destino_envio(numero_empresa: str) -> str:
    """Retorna o número real da empresa ou cai no número de teste conforme configuração."""
    if ENVIO_REAL_EMPRESAS and numero_parece_valido(numero_empresa):
        return normalizar_numero_whatsapp(numero_empresa)
    return normalizar_numero_whatsapp(META_NUMERO_TESTE)
