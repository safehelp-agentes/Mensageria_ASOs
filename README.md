<div align="center">

<img src="https://img.shields.io/badge/status-produção-22c55e?style=flat-square" alt="Em produção">
<img src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/WhatsApp_Business-API-25D366?style=flat-square&logo=whatsapp&logoColor=white" alt="WhatsApp">
<img src="https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?style=flat-square&logo=supabase&logoColor=white" alt="Supabase">
<img src="https://img.shields.io/badge/Groq-LLM-F55036?style=flat-square" alt="Groq">
<img src="https://img.shields.io/badge/deploy-Ubuntu_24.04-E95420?style=flat-square&logo=ubuntu&logoColor=white" alt="Ubuntu">

<br><br>

# SafeWork — Automação de ASOs via WhatsApp

**Plataforma completa de entrega de ASOs (Atestados de Saúde Ocupacional) via WhatsApp Business. Composta por três módulos independentes: um pipeline automático diário, um bot de atendimento sob demanda e uma automação de cadastro de contatos — todos integrados ao sistema legado SOC e ao Supabase.**

[Configuração](#configuração) · [Pipeline](#pipeline-automático) · [Bot](#bot-de-atendimento) · [Chatwoot](#chatwoot-crm-de-chat) · [Cadastro de Contatos](#cadastro-de-contatos) · [CRM](#crm) · [Deploy](#deploy)

</div>

---

## O problema que resolve

Empresas de saúde ocupacional emitem **ASOs** diariamente para seus clientes. O fluxo manual — exportar PDF, encontrar o contato, enviar — tomava horas. Além disso, RHs precisavam ligar para solicitar documentos de funcionários específicos. E a equipe interna não tinha visibilidade das conversas do WhatsApp.

Este sistema resolve os três lados:

- **Pipeline automático**: todo dia útil busca novos ASOs no SOC e entrega no WhatsApp do RH de cada empresa, sem intervenção humana
- **Bot de atendimento**: quando o RH precisa de um ASO específico a qualquer hora, basta enviar uma mensagem no WhatsApp — o bot localiza e entrega o PDF em segundos
- **CRM Chatwoot**: todas as mensagens WhatsApp aparecem em tempo real para a equipe, que pode responder diretamente pelo Chatwoot — a resposta chega no WhatsApp do contato

---

## Visão geral da arquitetura

```
┌──────────────────────────── PIPELINE (cron diário) ─────────────────────────┐
│                                                                              │
│  main.py ──► SOC REST API ──► lista empresas + ASOs novos                   │
│      │                                                                       │
│      ├──► SOC SOAP/WS-Security ──► download PDFs (MTOM multipart + ZIP)     │
│      │                                                                       │
│      ├──► Supabase ──► dedup / estado / CRM                                 │
│      │                                                                       │
│      ├──► Meta Cloud API ──► WhatsApp Business (1 template + N documentos)  │
│      │                                                                       │
│      └──► Chatwoot API ──► registra nota de envio na conversa do contato    │
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

┌──────────────────────────── BOT + CRM (Docker 24/7) ────────────────────────┐
│                                                                              │
│  WhatsApp ──► Meta Webhook ──► webhook_meta.py ──► POST /bot/mensagem       │
│                (Docker/n8n)   (Docker/n8n)              │                   │
│                                               bot/service.py (Docker)       │
│                                                  │           │              │
│                             ┌────────────────────┘           │              │
│                             ▼                                ▼              │
│                    Chatwoot (CRM)                   BOT_ATIVO=true?         │
│                  espelha mensagem                           │               │
│                  recebida para a                   bot/handler.py           │
│                  equipe ver                        (máquina de estados)     │
│                                            ┌────────┴──────┬──────┐        │
│                                            ▼               ▼      ▼        │
│                                       Groq LLM       SOC REST   Supabase   │
│                                            │                               │
│                                       SOC SOAP ──► PDF ──► Meta ──► WA    │
│                                                                              │
│  Agente no Chatwoot responde ──► POST /chatwoot/webhook (Traefik/HTTPS)     │
│                                       │                                     │
│                                  bot/service.py ──► Meta API ──► WhatsApp  │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘

Conversas inbound ──► Chatwoot (CRM de chat, chat.srv1564091.hstgr.cloud)
Histórico de envios ──► Supabase ──► CRM (index.html)
```

---

## Destaques técnicos

### Integração SOAP/WS-Security com sistema legado
O SOC não tem REST para download — usa Web Service SOAP com **WS-Security PasswordDigest** (SHA-1, nonce aleatório de 16 bytes, timestamp com janela de 5 minutos). A resposta chega como `multipart/related` com o PDF embutido via **MTOM**. Implementado do zero em `src/soc/downloader.py`.

### Otimização de custo no WhatsApp Business
A Meta cobra por **conversa iniciada**. Para empresas com múltiplos ASOs, o 1º PDF abre via *template aprovado* (cobrado), os demais chegam como *documentos simples* dentro da janela de 24h (gratuitos). Uma empresa com 5 ASOs paga como se fosse 1.

### Bot com máquina de estados e LLM
O bot mantém o estado de cada conversa no Supabase (fases: `livre → menu_principal → aguardando_nome → aguardando_funcionario → aguardando_confirmacao`). Usa Groq (llama-3.3-70b-versatile) apenas para interpretar linguagem natural — a lógica de negócio e o controle de fluxo ficam no Python, sem depender do LLM para decisões críticas.

### Cross-referência de dois exportadores SOC para tipo de ASO
O GED (exportador `191710`) fornece os PDFs mas não sabe o tipo de exame. O exportador `193037` sabe o tipo (Admissional, Periódico, etc.) mas não tem os arquivos. O bot cruza os dois por `MM/YYYY` — tolerante a diferenças de 1-2 dias entre os sistemas — para exibir o tipo correto na lista de seleção.

### ASOs sem PDF sinalizados, não silenciados
Quando o 193037 registra um ASO mas o GED não tem o arquivo correspondente, o bot exibe o item na lista com aviso `⚠️ documento não disponível` em vez de omiti-lo. O usuário entende que o exame existe, mas o documento não foi digitalizado.

### Pipeline idempotente com deduplicação composta
Cada ASO tem chave natural `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` consultada no Supabase antes de qualquer processamento. Rodar o pipeline duas vezes no mesmo dia é seguro.

### Validação de acesso por número de WhatsApp
Ao receber uma mensagem, o bot consulta o exportador `215872` (Contatos das Empresas) e verifica se o número está cadastrado. O campo `CODIGOEMPRESA` do contato restringe todas as buscas da sessão — o RH só vê dados da própria empresa.

### CRM single-file sem backend
`index.html` (~1.800 linhas) é um CRM completo que roda no browser. Conecta ao Supabase via PostgREST, exibe histórico de conversas, dashboard com gráficos, painel de ASOs e gestão de empresas — sem Node, sem framework, sem build step além da injeção de credenciais via `build.py`.

---

## Stack

| Camada | Tecnologia | Por quê |
|---|---|---|
| Pipeline | **Python 3.10+** | Legibilidade, ecossistema requests/crypto |
| Bot | **FastAPI + uvicorn** | ASGI assíncrono, ideal para webhook de alta concorrência |
| LLM | **Groq** (llama-3.3-70b-versatile) | Inferência rápida e gratuita para interpretação de linguagem natural |
| Sistema de origem | **SOC** (REST + SOAP WS-Security) | Sistema legado do cliente — sem alternativa |
| Mensageria | **Meta Cloud API v19.0** | Única forma oficial de WhatsApp Business em escala |
| Banco / estado | **Supabase** (PostgreSQL + PostgREST) | REST nativo, realtime para CRM, auth integrada |
| CRM de chat | **Chatwoot** v4.15 (Docker) | Agentes respondem no Chatwoot → vai ao WhatsApp |
| Webhook inbound | **n8n** (Docker) | Recebe callbacks da Meta e repassa ao bot |
| Proxy reverso | **Traefik** (Docker) | TLS automático via Let's Encrypt |
| Deploy | **VPS Ubuntu 24.04** | Sem overhead de K8s para pipeline diário + bot leve |
| CRM | **HTML/CSS/JS vanilla** | Zero dependências, zero build contínuo, funciona em qualquer CDN |

**Dependências Python:**
```
# Pipeline
python-dotenv   requests   cryptography   defusedxml

# Bot (adicionais)
fastapi   uvicorn   openai   pydantic

# Cadastro de contatos (adicionais)
playwright   gspread
```

---

## Início rápido

### Pré-requisitos

- Python 3.10+
- Conta SOC com chaves dos exportadores (`192392`, `191710`, `193815`, `192399`, `215872`, `193037`) e credenciais WS SOAP
- App Meta Business com template aprovado para documento
- Projeto Supabase com tabelas `empresas`, `asos_enviados`, `mensagens`, `conversas_bot`
- Conta Groq (gratuita) para o bot

### Instalação

```bash
git clone <repo-url> /opt/safework/envio_ASO
cd /opt/safework/envio_ASO

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
pip install fastapi uvicorn openai pydantic   # dependências do bot

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

## Bot de atendimento

Servidor FastAPI que responde mensagens WhatsApp sob demanda, 24/7. Roda como container Docker (stack `docker/bot/`) exposto via Traefik.

### Iniciar (primeira vez)

```bash
cd /opt/safework/envio_ASO/docker/bot

# Build da imagem (1x — só repetir se requirements.txt mudar)
docker build -t safework-bot .

# Subir o container
docker compose up -d

# Verificar
docker logs bot-bot-service-1 --tail 20
# → Application startup complete.
# → Uvicorn running on http://0.0.0.0:8001
```

### Gerenciar o container

```bash
# Reiniciar (necessário após atualizar service.py)
docker compose -f /opt/safework/envio_ASO/docker/bot/docker-compose.yml restart

# Ver estado
docker ps --filter name=bot-bot-service

# Ver logs em tempo real
docker logs bot-bot-service-1 -f
```

### Verificar status

```bash
# De dentro do container webhook-aso (testa a comunicação interna)
docker exec n8n-webhook-aso-1 python3 -c \
  "import urllib.request; r=urllib.request.urlopen('http://bot-service:8001/bot/health'); print(r.read().decode())"
# → {"status":"ok","bot_ativo":false}
```

### Fluxo de conversa

```
Mensagem recebida
      │
      ├─ Número não cadastrado no SOC (215872)? → recusa atendimento
      │
      ├─ Número de teste? → exige comando "empresa XXXXX" para definir empresa
      │
      └─ Número válido → identifica empresa pelo CODIGOEMPRESA do contato
             │
             ├─ fase: livre / nova → boas-vindas + detecta intenção (LLM)
             ├─ fase: menu_principal → opção 1 (buscar ASO) ou 0 (finalizar)
             ├─ fase: aguardando_nome_funcionario → extrai nome via LLM
             ├─ fase: aguardando_funcionario → lista funcionários, usuário escolhe
             └─ fase: aguardando_confirmacao → lista ASOs, usuário escolhe → envia PDF
```

### Endpoints

| Método | Rota | Descrição |
|---|---|---|
| `POST` | `/bot/mensagem` | Recebe mensagem do webhook_meta.py (via rede Docker interna) |
| `GET` | `/bot/health` | Health check |
| `POST` | `/chatwoot/webhook` | Recebe eventos do Chatwoot (agente respondeu → envia ao WhatsApp) |

O endpoint `/chatwoot/webhook` é exposto publicamente via Traefik em `https://n8n.srv1564091.hstgr.cloud/chatwoot/webhook` para contornar a proteção SSRF do Chatwoot (que bloqueia IPs privados).

### Modo de teste

Defina `BOT_NUMEROS_TESTE` no `.env` com seu número. Ao receber mensagens desse número, o bot só responde a ele. Para simular uma empresa específica, envie:

```
empresa 1530555
```

O bot assume aquela empresa para toda a sessão, como se o número estivesse cadastrado no SOC com aquele `CODIGOEMPRESA`.

---

## Chatwoot CRM de chat

O Chatwoot é o hub de atendimento humano: todas as mensagens WhatsApp recebidas aparecem lá para os agentes, e respostas digitadas no Chatwoot chegam ao contato pelo WhatsApp.

**Acesso:** `https://chat.srv1564091.hstgr.cloud`

### Fluxo completo

```
WhatsApp → Meta → webhook_meta.py → /bot/mensagem → espelhar_inbound() → Chatwoot
                                                                          (conversa aparece)

Agente digita no Chatwoot → webhook → /chatwoot/webhook → Meta API → WhatsApp do contato

Pipeline envia ASO → Meta API → WhatsApp do contato
                  └───────────────────────────────→ Chatwoot (mensagem de envio na conversa)
```

### Chatwoot é um projeto separado

O Chatwoot **não mora mais neste repositório**. Ele foi migrado para um projeto independente, já que passou a ser usado por mais de uma automação além do envio de ASOs:

- **Repositório:** `github.com/safehelp-agentes/ChatWoot`
- **Local na VPS:** `/opt/safework/chatwoot` (antes: `/opt/safework/envio_ASO/docker/chatwoot`)

```bash
cd /opt/safework/chatwoot

# Iniciar / reiniciar
docker compose up -d
docker compose restart chatwoot_app chatwoot_worker

# Logs
docker logs chatwoot-chatwoot_app-1 -f
```

### Configuração da inbox usada pelo espelhamento

> A inbox original "API Channel" havia sido apagada em algum momento e substituída por um canal nativo "WhatsApp Cloud API", que envia mensagem real pela Meta sempre que um registro `outgoing` é criado via API — causando envio duplicado ao cliente quando usada para o espelhamento (`espelhar_outbound`/`espelhar_envio_sistema`). Isso já foi corrigido: recriamos a inbox correta (tipo "API Channel", nome "WhatsApp SafeWork") e atualizamos `CHATWOOT_INBOX_ID`. **Nunca aponte `CHATWOOT_INBOX_ID` para uma inbox de canal nativo** (WhatsApp Cloud API, etc.) — só para "API Channel".

1. **Inbox** tipo "API Channel" (**não** "WhatsApp Cloud API") — "WhatsApp SafeWork"
2. **Webhook da inbox**: configurado via API (`channel.webhook_url`) apontando para `https://n8n.srv1564091.hstgr.cloud/chatwoot/webhook?token=<CHATWOOT_WEBHOOK_TOKEN>`
3. **Eventos que disparam o webhook**: `message_created` (o bot filtra por `message_type == "outgoing"` e ignora ecos — ver `bot/service.py`)

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

## CRM

Interface web para monitorar conversas, ASOs enviados, empresas e métricas.

**Acesso:** [`https://n8n.srv1564091.hstgr.cloud/CrmEnvioAso`](https://n8n.srv1564091.hstgr.cloud/CrmEnvioAso)

O `index.html` é servido pelo container `n8n-crm-1` (nginx:alpine), com volume mapeado em `/opt/safework/crm`. O Traefik faz o proxy reverso com TLS automático — não é necessário nenhum serviço externo (antigo Netlify foi descontinuado).

### Atualizar o CRM após mudanças

```bash
cd /opt/safework/envio_ASO

# 1. Restaura o template (descarta credenciais do build anterior)
git checkout index.html

# 2. Puxa as mudanças
git pull origin main

# 3. Injeta credenciais e copia para o nginx
export $(grep -v '^#' .env | grep -v '^$' | xargs)
python build.py
cp index.html /opt/safework/crm/
```

> O nginx serve o arquivo em tempo real — não precisa reiniciar o container.

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

### SOC — Bot

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SOC_CHAVE_FUNCIONARIOS` | sim | Chave exportador `192399` (cadastro de funcionários) |
| `SOC_CHAVE_CONTATOS_WA` | sim | Chave exportador `215872` (validação de acesso por número) |
| `SOC_CHAVE_ASO_FUNCIONARIO` | sim | Chave exportador `193037` (tipo de ASO por funcionário) |

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
| `SUPABASE_SERVICE_KEY` | sim | `service_role` key — backend/pipeline/bot apenas |
| `SUPABASE_ANON_KEY` | sim | `anon/public` key — usada pelo `build.py` para o CRM |

> ⚠️ **Nunca use a `service_role` key no CRM** — ela bypassa o Row Level Security e daria acesso total ao banco no browser. A `anon` key é a chave pública do Supabase (Settings → API → anon/public).

### Bot

| Variável | Obrigatório | Descrição |
|---|---|---|
| `GROQ_API_KEY` | sim | Chave da API Groq ([console.groq.com](https://console.groq.com) — gratuito) |
| `BOT_ATIVO` | não | `false` desliga o bot sem parar o serviço. Padrão: `true` |
| `BOT_NUMEROS_TESTE` | não | Quando preenchido, bot responde só esses números. Ex: `5511999999999` |
| `BOT_PORT` | não | Porta do serviço FastAPI. Padrão: `8001` |
| `BOT_MODEL` | não | Modelo Groq. Padrão: `llama-3.3-70b-versatile` |

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

### Chatwoot

| Variável | Obrigatório | Descrição |
|---|---|---|
| `CHATWOOT_ATIVO` | não | `true` para ativar espelhamento no Chatwoot. Padrão: `false` |
| `CHATWOOT_BASE_URL` | sim (se ativo) | URL do Chatwoot. Ex: `https://chat.srv1564091.hstgr.cloud` |
| `CHATWOOT_API_TOKEN` | sim (se ativo) | Token de acesso do agente bot (Chatwoot → Perfil → Token) |
| `CHATWOOT_ACCOUNT_ID` | sim (se ativo) | ID da conta Chatwoot (normalmente `1`) |
| `CHATWOOT_INBOX_ID` | sim (se ativo) | ID da inbox "WhatsApp SafeWork" — **precisa ser tipo "API Channel"**. Nunca apontar para inbox de canal nativo (ex: "WhatsApp Cloud API") — esse tipo envia mensagem real pela Meta ao espelhar, duplicando o envio do pipeline (ver [seção Chatwoot](#chatwoot-crm-de-chat)) |
| `CHATWOOT_WEBHOOK_TOKEN` | sim (se ativo) | Token de segurança para o endpoint `/chatwoot/webhook` |

> O bot valida `?token=` na URL do webhook. Configure o mesmo valor na URL do webhook da inbox no Chatwoot.

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
├── build.py                       # Injeta credenciais Supabase no index.html
├── index.html                     # CRM completo single-file (sem build contínuo)
├── deploy.sh                      # Script de deploy (git pull + pip + restart bot)
│
├── bot/                           # Bot de atendimento WhatsApp (FastAPI)
│   ├── service.py                 # App FastAPI: /bot/mensagem, /bot/health, /chatwoot/webhook
│   ├── handler.py                 # Máquina de estados — orquestra o fluxo de conversa
│   ├── tools.py                   # Ferramentas SOC: buscar funcionários, ASOs, baixar PDF
│   ├── llm.py                     # Interpretação de linguagem natural via Groq/OpenAI
│   └── state.py                   # Persistência de estado das conversas (Supabase via requests)
│
├── docker/
│   └── bot/                       # Container Docker do bot service
│       ├── Dockerfile             # python:3.11-slim + dependências FastAPI/openai
│       ├── docker-compose.yml     # Traefik labels + alias bot-service na rede n8n_default
│       └── requirements.txt       # fastapi, uvicorn, openai, requests, python-dotenv
│                                   # (Chatwoot é um projeto separado — github.com/safehelp-agentes/ChatWoot)
│
└── src/
    ├── soc/
    │   ├── api.py                 # Cliente REST Exporta Dados (empresas, GED, contatos)
    │   ├── downloader.py          # Cliente SOAP WS-Security + parser MTOM multipart
    │   ├── empresa.py             # SOAP alterarEmpresa — atualiza dados cadastrais no SOC
    │   └── cadastra_contatos.py   # Playwright CDP — cadastra contatos via UI web do SOC
    │
    ├── meta/
    │   └── whatsapp.py            # Upload PDF + template + documentos + espelho Chatwoot
    │
    ├── pipeline/
    │   └── processor.py           # Coleta em lote, download, extração ZIP, agrupamento
    │
    ├── state/
    │   └── manager.py             # Chave de identidade ASO, deduplicação
    │
    ├── integrations/
    │   ├── chatwoot.py            # Espelhamento WhatsApp↔Chatwoot (inbound/outbound/sistema)
    │   ├── supabase.py            # PostgREST — upsert empresas, ASOs, mensagens
    │   └── email.py               # Relatório de erros via SMTP Gmail
    │
    └── utils/
        └── helpers.py             # Retry com backoff, sanitização, detecção PDF/ZIP
```

---

## Deploy

### Atualizar código (pipeline + bot)

```bash
cd /opt/safework/envio_ASO
git pull origin main

# Reiniciar o container do bot para pegar as mudanças
docker compose -f docker/bot/docker-compose.yml restart

# Confirmar que subiu
docker logs bot-bot-service-1 --tail 5
```

> Se alterou `docker/bot/requirements.txt`, faça `docker build -t safework-bot .` antes do restart.

### Atualizar o CRM

```bash
cd /opt/safework/envio_ASO
git checkout index.html
git pull origin main
export $(grep -v '^#' .env | grep -v '^$' | xargs)
python build.py && cp index.html /opt/safework/crm/
```

### Verificar todos os serviços

```bash
docker ps --format "{{.Names}}\t{{.Status}}" | grep -E "bot|webhook|chatwoot|n8n|traefik"
```

Containers esperados:
- `bot-bot-service-1` — FastAPI bot
- `n8n-webhook-aso-1` — Recebe webhooks da Meta
- `n8n-traefik-1` (ou similar) — Proxy reverso HTTPS
- `chatwoot-chatwoot_app-1` — CRM Chatwoot
- `chatwoot-chatwoot_worker-1` — Worker Sidekiq
- `chatwoot-chatwoot_postgres-1` — Banco do Chatwoot
- `chatwoot-chatwoot_redis-1` — Cache do Chatwoot

---

## Segurança

- **Dupla trava de envio real** — `ENVIO_REAL_EMPRESAS=false` por padrão + bloqueio explícito em `_validar_numero_destino()`, independente da config
- **Validação de acesso por número** — bot consulta SOC a cada sessão nova; números não cadastrados são recusados imediatamente
- **Deduplicação idempotente** — chave `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` impede reenvio
- **WS-Security com nonce único** — 16 bytes aleatórios por chamada SOAP; tokens expiram em 5 minutos
- **Separação de chaves Supabase** — `service_role` apenas no backend; `anon` no CRM (frontend)
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
| Bot: não responde após `git pull` | Container não foi reiniciado | `docker compose -f docker/bot/docker-compose.yml restart` |
| Bot: container parado | Erro na inicialização | `docker logs bot-bot-service-1 --tail 50` para ver o motivo |
| Chatwoot: "Falha ao enviar" na resposta | Webhook não configurado ou bot parado | Verificar URL do webhook na inbox + `docker ps` |
| Chatwoot: mensagens recebidas não aparecem | `CHATWOOT_ATIVO=false` no .env | Setar `CHATWOOT_ATIVO=true` e reiniciar o bot |
| Chatwoot: conversa não abre para número novo | Contato não existe no Chatwoot | O bot cria automaticamente na primeira mensagem |
| ASOs não aparecem no Chatwoot | Pipeline não chamou `espelhar_envio_sistema` | Verificar se `CHATWOOT_ATIVO=true` no `.env` do servidor |
| Cliente recebe mensagem duplicada no WhatsApp | `CHATWOOT_INBOX_ID` aponta para inbox tipo "WhatsApp Cloud API" (canal nativo), que reenvia pela Meta ao espelhar | Recriar inbox como "API Channel" e atualizar `CHATWOOT_INBOX_ID` |
| Bot: "Não encontrei nenhum funcionário" para qualquer nome | `SOC_CHAVE_FUNCIONARIOS` ausente no `.env` | Adicionar chave do exportador `192399` ao `.env` e reiniciar |
| Bot: `[BOT] SOC retornou 0 registro(s)` nos logs | `SOC_EMPRESA` não configurado ou chave 192399 vinculada à empresa errada | Verificar `SOC_EMPRESA` no `.env` — o exportador 192399 exige `empresa=principal` |
| Bot: `Não foi encontrado registro no SOC` | Número não cadastrado no exportador 215872 | Cadastrar o contato no SOC com TEL1/TEL2 correto |
| Bot: tipo ASO não aparece na lista | `SOC_CHAVE_ASO_FUNCIONARIO` não configurado | Adicionar chave do exportador 193037 ao `.env` |
| Bot: `193037: 0 ASO(s)` nos logs | Empresa errada no parâmetro da API | O bot tenta empresa principal e empresa cliente automaticamente; verificar logs `[BOT] 193037 tentativa` |
| CRM: `Build OK` mas dados não aparecem | `SUPABASE_ANON_KEY` está com a `service_role` key | Usar a chave `anon/public` (diferente da service_role) |
| Mensagens inbound não aparecem no CRM | `phone_number_id` divergente entre Meta e n8n | Conferir nos logs do n8n |
| Cadastro: `iframe 'socframe' não encontrado` | SOC não está aberto no Chrome CDP | Abrir Chrome com `--remote-debugging-port=9222`, logar no SOC e deixar na tela 337 |
| Cadastro: `PermissionError` no Google Sheets | Planilha não compartilhada com a service account | Compartilhar com o email do JSON como Leitor |
| Cadastro: `GOOGLE_SHEETS_ID não definido` | Variável ausente no `.env` | Adicionar `GOOGLE_SHEETS_ID` ao `.env` |

---


<div align="center">

Desenvolvido por **Herick Campos** para **SafeWork** · Maio de 2026

</div>
