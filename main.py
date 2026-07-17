import os
import sys
import json
import shutil
import argparse
from datetime import datetime, timedelta

from config import (
    PASTA_TEMP, PASTA_DEBUG, PASTA_SAIDA_LISTAGEM,
    META_ENVIAR, META_TESTAR_SEM_ASO, META_NUMERO_TESTE,
    ENVIO_REAL_EMPRESAS, JANELA_DIAS,
)
from src.utils.helpers import (
    registrar_erro, obter_data_consulta, normalizar_numero_whatsapp,
    numero_parece_valido,
)
from src.state.manager import (
    chave_aso, filtrar_nao_enviados,
)
from src.pipeline.processor import (
    coletar_asos_por_data, salvar_listagem_asos,
    agrupar_por_empresa, baixar_pdfs_empresa,
)
from src.soc.api import (
    buscar_contatos_empresa, extrair_primeiro_numero_contato,
)
from src.meta.whatsapp import (
    enviar_pdfs_empresa_meta, enviar_teste_sem_aso_meta, resolver_destino_envio,
)
from src.integrations.supabase import (
    upsert_empresa,
    buscar_chaves_enviadas,
    marcar_aso_enviado,
    salvar_aso_pendente,
    buscar_asos_pendentes,
    buscar_dados_empresas,
    verificar_conectividade,
)


def _validar_numero_destino(numero_destino: str, codigo_empresa: str):
    """Aborta se tentarmos enviar para número real com ENVIO_REAL_EMPRESAS=False."""
    if ENVIO_REAL_EMPRESAS:
        return
    numero_teste = normalizar_numero_whatsapp(META_NUMERO_TESTE)
    if numero_destino != numero_teste:
        raise RuntimeError(
            f"BLOQUEIO DE SEGURANÇA: empresa {codigo_empresa} usaria número real "
            f"({numero_destino}) mas ENVIO_REAL_EMPRESAS=False. Envio cancelado."
        )


def _processar_grupo_empresas(grupos: dict, data_referencia: str, config_empresas: dict = None) -> list:
    config_empresas = config_empresas or {}
    resumo = []
    print(f"\nEmpresas com ASOs a processar: {len(grupos)}")

    for idx, codigo_empresa in enumerate(sorted(grupos.keys()), start=1):
        regs_empresa = grupos[codigo_empresa]
        nome_empresa = regs_empresa[0].get("EMPRESA_NOME", codigo_empresa)

        print(f"\n[{idx}/{len(grupos)}] Empresa {codigo_empresa} - {nome_empresa}")
        print(f"  ASOs: {len(regs_empresa)}")

        # ── Resolve contato ANTES de baixar PDFs ──────────────────────────────
        # Só processa empresas que tenham um contato com nome "ASO -" cadastrado.
        contatos          = buscar_contatos_empresa(codigo_empresa)
        contato_escolhido = extrair_primeiro_numero_contato(contatos)
        numero_empresa    = contato_escolhido["numero"]

        cfg_emp            = config_empresas.get(codigo_empresa, {})
        telefone_escolhido = cfg_emp.get("telefone_escolhido", "") or ""
        if telefone_escolhido:
            if numero_parece_valido(telefone_escolhido):
                numero_empresa = normalizar_numero_whatsapp(telefone_escolhido)
                print(f"  Usando telefone escolhido (CRM): {numero_empresa}")
            else:
                registrar_erro(f"Empresa {codigo_empresa}: telefone_escolhido '{telefone_escolhido}' inválido")
                print(f"  AVISO: telefone escolhido inválido ({telefone_escolhido})")
                numero_empresa = ""

        if not numero_empresa:
            print(f"  AVISO: nenhum contato com número válido cadastrado. Empresa ignorada.")
            registrar_erro(f"Empresa {codigo_empresa} sem contato com número válido — ignorada pelo pipeline")
            resumo.append({
                "empresa": codigo_empresa, "nome_empresa": nome_empresa,
                "downloads_ok": 0, "erros": 0, "meta_enviado": False,
                "meta_erro": "Sem contato ASO - válido",
            })
            continue

        resultado = baixar_pdfs_empresa(codigo_empresa, regs_empresa)

        numero_destino = resolver_destino_envio(numero_empresa)

        resultado.update({
            "numero_empresa_coletado":  numero_empresa,
            "numero_destino_utilizado": numero_destino,
            "contato_encontrado":       contato_escolhido["contato"],
            "origem_numero":            contato_escolhido["origem"],
            "erro_contato":             None,
        })

        upsert_empresa(
            codigo   = codigo_empresa,
            nome     = nome_empresa,
            cnpj     = regs_empresa[0].get("EMPRESA_CNPJ", ""),
            telefone = numero_empresa,
        )

        print(f"  Downloads OK: {resultado['downloads_ok']} | Erros: {resultado['erros']}")
        print(f"  Número destino: {numero_destino}")

        if META_ENVIAR and resultado["downloads_ok"] > 0:
            try:
                _validar_numero_destino(numero_destino, codigo_empresa)
                resp_meta = enviar_pdfs_empresa_meta(resultado, numero_destino)
                resultado.update({
                    "meta_enviado":       True,
                    "meta_enviados_ok":   resp_meta["enviados_ok"],
                    "meta_enviados_erro": resp_meta["enviados_erro"],
                    "meta_resposta":      resp_meta,
                })
                print(f"  [META] {resp_meta.get('asos_incluidos', 0)} ASO(s) em {resp_meta['enviados_ok']}/{resp_meta['total']} mensagem(ns)")

                if resp_meta["enviados_ok"] > 0:
                    chaves_com_erro_dl = {chave_aso(r) for r in resultado["registros_com_erro"]}
                    data_emissao       = resultado.get("data_emissao", "")

                    for reg in regs_empresa:
                        chave = chave_aso(reg)
                        if chave not in chaves_com_erro_dl:
                            marcar_aso_enviado(
                                chave          = chave,
                                codigo_empresa = codigo_empresa,
                                nome_empresa   = nome_empresa,
                                data_envio     = data_referencia,
                                data_emissao   = data_emissao,
                                numero_destino = numero_destino,
                            )

            except Exception as e:
                resultado["meta_erro"] = str(e)
                registrar_erro(f"Empresa {codigo_empresa} erro envio Meta: {e}")
                for reg in regs_empresa:
                    salvar_aso_pendente(
                        chave          = chave_aso(reg),
                        codigo_empresa = codigo_empresa,
                        nome_empresa   = nome_empresa,
                        data_emissao   = resultado.get("data_emissao", ""),
                        numero_destino = numero_destino,
                    )

        for reg in resultado.get("registros_com_erro", []):
            salvar_aso_pendente(
                chave          = chave_aso(reg),
                codigo_empresa = codigo_empresa,
                nome_empresa   = nome_empresa,
                data_emissao   = resultado.get("data_emissao", ""),
                numero_destino = numero_destino,
            )

        resumo.append(resultado)

    return resumo


def main(usar_ontem: bool = False, data_especifica: str | None = None):
    # ── Verifica conectividade com o Supabase antes de qualquer operação ───────
    print("Verificando conexão com Supabase...")
    supabase_ok, supabase_msg = verificar_conectividade()
    if not supabase_ok:
        msg_erro = (
            f"⚠️ Automação ASOs — SafeWork\n"
            f"ERRO DE EXECUÇÃO: Não foi possível conectar ao Supabase.\n"
            f"Detalhe: {supabase_msg}\n"
            f"O script foi encerrado. Verifique a conexão e as credenciais."
        )
        print(f"\n[SUPABASE] Falha na verificação de conectividade: {supabase_msg}")
        print("Enviando alerta para o número de teste e encerrando...")
        try:
            from src.meta.whatsapp import enviar_texto_meta
            enviar_texto_meta(META_NUMERO_TESTE, msg_erro)
            print("[META] Alerta enviado com sucesso.")
        except Exception as e:
            print(f"[META] Não foi possível enviar o alerta: {e}")
        sys.exit(1)

    print("[SUPABASE] Conexão OK.\n")

    # ── Prepara diretórios ─────────────────────────────────────────────────────
    if os.path.exists(PASTA_TEMP):
        shutil.rmtree(PASTA_TEMP)
    for pasta in (PASTA_TEMP, PASTA_DEBUG, PASTA_SAIDA_LISTAGEM):
        os.makedirs(pasta, exist_ok=True)

    # ── 1. Busca ASOs já enviados, pendentes e dados de empresas no Supabase ───
    chaves_enviadas  = buscar_chaves_enviadas()
    pendentes        = buscar_asos_pendentes()
    config_empresas  = buscar_dados_empresas()
    bloqueadas       = {cod for cod, c in config_empresas.items() if c.get("bloqueada")}
    print(f"\nASOs já enviados (Supabase):  {len(chaves_enviadas)}")
    print(f"ASOs pendentes (não enviados): {len(pendentes)}")
    print(f"Empresas bloqueadas:           {len(bloqueadas)}")

    # ── 2. Consulta ASOs do SOC (janela de JANELA_DIAS antes da data de referência)
    data_fim    = obter_data_consulta(usar_ontem, data_especifica)
    dt_fim      = datetime.strptime(data_fim, "%d/%m/%Y")
    data_inicio = (dt_fim - timedelta(days=JANELA_DIAS)).strftime("%d/%m/%Y")

    registros_todos = coletar_asos_por_data(data_inicio, data_fim, bloqueadas, config_empresas)
    caminho_json    = salvar_listagem_asos(registros_todos, data_fim)
    print(f"\nListagem salva em: {caminho_json}")
    print(f"Total de registros do SOC: {len(registros_todos)}")

    # ── 3. Filtra os não enviados ──────────────────────────────────────────────
    registros_a_processar = filtrar_nao_enviados(registros_todos, chaves_enviadas)
    print(f"Registros a processar (não enviados): {len(registros_a_processar)}")

    # ── 4. Processa empresas ───────────────────────────────────────────────────
    grupos = agrupar_por_empresa(registros_a_processar)
    resumo = _processar_grupo_empresas(grupos, data_fim, config_empresas)

    # ── Fallback: sem ASOs ─────────────────────────────────────────────────────
    if not grupos and META_ENVIAR and META_TESTAR_SEM_ASO:
        print("\nNenhum ASO encontrado. Enviando mensagem de teste...")
        try:
            enviar_teste_sem_aso_meta(data_fim)
        except Exception as e:
            registrar_erro(f"Erro ao enviar teste sem ASO: {e}")

    # ── 5. Salva resumo e exibe totais ─────────────────────────────────────────
    resumo_path = os.path.join(PASTA_SAIDA_LISTAGEM, "resumo_execucao.json")
    with open(resumo_path, "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2, default=str)

    print("\n================ RESUMO FINAL ================")
    print(f"Empresas processadas: {len(resumo)}")
    print(f"Resumo salvo em:      {resumo_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--ontem", action="store_true", help="Consulta data de ontem (d-1)")
    parser.add_argument("--data", metavar="DD/MM/AAAA", help="Consulta uma data específica (ex: 09/05/2026)")
    args = parser.parse_args()

    if args.ontem and args.data:
        parser.error("Use apenas --ontem ou --data, não os dois ao mesmo tempo.")

    try:
        main(usar_ontem=args.ontem, data_especifica=args.data)
    except Exception as e:
        registrar_erro(f"Erro geral na execução: {e}")
        raise
