from src.soc.api import esta_assinado_digitalmente


def chave_aso(reg: dict) -> str:
    return (
        f"{str(reg.get('CD_EMPRESA', '')).strip()}"
        f"|{str(reg.get('CD_GED', '')).strip()}"
        f"|{str(reg.get('CD_ARQUIVO_GED', '')).strip()}"
    )


def filtrar_nao_enviados(registros: list, chaves_enviadas: set) -> list:
    """Remove registros já marcados como enviados no Supabase e elimina duplicatas locais."""
    vistos    = set()
    resultado = []
    for reg in registros:
        chave = chave_aso(reg)
        if chave in vistos or chave in chaves_enviadas:
            continue
        vistos.add(chave)
        resultado.append(reg)
    return resultado


def separar_por_assinatura(registros: list) -> tuple:
    """Retorna (assinados, nao_assinados)."""
    assinados     = [r for r in registros if     esta_assinado_digitalmente(r)]
    nao_assinados = [r for r in registros if not esta_assinado_digitalmente(r)]
    return assinados, nao_assinados
