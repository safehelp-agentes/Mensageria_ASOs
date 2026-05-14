import os
from pathlib import Path
from dotenv import load_dotenv


def _carregar_dotenv():
    caminho_atual = Path(__file__).resolve()
    for pasta in [caminho_atual.parent] + list(caminho_atual.parents):
        env_file = pasta / ".env"
        if env_file.exists():
            load_dotenv(dotenv_path=env_file)
            return str(env_file)
    load_dotenv()
    return None


_carregar_dotenv()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ── Pastas de saída ────────────────────────────────────────────────────────────
PASTA_TEMP           = os.path.join(BASE_DIR, "output", "temp_asos")
PASTA_DEBUG          = os.path.join(BASE_DIR, "output", "debug_downloads")
PASTA_SAIDA_LISTAGEM = os.path.join(BASE_DIR, "output", "saida_asos")

# ── Arquivos de estado ─────────────────────────────────────────────────────────
ARQUIVO_PENDENTES = os.path.join(BASE_DIR, "data", "pendentes.json")
ARQUIVO_ERROS_ASO = os.path.join(BASE_DIR, "data", "erros_aso.json")

# ── SOC API ────────────────────────────────────────────────────────────────────
SOC_URL          = (os.getenv("SOC_URL") or "https://ws1.soc.com.br/WebSoc/exportadados").strip()
SOC_DOWNLOAD_URL = "https://ws1.soc.com.br/WSSoc/DownloadArquivosWs"

SOC_EMPRESA_PRINCIPAL = (os.getenv("SOC_EMPRESA") or "").strip()
SOC_CHAVE_EMPRESAS    = (os.getenv("SOC_CHAVE_EMPRESAS") or "").strip()
SOC_CHAVE_GED         = (os.getenv("SOC_CHAVE_GED") or "").strip()
SOC_WS_USUARIO        = (os.getenv("SOC_WS_USUARIO") or "").strip()
SOC_WS_PASSWORD       = (os.getenv("SOC_WS_PASSWORD") or "").strip()

# Códigos da integração SOC. Podem mudar quando o SOC reconfigura o usuário.
CODIGO_EMPRESA_PRINCIPAL = (os.getenv("SOC_CODIGO_EMPRESA_PRINCIPAL") or SOC_EMPRESA_PRINCIPAL or "289501").strip()
CODIGO_RESPONSAVEL       = (os.getenv("SOC_CODIGO_RESPONSAVEL") or "104404").strip()
CODIGO_USUARIO           = (os.getenv("SOC_CODIGO_USUARIO") or "3604573").strip()

# Códigos de exportação
CODIGO_EXPORTA_EMPRESAS = "192392"
CODIGO_EXPORTA_GED      = "191710"
CODIGO_EXPORTA_CONTATOS = "193815"
CODIGO_TIPO_GED_ASO     = "38"

SOC_CHAVE_CONTATOS                 = (os.getenv("SOC_CHAVE_CONTATOS") or "5fc7d830a2f31f0afa69").strip()
SOC_EXPORTA_CONTATOS_USUARIO       = (os.getenv("SOC_EXPORTA_CONTATOS_USUARIO") or CODIGO_USUARIO).strip()
SOC_EXPORTA_CONTATOS_IDENTIFICACAO = (os.getenv("SOC_EXPORTA_CONTATOS_IDENTIFICACAO") or "").strip()
SOC_EXPORTA_CONTATOS_CODIGO_PERFIL = (os.getenv("SOC_EXPORTA_CONTATOS_CODIGO_PERFIL") or "").strip()

# ── Comportamento ──────────────────────────────────────────────────────────────
JANELA_DIAS               = 30
USAR_ONTEM                = False  # Controlado pelo argumento --ontem no CLI
IGNORAR_EMPRESA_PRINCIPAL = False
DELAY_ENTRE_REQUISICOES   = 0.2
DELAY_ENTRE_DOWNLOADS     = 0.2
ENVIO_REAL_EMPRESAS       = (os.getenv("ENVIO_REAL_EMPRESAS") or "false").strip().lower() == "true"

EMPRESAS_PERMITIDAS: set = set({"1338567"})   # Vazio = todas. Ex: {"295569", "334567"}
LIMITE_EMPRESAS           = None   # None = sem limite. Ex: 5

# ── Meta / WhatsApp ────────────────────────────────────────────────────────────
META_WA_TOKEN        = (os.getenv("META_WA_TOKEN") or "").strip()
META_PHONE_NUMBER_ID = (os.getenv("META_PHONE_NUMBER_ID") or "").strip()
META_TEMPLATE_NAME   = (os.getenv("META_TEMPLATE_NAME") or "").strip()
META_NUMERO_TESTE    = (os.getenv("META_NUMERO_TESTE") or "").strip()
META_ENVIAR          = (os.getenv("META_ENVIAR") or "false").strip().lower() == "true"
META_TESTAR_SEM_ASO  = (os.getenv("META_TESTAR_SEM_ASO") or "false").strip().lower() == "true"
META_TIMEOUT         = 60
META_API_VERSION     = "v19.0"

# ── Email ──────────────────────────────────────────────────────────────────────
EMAIL_REMETENTE = (os.getenv("EMAIL_REMETENTE") or "").strip()
EMAIL_SENHA_APP = (os.getenv("EMAIL_SENHA_APP") or "").strip()
EMAIL_DESTINO   = (os.getenv("EMAIL_DESTINO") or "").strip()
EMAIL_ENVIAR    = (os.getenv("EMAIL_ENVIAR") or "false").strip().lower() == "true"
