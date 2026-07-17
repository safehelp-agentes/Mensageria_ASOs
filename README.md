<div align="center">

<img src="https://img.shields.io/badge/status-produção-22c55e?style=flat-square" alt="Em produção">
<img src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/WhatsApp_Business-API-25D366?style=flat-square&logo=whatsapp&logoColor=white" alt="WhatsApp">
<img src="https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?style=flat-square&logo=supabase&logoColor=white" alt="Supabase">
<img src="https://img.shields.io/badge/deploy-Ubuntu_24.04-E95420?style=flat-square&logo=ubuntu&logoColor=white" alt="Ubuntu">

<br><br>

# SafeWork — Automação de ASOs via WhatsApp

**Plataforma completa de entrega de ASOs (Atestados de Saúde Ocupacional) via WhatsApp Business. Composta por dois módulos independentes: um pipeline automático diário e uma automação de cadastro de contatos — ambos integrados ao sistema legado SOC e ao Supabase.**

[Configuração](#configuração) · [Pipeline](#pipeline-automático) · [Cadastro de Contatos](#cadastro-de-contatos) · [Deploy](#deploy)

</div>

---

## O problema que resolve

Empresas de saúde ocupacional emitem **ASOs** diariamente para seus clientes. O fluxo manual — exportar PDF, encontrar o contato, enviar — tomava horas.

Este sistema resolve isso com um **pipeline automático**: todo dia útil busca novos ASOs no SOC e entrega no WhatsApp do RH de cada empresa, sem intervenção humana. O estado de envio (dedup/idempotência) fica no Supabase.

---

## Visão geral da arquitetura

```
┌──────────────────────────── PIPELINE (cron diário) ─────────────────────────┐
│                                                                              │
│  main.py ──► SOC REST API ──► lista empresas + ASOs novos                   │
│      │                                                                       │
│      ├──► SOC SOAP/WS-Security ──► download PDFs (MTOM multipart + ZIP)     │
│      │                                                                       │
│      ├──► Supabase ──► dedup / estado de envio (idempotência)               │
│      │                                                                       │
│      └──► Meta Cloud API ──► WhatsApp Business (1 template, PDFs unidos)    │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── CADASTRO DE CONTATOS (manual) ──────────────────┐
│                                                                              │
│  Google Forms ──► Google Sheets ──► cadastra_contatos.py                    │
│                                            │                                │
│                                  Playwright CDP (Chrome local)              │
│                                            │                                │
│                                    SOC Web UI (tela 337/480)                │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

O pipeline é send-only: entrega o ASO via Meta e marca o envio no Supabase.
Não há CRM nem registro em Chatwoot — respostas dos clientes não são coletadas.
```

---

## Destaques técnicos

### Integração SOAP/WS-Security com sistema legado
O SOC não tem REST para download — usa Web Service SOAP com **WS-Security PasswordDigest** (SHA-1, nonce aleatório de 16 bytes, timestamp com janela de 5 minutos). A resposta chega como `multipart/related` com o PDF embutido via **MTOM**. Implementado do zero em `src/soc/downloader.py`.

### Otimização de custo no WhatsApp Business
A Meta cobra por **conversa iniciada**. Para empresas com múltiplos ASOs, todos os PDFs são unidos num único arquivo (um marcador/bookmark por funcionário) e enviados numa única mensagem de *template aprovado*. Uma empresa com 5 ASOs paga como se fosse 1 — e a entrega não depende da janela de 24h, já que é sempre uma única mensagem.

### Pipeline idempotente com deduplicação composta
Cada ASO tem chave natural `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` consultada no Supabase antes de qualquer processamento. Rodar o pipeline duas vezes no mesmo dia é seguro.

---

## Stack

| Camada | Tecnologia | Por quê |
|---|---|---|
| Pipeline | **Python 3.10+** | Legibilidade, ecossistema requests/crypto |
| Sistema de origem | **SOC** (REST + SOAP WS-Security) | Sistema legado do cliente — sem alternativa |
| Mensageria | **Meta Cloud API v19.0** | Única forma oficial de WhatsApp Business em escala |
| Banco / estado | **Supabase** (PostgreSQL + PostgREST) | REST nativo para dedup/estado de envio |
| Proxy reverso | **Traefik** (Docker) | TLS automático via Let's Encrypt |
| Deploy | **VPS Ubuntu 24.04** | Sem overhead de K8s para pipeline diário |

**Dependências Python:**
```
# Pipeline
python-dotenv   requests   cryptography   defusedxml   pypdf

# Cadastro de contatos (adicionais)
playwright   gspread
```

> `pypdf` é usado por `src/meta/whatsapp.py` pra unir os PDFs de uma empresa num único arquivo antes do envio (ver [Estratégia de envio WhatsApp](ARCHITECTURE.md#7-estratégia-de-envio-whatsapp)).

---

## Início rápido

### Pré-requisitos

- Python 3.10+
- Conta SOC com chaves dos exportadores (`192392`, `191710`, `193815`) e credenciais WS SOAP
- App Meta Business com template aprovado para documento
- Projeto Supabase com tabelas `empresas` e `asos_enviados`

### Instalação

```bash
git clone <repo-url> /opt/safework/envio_ASO
cd /opt/safework/envio_ASO

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env   # preencha as variáveis (seção abaixo)
```

---

## Pipeline automático

Roda via cron, busca ASOs novos no SOC e entrega no WhatsApp de cada empresa.

### Execução manual

```bash
# Modo seguro — envia só para o número de teste (ENVIO_REAL_EMPRESAS=false)
python main.py

# Reprocessa ASOs emitidos ontem
python main.py --ontem

# Reprocessa uma data específica
python main.py --data "09/05/2026"
```

> **Por padrão `ENVIO_REAL_EMPRESAS=false`** — nenhum documento chega ao número real até você ativar essa flag. Há ainda um bloqueio explícito em `_validar_numero_destino()` que aborta o envio se a flag estiver desativada.

### Agendamento via cron

```cron
# Envia ASOs do dia atual às 18h (dias úteis)
0 18 * * 1-5 cd /opt/safework/envio_ASO && .venv/bin/python main.py >> /var/log/safework/aso.log 2>&1

# Captura o que ficou de ontem às 8h
0 8  * * 1-5 cd /opt/safework/envio_ASO && .venv/bin/python main.py --ontem >> /var/log/safework/aso.log 2>&1
```

### Saídas de cada execução

| Arquivo | Conteúdo |
|---|---|
| `output/saida_asos/asos_DD-MM-YYYY.json` | Todos os ASOs encontrados no SOC |
| `output/saida_asos/resumo_execucao.json` | Status por empresa (downloads, envios, erros) |
| `output/debug_downloads/` | Dumps de respostas SOAP problemáticas |

> `output/temp_asos/` é apagado no início de cada execução — PDFs não persistem no disco.

---

## Cadastro de Contatos

Automação que lê respostas de um Google Forms (empresas que querem receber ASOs por WhatsApp) e cadastra o contato responsável diretamente na tela de Contatos do SOC via Playwright.

**Não roda na VPS** — depende de um Chrome local já logado no SOC via CDP.

### Pré-requisitos

```bash
pip install playwright gspread
playwright install chromium
```

Abra o Chrome com a porta de debug e faça login no SOC manualmente:

```
chrome.exe --remote-debugging-port=9222 --user-data-dir="C:\chrome-soc"
```

Deixe na **tela de pesquisa de empresa (337)** antes de rodar o script.

### Execução

```bash
# Lê do Google Sheets (Forms)
python src/soc/cadastra_contatos.py --sheets

# Lê de um CSV local
python src/soc/cadastra_contatos.py caminho/para/planilha.csv

# Padrão: lê de data/cadastro_contatos.csv
python src/soc/cadastra_contatos.py
```

### Comportamento

- Cadastra nome, telefone e e-mail do responsável na tela 480 (Contatos) do SOC
- Detecta duplicatas via popup nativo do SOC e pula sem quebrar o lote
- Isola falhas por empresa — erro em uma empresa não interrompe as demais
- Gera screenshot de evidência em `evidencias_contatos/` para cada falha

---

## Configuração

Todas as variáveis vivem no `.env`. Use `.env.example` como base.

### SOC — Pipeline

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SOC_EMPRESA` | sim | Código da empresa principal |
| `SOC_CHAVE_EMPRESAS` | sim | Chave exportador `192392` (lista de empresas) |
| `SOC_CHAVE_GED` | sim | Chave exportador `191710` (ASOs/GED) |
| `SOC_CHAVE_CONTATOS` | sim | Chave exportador `193815` (contatos p/ pipeline) |
| `SOC_WS_USUARIO` | sim | Usuário SOAP |
| `SOC_WS_PASSWORD` | sim | Senha SOAP |
| `SOC_CODIGO_RESPONSAVEL` | sim | Código do responsável (SOAP) |
| `SOC_CODIGO_USUARIO` | sim | Código do usuário (SOAP) |

### Meta / WhatsApp

| Variável | Obrigatório | Descrição |
|---|---|---|
| `META_WA_TOKEN` | sim | Token Bearer do app Meta Business |
| `META_PHONE_NUMBER_ID` | sim | ID do número WhatsApp Business |
| `META_TEMPLATE_NAME` | sim | Nome do template aprovado |
| `META_NUMERO_TESTE` | sim | Número de destino quando `ENVIO_REAL_EMPRESAS=false` |
| `META_ENVIAR` | não | `true` para enviar de fato. Padrão: `false` |
| `ENVIO_REAL_EMPRESAS` | não | `true` libera envio aos números reais. Padrão: `false` |

### Supabase

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SUPABASE_URL` | sim | URL do projeto (`https://xxx.supabase.co`) |
| `SUPABASE_SERVICE_KEY` | sim | `service_role` key — usada pelo pipeline para gravar estado |
| `SUPABASE_SECRET_KEY` | sim | `anon/public` key — usada pelo cliente `src/integrations/supabase.py` |

> ⚠️ **Nunca exponha a `service_role` key em frontend/browser** — ela bypassa o Row Level Security e daria acesso total ao banco.

### SOC — WebService ImportacaoEmpresa (SOAP)

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SOC_IMPORTACAO_EMPRESA_URL` | não | Endpoint do serviço. Padrão embutido: `https://ws1.soc.com.br/WSSoc/EmpresaWs` |
| `SOC_CHAVE_IMPORTACAO_EMPRESA` | não | `chaveAcesso` para `identificacaoWsVo` (opcional conforme WSDL) |
| `SOC_HOMOLOGACAO` | não | `true` para ambiente de homologação. Padrão: `false` |

### Cadastro de Contatos (Playwright + Google Sheets)

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SOC_CDP_URL` | não | URL do Chrome com debug ativo. Padrão: `http://localhost:9222` |
| `GOOGLE_CREDENTIALS_JSON` | sim (--sheets) | Caminho para o JSON da service account Google |
| `GOOGLE_SHEETS_ID` | sim (--sheets) | ID da planilha Google Sheets conectada ao Forms |
| `GOOGLE_SHEETS_GID` | sim (--sheets) | GID da aba com as respostas do Forms |

### Alertas por e-mail (opcional)

| Variável | Descrição |
|---|---|
| `EMAIL_REMETENTE` | Conta Gmail que envia o relatório |
| `EMAIL_SENHA_APP` | [App Password](https://myaccount.google.com/apppasswords) do Gmail |
| `EMAIL_DESTINO` | Destinatário do relatório de erros |
| `EMAIL_ENVIAR` | `true`/`false`. Padrão: `false` |

---

## Estrutura do projeto

```
envio_ASO/
├── main.py                        # Orquestrador do pipeline — etapas numeradas
├── config.py                      # Carrega .env, expõe constantes tipadas
├── deploy.sh                      # Script de deploy (git pull + pip)
│
├── testes/                        # Scripts de teste manual (flags hardcoded no topo, sem argparse)
│   └── teste_funcionario_guia.py         # Testa exportador 216658 (Funcionário e Guia)
│
└── src/
    ├── soc/
    │   ├── api.py                 # Cliente REST Exporta Dados (empresas, GED, contatos)
    │   ├── downloader.py          # Cliente SOAP WS-Security + parser MTOM multipart
    │   ├── empresa.py             # SOAP alterarEmpresa — atualiza dados cadastrais no SOC
    │   └── cadastra_contatos.py   # Playwright CDP — cadastra contatos via UI web do SOC
    │
    ├── meta/
    │   └── whatsapp.py            # Upload PDF + une PDFs num único arquivo + envio via template
    │
    ├── pipeline/
    │   └── processor.py           # Coleta em lote, download, extração ZIP, agrupamento
    │
    ├── state/
    │   └── manager.py             # Chave de identidade ASO, deduplicação
    │
    ├── integrations/
    │   ├── supabase.py            # PostgREST — upsert empresas e ASOs (dedup/estado)
    │   └── email.py               # Relatório de erros via SMTP Gmail
    │
    └── utils/
        └── helpers.py             # Retry com backoff, sanitização, detecção PDF/ZIP
```

---

## Deploy

### Atualizar código

```bash
cd /opt/safework/envio_ASO
git pull origin main

# Ou simplesmente rode o deploy.sh, que já cuida de git pull + pip
./deploy.sh
```

O pipeline roda por cron (não é um serviço contínuo) — não há containers próprios para verificar. Basta conferir os logs da última execução em `/var/log/safework/aso.log`.

---

## Segurança

- **Dupla trava de envio real** — `ENVIO_REAL_EMPRESAS=false` por padrão + bloqueio explícito em `_validar_numero_destino()`, independente da config
- **Deduplicação idempotente** — chave `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` impede reenvio
- **WS-Security com nonce único** — 16 bytes aleatórios por chamada SOAP; tokens expiram em 5 minutos
- **Chave Supabase server-side** — `service_role` usada apenas no pipeline (servidor), nunca em frontend
- **PDFs não persistem no disco** — arquivos temporários são deletados após upload Meta via `finally: os.unlink()`
- **Credenciais fora do repositório** — `.env` no `.gitignore`; `.env.example` documenta sem valores

```bash
# Auditoria rápida de credenciais no repositório
git ls-files | grep -E '\.env$|\.key$|\.pem$'
```

Detalhes completos em [SECURITY.md](SECURITY.md).

---

## Troubleshooting

| Sintoma | Causa provável | Como resolver |
|---|---|---|
| `codigoMensagem != SOC-100` | Credenciais SOAP ou chave GED inválida | Inspecionar `output/debug_downloads/<chave>_xml.txt` |
| `Payload em formato inesperado` | SOC devolveu HTML em vez do PDF | Mesmo dump acima |
| `Erro upload PDF Meta: HTTP 401` | Token Meta expirado | Renovar em developers.facebook.com |
| `Erro envio template: HTTP 400` | Template não aprovado ou nome errado | Painel Meta → Message Templates |
| `BLOQUEIO DE SEGURANÇA` | Trava funcionando corretamente | Ativar `ENVIO_REAL_EMPRESAS=true` |
| `[SUPABASE] Erro upsert: 403` | RLS bloqueando ou chave errada | Verificar `SUPABASE_SERVICE_KEY` em supabase.com → Settings → API |
| Cadastro: `iframe 'socframe' não encontrado` | SOC não está aberto no Chrome CDP | Abrir Chrome com `--remote-debugging-port=9222`, logar no SOC e deixar na tela 337 |
| Cadastro: `PermissionError` no Google Sheets | Planilha não compartilhada com a service account | Compartilhar com o email do JSON como Leitor |
| Cadastro: `GOOGLE_SHEETS_ID não definido` | Variável ausente no `.env` | Adicionar `GOOGLE_SHEETS_ID` ao `.env` |

---


<div align="center">

Desenvolvido por **Herick Campos** para **SafeWork** · Maio de 2026

</div>
