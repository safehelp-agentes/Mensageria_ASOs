"""
Automação de cadastro de Contatos no SOC via Playwright (CDP).

Este módulo NÃO faz login. Conecta-se a um Chrome já aberto e já logado
no SOC via CDP. Antes de rodar, abra o Chrome uma vez assim:

    chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\\chrome-soc"

Faça login no SOC manualmente, deixe na tela de pesquisa de empresa (337),
e então execute:

    python src/soc/cadastra_contatos.py [arquivo.csv | --sheets]

Fontes de dados:
  --sheets   → lê do Google Sheets (Forms) via service account
  arquivo    → lê de um CSV local
  (sem arg)  → lê de data/cadastro_contatos.csv

Variáveis de ambiente necessárias (.env):
  SOC_CDP_URL              — padrão: http://localhost:9222
  GOOGLE_CREDENTIALS_JSON  — caminho para o JSON da service account (para --sheets)
  GOOGLE_SHEETS_ID         — ID da planilha Google Sheets
  GOOGLE_SHEETS_GID        — GID da aba com respostas do Forms
"""

from __future__ import annotations

import csv
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from playwright.sync_api import Frame, Page, sync_playwright

load_dotenv()

# ── Configuração ───────────────────────────────────────────────────────────────
CDP_URL        = os.getenv("SOC_CDP_URL", "http://localhost:9222")
DIR_EVIDENCIAS = Path(os.getenv("SOC_DIR_EVIDENCIAS", "evidencias_contatos"))

# True  → lê do Google Sheets (Forms)
# False → lê do CSV local
USAR_SHEETS = False

GOOGLE_CREDENTIALS_JSON = os.getenv("GOOGLE_CREDENTIALS_JSON", "")
GOOGLE_SHEETS_ID        = os.getenv("GOOGLE_SHEETS_ID", "")
GOOGLE_SHEETS_GID       = int(os.getenv("GOOGLE_SHEETS_GID", "0"))

TIMEOUT_PADRAO = 30_000

_PROJECT_ROOT = Path(__file__).parent.parent.parent


logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("cadastra_contatos")


# ── Modelos de dados ───────────────────────────────────────────────────────────

@dataclass
class Contato:
    """Campos da tela cad149i do SOC (IDs confirmados inspecionando o formulário real)."""
    nome: str            # #nomeContato
    telefone1: str = ""  # #tel1
    email1: str = ""     # #email1


@dataclass
class EmpresaContatos:
    cnpj: str
    nome_empresa: str = ""
    contatos: list[Contato] = field(default_factory=list)


@dataclass
class ResultadoCadastro:
    cnpj: str
    nome_empresa: str = ""
    sucesso: bool = False
    mensagem: str = ""
    evidencia: Optional[str] = None


# ── Colunas da planilha / CSV ──────────────────────────────────────────────────
COL_CNPJ              = "CNPJ/CPF"
COL_NOME              = "Nome Empresa"
COL_DESEJA_ASO        = "A empresa deseja receber os ASOs diariamente"
COL_TELEFONE          = "Qual o numero de telefone do responsavel"
COL_NOME_RESPONSAVEL  = "Qual o nome do responsavel"
COL_EMAIL_RESPONSAVEL = "Qual o Email do responsavel"


# ── Leitura de dados ───────────────────────────────────────────────────────────

def _normalizar_cnpj(valor: str) -> str:
    return re.sub(r"\D", "", valor or "")


def _cnpj_valido(cnpj: str) -> bool:
    return len(cnpj) in (11, 14)


def _coluna(linha: dict, nome_alvo: str) -> str:
    """Busca coluna por prefixo (20 chars), tolerando variações do Forms."""
    alvo = nome_alvo.lower().strip()
    for chave, valor in linha.items():
        chave_n = (chave or "").lower().strip()
        if chave_n.startswith(alvo[:20]) or alvo.startswith(chave_n[:20]):
            return ("" if valor is None else str(valor)).strip()
    return ""


def _processar_linhas(linhas: list[dict], origem: str) -> list[EmpresaContatos]:
    por_cnpj: dict[str, EmpresaContatos] = {}
    for i, linha in enumerate(linhas, start=2):
        if _coluna(linha, COL_DESEJA_ASO).lower() != "sim":
            continue
        cnpj = _normalizar_cnpj(_coluna(linha, COL_CNPJ))
        if not _cnpj_valido(cnpj):
            logger.warning("%s linha %d: CNPJ inválido '%s'. Pulando.", origem, i, _coluna(linha, COL_CNPJ))
            continue
        nome        = _coluna(linha, COL_NOME)
        telefone    = _coluna(linha, COL_TELEFONE)
        responsavel = _coluna(linha, COL_NOME_RESPONSAVEL)
        email       = _coluna(linha, COL_EMAIL_RESPONSAVEL)
        nome_contato = f"ASO - {responsavel}" if responsavel else f"ASO - {nome}"
        if cnpj not in por_cnpj:
            por_cnpj[cnpj] = EmpresaContatos(cnpj=cnpj, nome_empresa=nome)
        por_cnpj[cnpj].contatos.append(Contato(nome=nome_contato, telefone1=telefone, email1=email))
    logger.info("%s: %d empresa(s) válida(s) carregada(s).", origem, len(por_cnpj))
    return list(por_cnpj.values())


def ler_csv(caminho: str) -> list[EmpresaContatos]:
    with open(caminho, newline="", encoding="utf-8-sig") as f:
        return _processar_linhas(list(csv.DictReader(f)), f"CSV({caminho})")


def ler_google_sheets() -> list[EmpresaContatos]:
    import gspread

    if not GOOGLE_CREDENTIALS_JSON:
        raise RuntimeError("GOOGLE_CREDENTIALS_JSON não definido no .env.")
    if not GOOGLE_SHEETS_ID:
        raise RuntimeError("GOOGLE_SHEETS_ID não definido no .env.")

    path = Path(GOOGLE_CREDENTIALS_JSON)
    if not path.is_absolute():
        path = _PROJECT_ROOT / GOOGLE_CREDENTIALS_JSON

    logger.info("Conectando ao Google Sheets (id=%s gid=%d)...", GOOGLE_SHEETS_ID, GOOGLE_SHEETS_GID)
    gc     = gspread.service_account(filename=str(path))
    aba    = gc.open_by_key(GOOGLE_SHEETS_ID).get_worksheet_by_id(GOOGLE_SHEETS_GID)
    linhas = aba.get_all_records()
    logger.info("Google Sheets: %d linha(s) carregada(s).", len(linhas))
    return _processar_linhas(linhas, "Google Sheets")


# ── Navegação SOC (Playwright) ─────────────────────────────────────────────────

def _frame_cadastro(page: Page) -> Frame:
    socframe = page.frame(name="socframe")
    if socframe is None:
        raise RuntimeError("iframe 'socframe' não encontrado — SOC não está aberto.")
    for child in socframe.child_frames:
        url = (child.url or "").lower()
        if "cad" in url or "websoc" in url:
            return child
    return socframe


def _formatar_cnpj(cnpj: str) -> str:
    d = _normalizar_cnpj(cnpj)
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return d


def _ir_para_tela_337(page: Page, ultimo_status: str | None = None) -> Frame:
    """
    Garante que o frame está na tela de pesquisa de empresa (337).
    Sequência varia conforme estado após o último contato:
      "ja_existe"  → volta → browse          (já na lista de contatos)
      "cadastrado" → browse → volta → browse  (em visualização do contato salvo)
    """
    frame = _frame_cadastro(page)
    if frame.locator("#nomeSeach").is_visible():
        return frame

    logger.info("Voltando para tela de pesquisa (último status: %s)...", ultimo_status)

    if ultimo_status == "ja_existe":
        frame.click("a[href=\"javascript:doAcao('volta');\"]")
        page.wait_for_timeout(1500)
        frame = _frame_cadastro(page)
        frame.click("a[href=\"javascript:doAcao('browse');\"]")
        page.wait_for_timeout(1500)
    else:
        frame.click("a[href=\"javascript:doAcao('browse');\"]")
        page.wait_for_timeout(1500)
        frame = _frame_cadastro(page)
        frame.click("a[href=\"javascript:doAcao('volta');\"]")
        page.wait_for_timeout(1500)
        frame = _frame_cadastro(page)
        frame.click("a[href=\"javascript:doAcao('browse');\"]")
        page.wait_for_timeout(1500)

    frame = _frame_cadastro(page)
    frame.wait_for_selector("#nomeSeach", state="visible", timeout=TIMEOUT_PADRAO)
    logger.info("Tela de pesquisa pronta.")
    return frame


def _navegar_para_contatos(page: Page, cnpj: str, ultimo_status: str | None = None) -> Frame:
    frame = _ir_para_tela_337(page, ultimo_status)

    logger.info("Buscando empresa CNPJ %s...", _formatar_cnpj(cnpj))
    frame.fill("#nomeSeach", _formatar_cnpj(cnpj))
    frame.click("img[name='botao-pesquisar-padrao-soc']")
    frame.wait_for_selector("#tableBrowserId tr.cor1", state="visible", timeout=TIMEOUT_PADRAO)

    frame.locator("#tableBrowserId tr.cor1 td.codigo").first.click()
    frame.wait_for_selector("img[tooltype='Contatos']", state="visible", timeout=TIMEOUT_PADRAO)

    frame.click("img[tooltype='Contatos']")
    frame.wait_for_selector("a[href=\"javascript:doAcao('inc');\"]", state="visible", timeout=TIMEOUT_PADRAO)

    return _frame_cadastro(page)


def _preencher_contato(page: Page, frame: Frame, contato: Contato) -> str:
    """Preenche e salva um contato. Retorna 'cadastrado' ou 'ja_existe'."""
    logger.info("[contato] Abrindo formulário para '%s'...", contato.nome)
    frame.click("a[href=\"javascript:doAcao('inc');\"]")
    frame.wait_for_selector("#nomeContato", timeout=TIMEOUT_PADRAO)

    frame.fill("#nomeContato", contato.nome)
    frame.fill("#tel1", contato.telefone1)
    frame.fill("#email1", contato.email1)

    _msgs: list[str] = []

    def _on_dialog(dialog) -> None:
        _msgs.append(dialog.message)
        dialog.accept()

    page.on("dialog", _on_dialog)
    logger.info("[contato] Salvando...")
    frame.click("a[href=\"javascript:doAcao('save');\"]")
    page.wait_for_timeout(1500)
    page.remove_listener("dialog", _on_dialog)

    if _msgs:
        msg = _msgs[0]
        if "já cadastrado" in msg.lower():
            logger.warning("[contato] '%s' já existe nesta empresa. Pulando.", contato.nome)
            frame = _frame_cadastro(page)
            frame.click("a[href=\"javascript:doAcao('can');\"]")
            page.wait_for_timeout(1000)
            frame = _frame_cadastro(page)
            frame.wait_for_selector("a[href=\"javascript:doAcao('inc');\"]", state="visible", timeout=TIMEOUT_PADRAO)
            return "ja_existe"
        raise RuntimeError(f"Popup inesperado do SOC ao salvar contato: {msg}")

    page.wait_for_timeout(2000)
    frame = _frame_cadastro(page)
    frame.wait_for_selector("a[href=\"javascript:doAcao('inc');\"]", state="visible", timeout=TIMEOUT_PADRAO)
    logger.info("[contato] '%s' cadastrado com sucesso.", contato.nome)
    return "cadastrado"


# ── Orquestração ───────────────────────────────────────────────────────────────

def cadastrar_contatos(lista_empresas: list[EmpresaContatos]) -> list[ResultadoCadastro]:
    """Cadastra contatos sequencialmente. Falha em uma empresa não interrompe o lote."""
    DIR_EVIDENCIAS.mkdir(parents=True, exist_ok=True)
    resultados: list[ResultadoCadastro] = []

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(CDP_URL)
        context = browser.contexts[0]
        context.set_default_timeout(TIMEOUT_PADRAO)
        page = context.pages[0] if context.pages else context.new_page()

        ultimo_status: str | None = None
        for empresa in lista_empresas:
            try:
                frame = _navegar_para_contatos(page, empresa.cnpj, ultimo_status)
                cadastrados = pulados = 0
                for contato in empresa.contatos:
                    ultimo_status = _preencher_contato(page, frame, contato)
                    if ultimo_status == "ja_existe":
                        pulados += 1
                    else:
                        cadastrados += 1
                        logger.info("Contato '%s' cadastrado — %s (CNPJ %s)",
                                    contato.nome, empresa.nome_empresa, empresa.cnpj)

                partes = []
                if cadastrados:
                    partes.append(f"{cadastrados} cadastrado(s)")
                if pulados:
                    partes.append(f"{pulados} já existia(m)")
                resultados.append(ResultadoCadastro(
                    cnpj=empresa.cnpj,
                    nome_empresa=empresa.nome_empresa,
                    sucesso=True,
                    mensagem=", ".join(partes) or "nenhum processado",
                ))

            except Exception as exc:
                evidencia = DIR_EVIDENCIAS / f"erro_{empresa.cnpj}.png"
                try:
                    page.screenshot(path=str(evidencia), full_page=True)
                except Exception:
                    evidencia = None
                logger.error("Falha — %s (CNPJ %s): %s", empresa.nome_empresa, empresa.cnpj, exc)
                resultados.append(ResultadoCadastro(
                    cnpj=empresa.cnpj,
                    nome_empresa=empresa.nome_empresa,
                    sucesso=False,
                    mensagem=str(exc),
                    evidencia=str(evidencia) if evidencia else None,
                ))

        # connect_over_cdp: close() encerra só a conexão CDP, não o Chrome.
        browser.close()

    return resultados


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    usar_sheets = USAR_SHEETS or arg == "--sheets"

    if usar_sheets:
        try:
            empresas = ler_google_sheets()
        except Exception as e:
            print(f"[ERRO] Google Sheets: {e}")
            sys.exit(1)
    else:
        _default = _PROJECT_ROOT / "data" / "cadastro_contatos.csv"
        caminho = arg if arg and arg != "--sheets" else str(_default)
        if not Path(caminho).exists():
            print(f"[ERRO] Arquivo não encontrado: {caminho}")
            print("Uso: python src/soc/cadastra_contatos.py [arquivo.csv | --sheets]")
            sys.exit(1)
        empresas = ler_csv(caminho)

    if not empresas:
        logger.info("Nenhuma empresa válida para cadastrar. Encerrando.")
        sys.exit(0)

    for r in cadastrar_contatos(empresas):
        s = "OK" if r.sucesso else "FALHA"
        logger.info("[%s] %s (CNPJ %s) — %s", s, r.nome_empresa, r.cnpj, r.mensagem)
