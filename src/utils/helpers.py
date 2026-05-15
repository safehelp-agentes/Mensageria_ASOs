import re
import time
import requests
from datetime import datetime, timedelta

from config import USAR_ONTEM


def registrar_erro(msg: str):
    print("ERRO REGISTRADO:", str(msg))


def _requisicao_com_retry(metodo, url, *, tentativas=3, backoff_base=2.0, **kwargs):
    """Executa requisição HTTP com retry e backoff exponencial em erros de rede ou 5xx."""
    ultimo_erro = None
    for tentativa in range(1, tentativas + 1):
        try:
            resp = metodo(url, **kwargs)
            if resp.status_code < 500:
                return resp
            ultimo_erro = RuntimeError(f"Servidor retornou HTTP {resp.status_code}")
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            ultimo_erro = e
        if tentativa < tentativas:
            espera = backoff_base ** tentativa
            print(f"[RETRY {tentativa}/{tentativas}] Aguardando {espera:.0f}s: {ultimo_erro}")
            time.sleep(espera)
    raise ultimo_erro


def sanitizar_nome(nome: str) -> str:
    nome = (nome or "").strip()
    nome = re.sub(r'[\\/:*?"<>|]+', "_", nome)
    nome = re.sub(r"\s+", " ", nome).strip()
    return nome


def obter_data_consulta(usar_ontem: bool | None = None, data_especifica: str | None = None) -> str:
    if data_especifica:
        try:
            dt = datetime.strptime(data_especifica.strip(), "%d/%m/%Y")
        except ValueError:
            raise ValueError(f"Data inválida '{data_especifica}'. Use o formato DD/MM/AAAA (ex: 09/05/2026).")
        return dt.strftime("%d/%m/%Y")
    flag = usar_ontem if usar_ontem is not None else USAR_ONTEM
    if flag:
        return (datetime.now() - timedelta(days=1)).strftime("%d/%m/%Y")
    return datetime.now().strftime("%d/%m/%Y")


def formatar_data_ws(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(dt.microsecond / 1000):03d}Z"


def payload_tipo(payload: bytes) -> str:
    if payload.startswith(b"%PDF"):
        return "pdf"
    if payload.startswith(b"PK\x03\x04"):
        return "zip"
    return "desconhecido"


def normalizar_numero(numero: str) -> str:
    return re.sub(r"\D+", "", str(numero or "").strip())


def normalizar_numero_whatsapp(numero: str) -> str:
    numero = normalizar_numero(numero)
    if not numero:
        return ""
    if numero.startswith("0"):
        numero = numero.lstrip("0")
    if len(numero) in (10, 11):
        numero = f"55{numero}"
    return numero


def numero_parece_valido(numero: str) -> bool:
    return len(normalizar_numero_whatsapp(numero)) >= 12
