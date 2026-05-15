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


