import os
import json
import shutil
import argparse
from collections import defaultdict
from datetime import datetime

from config import (
    PASTA_TEMP, PASTA_DEBUG, PASTA_SAIDA_LISTAGEM,
    META_ENVIAR, META_TESTAR_SEM_ASO, META_NUMERO_TESTE,
    ENVIO_REAL_EMPRESAS,
)
from src.utils.helpers import (
    erros_execucao, registrar_erro, obter_data_consulta, normalizar_numero_whatsapp,
)
from src.state.manager import (
    chave_aso, filtrar_nao_enviados, separar_por_assinatura,
)
from src.pipeline.processor import (
    coletar_asos_por_data, salvar_listagem_asos,
    agrupar_por_empresa, baixar_pdfs_empresa,
)
from src.soc.api import (
    buscar_contatos_empresa, extrair_primeiro_numero_contato,
    buscar_asos_empresa, esta_assinado_digitalmente,
)
from src.meta.whatsapp import (
    enviar_pdfs_empresa_meta, enviar_teste_sem_aso_meta, resolver_destino_envio,
)
from src.integrations.email import enviar_email_erros
from src.integrations.supabase import (
    upsert_empresa,
    buscar_chaves_enviadas,
    buscar_pendentes,
    registrar_aso_pendente,
    marcar_aso_enviado,
    registrar_mensagem_outbound,
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


def _processar_grupo_empresas(grupos: dict, chaves_enviadas: set, data_referencia: str) -> list:
    resumo = []
    print(f"\nEmpresas com ASOs prontos: {len(grupos)}")

    for idx, codigo_empresa in enumerate(sorted(grupos.keys()), start=1):
        regs_empresa = grupos[codigo_empresa]
        nome_empresa = regs_empresa[0].get("EMPRESA_NOME", codigo_empresa)

        print(f"\n[{idx}/{len(grupos)}] Empresa {codigo_empresa} - {nome_empresa}")
        print(f"  ASOs: {len(regs_empresa)}")

        resultado = baixar_pdfs_empresa(codigo_empresa, regs_empresa)

        contatos          = buscar_contatos_empresa(codigo_empresa)
        contato_escolhido = extrair_primeiro_numero_contato(contatos)
        numero_empresa    = contato_escolhido["numero"]
        numero_destino    = resolver_destino_envio(numero_empresa)

        if not numero_empresa:
            registrar_erro(f"Empresa {codigo_empresa} sem telefone válido")

        resultado.update({
            "numero_empresa_coletado":  numero_empresa,
            "numero_destino_utilizado": numero_destino,
            "contato_encontrado":       contato_escolhido["contato"],
            "origem_numero":            contato_escolhido["origem"],
            "erro_contato":             None if numero_empresa else "Nenhum telefone válido",
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
                print(f"  [META] {resp_meta['enviados_ok']}/{resp_meta['total']} PDF(s) enviados")

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

                    for r in resp_meta.get("respostas", []):
                        if not r.get("sucesso"):
                            continue
                        try:
                            wamid = (
                                r.get("resposta", {})
                                 .get("messages", [{}])[0]
                                 .get("id", "")
                            )
                        except (IndexError, AttributeError):
                            wamid = ""
                        registrar_mensagem_outbound(
                            codigo_empresa = codigo_empresa,
                            nome_empresa   = nome_empresa,
                            numero         = numero_destino,
                            nome_arquivo   = r.get("arquivo", ""),
                            wamid          = wamid,
                        )

            except Exception as e:
                resultado["meta_erro"] = str(e)
                registrar_erro(f"Empresa {codigo_empresa} erro envio Meta: {e}")

        resumo.append(resultado)

    return resumo


def _reprocessar_pendentes(chaves_enviadas: set) -> list:
    pendentes = buscar_pendentes()
    if not pendentes:
        print("\nNenhum ASO pendente para revisitar.")
        return []

    print(f"\n{'=' * 46}")
    print(f"Revisitando {len(pendentes)} registro(s) pendente(s)...")

    grupos_data = defaultdict(lambda: {"chaves": [], "nome": ""})
    for p in pendentes:
        codigo   = str(p.get("codigo_empresa", "") or "").strip()
        data_iso = str(p.get("data_emissao",   "") or "").strip()
        if not codigo or not data_iso:
            continue
        try:
            d, m, a = data_iso.split("-")[2], data_iso.split("-")[1], data_iso.split("-")[0]
            data_br = f"{d}/{m}/{a}"
        except Exception:
            continue
        grupos_data[(codigo, data_br)]["chaves"].append(p["chave_aso"])
        if not grupos_data[(codigo, data_br)]["nome"]:
            grupos_data[(codigo, data_br)]["nome"] = p.get("nome_empresa", "")

    agora_assinados = []

    for (codigo_empresa, data_emissao), info in grupos_data.items():
        chaves_set   = set(info["chaves"])
        nome_empresa = info["nome"]
        print(f"\n  Empresa {codigo_empresa} ({nome_empresa}) | {data_emissao} | {len(chaves_set)} pendente(s)")

        registros = buscar_asos_empresa(
            codigo_empresa_cliente = codigo_empresa,
            data_inicio            = data_emissao,
            data_fim               = data_emissao,
        )

        novos = [
            r for r in registros
            if chave_aso(r) in chaves_set
            and esta_assinado_digitalmente(r)
            and chave_aso(r) not in chaves_enviadas
        ]

        if not novos:
            print("  -> Ainda pendente, sem mudança.")
            continue

        print(f"  -> {len(novos)} agora assinado(s), adicionando ao processamento.")
        for reg in novos:
            reg.setdefault("EMPRESA_CONSULTADA", codigo_empresa)
            reg.setdefault("EMPRESA_NOME",       nome_empresa)
            reg.setdefault("EMPRESA_CNPJ",        "")
        agora_assinados.extend(novos)

    if not agora_assinados:
        print("\nNenhum pendente passou para assinado nesta execução.")
        return []

    grupos    = agrupar_por_empresa(agora_assinados)
    data_hoje = datetime.now().strftime("%d/%m/%Y")
    return _processar_grupo_empresas(grupos, chaves_enviadas, data_hoje)


def main(usar_ontem: bool = False, data_especifica: str | None = None):
    # ── Prepara diretórios ─────────────────────────────────────────────────────
    if os.path.exists(PASTA_TEMP):
        shutil.rmtree(PASTA_TEMP)
    for pasta in (PASTA_TEMP, PASTA_DEBUG, PASTA_SAIDA_LISTAGEM):
        os.makedirs(pasta, exist_ok=True)

    # ── 1. Busca ASOs já enviados no Supabase ──────────────────────────────────
    chaves_enviadas = buscar_chaves_enviadas()
    print(f"\nASOs já enviados (Supabase): {len(chaves_enviadas)}")

    # ── 2. Consulta ASOs do SOC ────────────────────────────────────────────────
    data_consulta   = obter_data_consulta(usar_ontem, data_especifica)
    registros_todos = coletar_asos_por_data(data_consulta)
    caminho_json    = salvar_listagem_asos(registros_todos, data_consulta)
    print(f"\nListagem salva em: {caminho_json}")
    print(f"Total de registros do SOC: {len(registros_todos)}")

    # ── 3. Filtra os não enviados ──────────────────────────────────────────────
    registros_pendentes = filtrar_nao_enviados(registros_todos, chaves_enviadas)
    print(f"Registros a processar (não enviados): {len(registros_pendentes)}")

    # ── 4. Separa por assinatura digital ───────────────────────────────────────
    assinados, nao_assinados = separar_por_assinatura(registros_pendentes)
    print(f"  Prontos (assinados): {len(assinados)}")
    print(f"  Pendentes (sem assinatura): {len(nao_assinados)}")

    # ── 5. Registra pendentes no Supabase (enviado=False) ──────────────────────
    # A tabela asos_enviados tem FK para empresas, então garante o cadastro primeiro
    empresas_pendentes_vistas = set()
    for reg in nao_assinados:
        cod = str(reg.get("EMPRESA_CONSULTADA", "")).strip()
        if cod not in empresas_pendentes_vistas:
            upsert_empresa(
                codigo   = cod,
                nome     = reg.get("EMPRESA_NOME", ""),
                cnpj     = reg.get("EMPRESA_CNPJ", ""),
                telefone = "",
            )
            empresas_pendentes_vistas.add(cod)
        registrar_aso_pendente(chave_aso(reg), reg)

    # ── 6. Processa assinados por empresa ──────────────────────────────────────
    grupos = agrupar_por_empresa(assinados)
    resumo = _processar_grupo_empresas(grupos, chaves_enviadas, data_consulta)

    # ── Fallback: sem ASOs prontos ─────────────────────────────────────────────
    if not grupos and META_ENVIAR and META_TESTAR_SEM_ASO:
        print("\nNenhum ASO pronto para envio. Enviando mensagem de teste...")
        try:
            enviar_teste_sem_aso_meta(data_consulta)
        except Exception as e:
            registrar_erro(f"Erro ao enviar teste sem ASO: {e}")

    # ── 7. Revisita pendentes de execuções anteriores ─────────────────────────
    resumo += _reprocessar_pendentes(chaves_enviadas)

    # ── 8. Salva resumo e exibe totais ────────────────────────────────────────
    resumo_path = os.path.join(PASTA_SAIDA_LISTAGEM, "resumo_execucao.json")
    with open(resumo_path, "w", encoding="utf-8") as f:
        json.dump(resumo, f, ensure_ascii=False, indent=2, default=str)

    print("\n================ RESUMO FINAL ================")
    print(f"Empresas processadas:          {len(resumo)}")
    print(f"ASOs pendentes (sem assinatura): {len(nao_assinados)}")
    print(f"Resumo salvo em:               {resumo_path}")


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
    finally:
        enviar_email_erros(erros_execucao)
