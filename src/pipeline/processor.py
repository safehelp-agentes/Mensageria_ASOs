import os
import io
import json
import time
import zipfile
from collections import defaultdict

from config import (
    PASTA_TEMP, PASTA_SAIDA_LISTAGEM,
    DELAY_ENTRE_REQUISICOES, DELAY_ENTRE_DOWNLOADS,
)
from src.utils.helpers import sanitizar_nome, registrar_erro
from src.soc.api import buscar_empresas, buscar_asos_empresa, buscar_contatos_empresa, extrair_primeiro_numero_contato
from src.soc.downloader import baixar_documento
from src.integrations.supabase import buscar_dados_empresas, upsert_empresa


def _montar_nome_arquivo_saida(nome_base: str, tipo: str) -> str:
    nome_base = sanitizar_nome(os.path.splitext(nome_base)[0])
    return f"{nome_base}.{tipo}" if tipo in ("pdf", "zip") else f"{nome_base}.bin"


def _sincronizar_empresas_completo(empresas_soc: list, dados_supabase: dict) -> None:
    """
    Sincroniza todas as empresas do SOC com o Supabase.
    - nome/cnpj: atualiza se diferente do registrado
    - telefone: busca no SOC apenas quando o Supabase não tem valor (evita chamadas desnecessárias)
    Não toca em telefone_escolhido nem bloqueada — esses campos são exclusivos do CRM.
    """
    atualizadas = sem_tel_resolvidas = 0
    total = len(empresas_soc)
    print(f"[SUPABASE] Sincronizando {total} empresa(s) com o SOC...")

    for idx, emp in enumerate(empresas_soc, start=1):
        if idx % 25 == 0 or idx == total:
            print(f"  [sync {idx}/{total}] atualizadas: {atualizadas} | telefones preenchidos: {sem_tel_resolvidas}")

        codigo = str(emp.get("CODIGO", "")).strip()
        if not codigo:
            continue

        nome_soc = (emp.get("RAZAOSOCIAL") or emp.get("NOMEABREVIADO") or "").strip()
        cnpj_soc = str(emp.get("CNPJ", "")).strip()

        dados    = dados_supabase.get(codigo, {})
        nome_db  = dados.get("nome", "")
        cnpj_db  = dados.get("cnpj", "")
        tel_db   = dados.get("telefone", "")

        telefone_novo = tel_db
        if not tel_db:
            try:
                contatos  = buscar_contatos_empresa(codigo)
                tel_soc   = extrair_primeiro_numero_contato(contatos).get("numero", "")
                if tel_soc:
                    telefone_novo = tel_soc
                    sem_tel_resolvidas += 1
            except Exception as e:
                registrar_erro(f"Erro ao buscar contato empresa {codigo}: {e}")

        if nome_soc != nome_db or cnpj_soc != cnpj_db or telefone_novo != tel_db:
            upsert_empresa(codigo=codigo, nome=nome_soc, cnpj=cnpj_soc, telefone=telefone_novo)
            atualizadas += 1

    print(f"[SUPABASE] Empresas verificadas: {len(empresas_soc)} | Atualizadas: {atualizadas} | Telefones preenchidos: {sem_tel_resolvidas}")


def coletar_asos_por_data(data_inicio: str, data_fim: str, empresas_bloqueadas: set = None, dados_supabase: dict = None) -> list:
    """Busca ASOs de todas as empresas no intervalo [data_inicio, data_fim] (DD/MM/YYYY).
    Empresas em empresas_bloqueadas são ignoradas antes de qualquer consulta ao SOC.
    dados_supabase: dict retornado por buscar_dados_empresas(); se None, é buscado internamente.
    """
    empresas_bloqueadas = empresas_bloqueadas or set()

    empresas = buscar_empresas()

    if dados_supabase is None:
        dados_supabase = buscar_dados_empresas()

    _sincronizar_empresas_completo(empresas, dados_supabase)

    empresas_ativas = [
        emp for emp in empresas
        if str(emp.get("CODIGO", "")).strip() not in empresas_bloqueadas
    ]

    ignoradas = len(empresas) - len(empresas_ativas)
    print(f"Total de empresas no SOC: {len(empresas)}")
    if ignoradas:
        print(f"Empresas bloqueadas (sem consulta ao SOC): {ignoradas}")
    print(f"Empresas a consultar: {len(empresas_ativas)}")
    print(f"Período consultado: {data_inicio} → {data_fim}")

    resultados = []

    for i, emp in enumerate(empresas_ativas, start=1):
        codigo_empresa = str(emp.get("CODIGO", "")).strip()
        nome_empresa   = (emp.get("RAZAOSOCIAL") or emp.get("NOMEABREVIADO") or "").strip()

        print(f"[{i}/{len(empresas_ativas)}] Empresa {codigo_empresa} - {nome_empresa}")

        registros = buscar_asos_empresa(
            codigo_empresa_cliente=codigo_empresa,
            data_inicio=data_inicio,
            data_fim=data_fim,
        )

        for reg in registros:
            resultados.append({
                "EMPRESA_CONSULTADA": codigo_empresa,
                "EMPRESA_NOME":       nome_empresa,
                "EMPRESA_CNPJ":       str(emp.get("CNPJ", "")).strip(),
                **reg,
            })

        if not registros:
            print("    -> 0 registros")

        time.sleep(DELAY_ENTRE_REQUISICOES)

    return resultados


def salvar_listagem_asos(registros: list, data_consulta: str) -> str:
    os.makedirs(PASTA_SAIDA_LISTAGEM, exist_ok=True)
    caminho = os.path.join(PASTA_SAIDA_LISTAGEM, f"asos_{data_consulta.replace('/', '-')}.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(registros, f, ensure_ascii=False, indent=2)
    return caminho


def agrupar_por_empresa(registros: list) -> dict:
    grupos = defaultdict(list)
    for reg in registros:
        empresa = str(reg.get("EMPRESA_CONSULTADA", "")).strip()
        if empresa:
            grupos[empresa].append(reg)
    return grupos


def baixar_pdfs_empresa(codigo_empresa: str, registros_empresa: list) -> dict | None:
    """Baixa todos os PDFs de uma empresa e salva em pasta temporária."""
    if not registros_empresa:
        return None

    nome_empresa   = sanitizar_nome(registros_empresa[0].get("EMPRESA_NOME", codigo_empresa))
    dt_emissao_raw = registros_empresa[0].get("DT_EMISSAO", "")
    data_ref       = sanitizar_nome(dt_emissao_raw).replace("/", "-")
    data_emissao   = dt_emissao_raw or "sem data"
    pasta_empresa  = os.path.join(PASTA_TEMP, f"{codigo_empresa} - {nome_empresa}")
    os.makedirs(pasta_empresa, exist_ok=True)

    downloads_ok       = 0
    erros              = 0
    vistos             = set()
    nomes_salvos       = set()
    registros_com_erro = []

    for i, reg in enumerate(registros_empresa, start=1):
        cd_empresa = str(reg.get("CD_EMPRESA", "")).strip()
        cd_ged     = str(reg.get("CD_GED", "")).strip()
        cd_arquivo = str(reg.get("CD_ARQUIVO_GED", "")).strip()
        nome_base  = reg.get("NM_ARQUIVOS_GED", cd_arquivo)

        chave_unica = (cd_empresa, cd_ged, cd_arquivo)
        if chave_unica in vistos:
            continue
        vistos.add(chave_unica)

        print(f"    [{i}/{len(registros_empresa)}] baixando {nome_base}")

        try:
            payload, tipo, _ = baixar_documento(cd_empresa, cd_ged, cd_arquivo)

            if tipo == "zip":
                with zipfile.ZipFile(io.BytesIO(payload)) as zf:
                    pdfs_no_zip = [n for n in zf.namelist() if n.lower().endswith(".pdf")]
                    if not pdfs_no_zip:
                        raise RuntimeError("ZIP recebido do SOC não contém nenhum PDF.")
                    for nome_pdf_zip in pdfs_no_zip:
                        pdf_payload   = zf.read(nome_pdf_zip)
                        nome_saida    = sanitizar_nome(os.path.splitext(os.path.basename(nome_pdf_zip) or nome_base)[0]) + ".pdf"
                        nome_original = nome_saida
                        contador      = 1
                        while nome_saida.lower() in nomes_salvos:
                            stem, ext  = os.path.splitext(nome_original)
                            nome_saida = f"{stem} ({contador}){ext}"
                            contador  += 1
                        with open(os.path.join(pasta_empresa, nome_saida), "wb") as f:
                            f.write(pdf_payload)
                        nomes_salvos.add(nome_saida.lower())
                        print(f"      [ZIP→PDF] Extraído: {nome_saida}")
                downloads_ok += 1

            elif tipo == "pdf":
                nome_saida    = _montar_nome_arquivo_saida(nome_base, tipo)
                nome_original = nome_saida
                contador      = 1
                while nome_saida.lower() in nomes_salvos:
                    stem, ext  = os.path.splitext(nome_original)
                    nome_saida = f"{stem} ({contador}){ext}"
                    contador  += 1
                with open(os.path.join(pasta_empresa, nome_saida), "wb") as f:
                    f.write(payload)
                nomes_salvos.add(nome_saida.lower())
                downloads_ok += 1

            else:
                raise RuntimeError(f"Formato desconhecido (tipo={tipo}), ignorado.")

        except Exception as e:
            erros += 1
            registrar_erro(f"Erro download GED empresa {cd_empresa} arquivo {cd_arquivo}: {e}")
            print(f"      ERRO: {e}")
            registros_com_erro.append(reg)

        time.sleep(DELAY_ENTRE_DOWNLOADS)

    return {
        "empresa":            codigo_empresa,
        "nome_empresa":       nome_empresa,
        "data":               data_ref,
        "data_emissao":       data_emissao,
        "total_registros":    len(registros_empresa),
        "downloads_ok":       downloads_ok,
        "erros":              erros,
        "pasta_pdfs":         pasta_empresa,
        "registros_empresa":  registros_empresa,
        "registros_com_erro": registros_com_erro,
        "meta_enviado":       False,
        "meta_enviados_ok":   0,
        "meta_enviados_erro": 0,
        "meta_erro":          None,
        "meta_resposta":      None,
    }

