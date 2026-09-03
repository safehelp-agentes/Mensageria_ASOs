# CLAUDE.md — SafeWork · Envio de ASOs

> Contexto para o Claude Code e para quem for continuar o desenvolvimento.
> Produto e como rodar em detalhe: [README.md](README.md) · arquitetura interna: [ARCHITECTURE.md](ARCHITECTURE.md) · deploy e operação: [OPERACAO.md](OPERACAO.md) · segurança/LGPD: [SECURITY.md](SECURITY.md).

## O que é

Automação que entrega **ASOs** (Atestados de Saúde Ocupacional) para o RH das empresas clientes da **GPS SafeWork** via **WhatsApp Business**. É **produção real, com empresas e números reais** — toda mudança precisa de cuidado.

Três módulos independentes:

1. **Pipeline (send-only)** — `main.py` + `src/`. Roda em lote (systemd timer na VPS): busca ASOs novos no SOC, baixa os PDFs, une por empresa e envia por WhatsApp. **Só envia; nunca lê respostas.** Persiste o estado de envio no Supabase (`asos_enviados`).
2. **Inbox / CRM (read-mostly)** — `inbox/`. Serviço FastAPI **separado**: webhook da Meta que grava as respostas dos clientes em `mensagens` + SPA de 3 abas (Dashboard, Conversas, ASOs). Não realimenta o pipeline. Ver [ARCHITECTURE.md §10](ARCHITECTURE.md#10-crm--recepção-e-visualização-inbox-de-asos).
3. **Cadastro de contatos (manual, local)** — `src/soc/cadastra_contatos.py`. Playwright/CDP que cadastra contatos na UI web do SOC. **Não roda na VPS** (precisa de um Chrome local logado no SOC).

## Invariantes de segurança — NUNCA quebrar

Estas travas existem porque o sistema manda documentos de saúde para números reais:

1. **Dupla trava de envio real.** `ENVIO_REAL_EMPRESAS=false` por padrão **e** o bloqueio explícito em `main.py:_validar_numero_destino()`, que aborta se for mandar para qualquer número que não seja o `META_NUMERO_TESTE` com a flag desligada. As **duas** camadas devem ser preservadas — nunca remova uma "porque a outra já cobre".
2. **Idempotência por `chave_aso`** = `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` (`src/state/manager.py`). Há índice UNIQUE em `asos_enviados.chave_aso`. Rodar o pipeline 2x no mesmo dia não pode duplicar envio.
3. **Fail-safe de inadimplência.** Antes de buscar os exames de cada empresa, `coletar_asos_por_data` consulta o exportador `200410`. Empresa inadimplente **ou erro na consulta** → não recebe ASO. O erro bloqueia por precaução; nunca inverter para "na dúvida, envia".
4. **Consultas ao Supabase têm de ser bounded.** `buscar_chaves_enviadas`/`buscar_asos_pendentes` filtram por janela de `data_emissao` (`JANELA_DIAS`). Sem isso o PostgREST **trunca em silêncio** ao passar do limite de linhas e ASOs já enviados voltam a ser reenviados. **Já aconteceu em produção** — post-mortem em [OPERACAO.md](OPERACAO.md#post-mortem--reenvio-de-asos-jul2026). Toda query nova ao Supabase precisa ser paginada (`Range`) ou filtrada por natureza.

## Como rodar

Pipeline (local, modo seguro — só manda para o número de teste):

```bash
python main.py                    # data de hoje
python main.py --ontem            # reprocessa ontem (d-1)
python main.py --data 09/05/2026  # reprocessa uma data específica
```

`ENVIO_REAL_EMPRESAS=false` no `.env` garante que nada chega a número real.

Inbox / CRM (local):

```bash
uvicorn inbox.app:app --host 0.0.0.0 --port 8002 --reload
# dashboard http://localhost:8002/  ·  webhook em /webhook
```

Deploy e agendamento em produção: ver [OPERACAO.md](OPERACAO.md).

## Convenções (preferências do mantenedor)

- **Português no domínio.** Campos do SOC (`CD_GED`, `DT_EMISSAO`, `EMPRESA_CONSULTADA`), nomes de função e comentários em pt-BR. Manter.
- **Sem wrappers/artefatos desnecessários.** Resolver o problema pedido, direto — sem `run.sh`, logs extras, métricas ou buffers que ninguém pediu.
- **Testes em `testes/`** usam variáveis hardcoded no topo do arquivo (seção `# ── Flags de teste — altere aqui ──`), não argparse/CLI — o mantenedor edita e roda pela IDE.
- **Confirmar antes de mexer em produção/infra.** Para qualquer coisa que escreva/edite arquivos ou rode comandos com efeito (gerar segredos, criar rotas, mexer em systemd/Docker/painel Meta), apresentar o plano em texto e esperar o "ok" — não sair implementando direto.
- **Arquitetura em camadas:** `src/soc/` não importa `src/meta/` e vice-versa; só `main.py` conhece todos. Trocar de provedor deve afetar uma pasta só.

## Mapa de arquivos

```
main.py                    # Orquestrador do pipeline — 5 etapas numeradas no código
config.py                  # Carrega .env, expõe constantes (inclui EMPRESAS_BLOQUEADAS)
src/
  soc/api.py               # SOC REST "Exporta Dados": empresas, ASOs, contatos, inadimplência
  soc/downloader.py        # SOC SOAP WS-Security + parser MTOM/multipart (baixa PDF/ZIP)
  soc/empresa.py           # SOAP alterarEmpresa (atualiza cadastro no SOC)
  soc/cadastra_contatos.py # Playwright/CDP — cadastro de contatos (roda local, não na VPS)
  meta/whatsapp.py         # Upload + une PDFs num arquivo só + envio via template
  pipeline/processor.py    # Coleta em lote, gate de inadimplência, download, extração de ZIP
  state/manager.py         # chave_aso + filtro de não-enviados (idempotência)
  integrations/supabase.py # PostgREST: estado de envio (asos_enviados)
  utils/helpers.py         # retry com backoff, normalização de telefone, magic bytes PDF/ZIP
inbox/                     # Serviço FastAPI: webhook Meta + SPA (Dashboard/Conversas/ASOs)
```

Coisas que **não** existem mais (não recriar sem motivo): `src/integrations/email.py`, a tabela `empresas` no Supabase, e o bot 24/7 + Chatwoot. `registrar_erro()` (helpers) hoje só faz `print`. Os únicos avisos ativos saem por WhatsApp ao `META_NUMERO_TESTE`: o resumo de inadimplência e o alerta de falha de conexão com o Supabase.

## Variáveis de ambiente

`.env` (nunca commitado; base em `.env.example`). As críticas:

- `ENVIO_REAL_EMPRESAS` — `true` só em produção
- `META_ENVIAR` — habilita o envio de fato
- `SUPABASE_SERVICE_KEY` — service_role, só server-side (bypassa RLS; nunca vai ao browser)
- `SOC_CHAVE_*` — chaves dos exportadores: `192392` empresas, `191710` GED/ASOs, `193815` contatos, `200410` inadimplência
- `META_WA_TOKEN`, `META_PHONE_NUMBER_ID`, `META_TEMPLATE_NAME`, `META_NUMERO_TESTE`
- `WEBHOOK_VERIFY_TOKEN`, `INBOX_INTERNAL_TOKEN` — Inbox

## Gotchas

- **Run do pipeline morto no meio** (enviou mas não marcou no Supabase) → reenvio no próximo run. Por isso a unit systemd usa `TimeoutStartSec=0` (ver [OPERACAO.md](OPERACAO.md#deploy-do-pipeline-systemd-timer)).
- **Empresa "sumiu" do envio sem erro claro** → provavelmente bloqueada por inadimplência/erro no `200410`, ou está em `EMPRESAS_BLOQUEADAS` (`config.py`), ou sem contato válido no exportador `193815`. Checklist em [OPERACAO.md](OPERACAO.md#diagnóstico-empresa-não-recebeu-aso).
- **Número sem o 9º dígito:** a Meta às vezes entrega assim; o Inbox gera variantes com/sem o 9 para casar envio e resposta na mesma conversa.
- **Custo Meta:** todos os ASOs de uma empresa vão unidos em 1 PDF, 1 template = 1 conversa cobrada.
