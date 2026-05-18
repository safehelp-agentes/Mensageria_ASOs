<div align="center">

<img src="https://img.shields.io/badge/status-produção-22c55e?style=flat-square" alt="Em produção">
<img src="https://img.shields.io/badge/python-3.10+-3776AB?style=flat-square&logo=python&logoColor=white" alt="Python">
<img src="https://img.shields.io/badge/WhatsApp_Business-API-25D366?style=flat-square&logo=whatsapp&logoColor=white" alt="WhatsApp">
<img src="https://img.shields.io/badge/Supabase-PostgreSQL-3ECF8E?style=flat-square&logo=supabase&logoColor=white" alt="Supabase">
<img src="https://img.shields.io/badge/deploy-Ubuntu_24.04-E95420?style=flat-square&logo=ubuntu&logoColor=white" alt="Ubuntu">

<br><br>

# SafeWork — Automação de ASOs via WhatsApp

**Pipeline de produção que conecta um sistema legado de saúde ocupacional (SOC) à API do WhatsApp Business, entregando documentos PDF automaticamente para centenas de empresas clientes.**

[Arquitetura detalhada](ARCHITECTURE.md) · [Configuração](#configuração) · [Como rodar](#início-rápido)

</div>

---

## O problema que resolve

Empresas de saúde ocupacional emitem **ASOs (Atestados de Saúde Ocupacional)** diariamente para seus clientes. O fluxo manual — exportar PDF do sistema, encontrar o contato da empresa, enviar — tomava horas e dependia de ação humana.

Este sistema elimina esse processo: todo dia útil um pipeline roda automaticamente, busca os documentos, valida, e entrega cada ASO no WhatsApp do RH da empresa certa — sem intervenção humana.

---

## Visão geral da arquitetura

```
┌─────────────────────────────────────────────────────────┐
│                    VPS (cron diário)                    │
│                                                         │
│  main.py ──► SOC REST API ──► lista empresas + ASOs     │
│      │                                                  │
│      ├──► SOC SOAP/WS-Security ──► download PDFs        │
│      │         (MTOM multipart, ZIPs extraídos)         │
│      │                                                  │
│      ├──► Supabase ──► dedup / estado / CRM             │
│      │                                                  │
│      └──► Meta Cloud API ──► WhatsApp Business          │
│               (1 template + N documentos = 1 conversa) │
└─────────────────────────────────────────────────────────┘

Respostas das empresas ──► Meta webhook ──► n8n ──► Supabase
                                                        │
                                               CRM (index.html)
```

> Diagrama completo com sequências e modelo de dados em [ARCHITECTURE.md](ARCHITECTURE.md).

---

## Destaques técnicos

### Integração com sistema legado via SOAP/WS-Security
O SOC não tem REST para download — usa um Web Service SOAP com autenticação **WS-Security PasswordDigest** (SHA-1, nonce aleatório de 16 bytes, timestamp com janela de 5 minutos). A resposta chega como `multipart/related` com o PDF embutido via **MTOM** (referência por `Content-ID`). Implementado do zero em `src/soc/downloader.py`.

### Otimização de custo no WhatsApp Business
A Meta cobra por **conversa iniciada**. Para empresas com múltiplos ASOs no mesmo dia, enviar cada PDF separadamente multiplicaria o custo. A solução: o 1º PDF abre a conversa via *template aprovado* (cobrado), os demais chegam como *documentos simples* dentro da janela de 24h (gratuitos). Uma empresa com 5 ASOs paga como se tivesse enviado 1.

### CRM single-file sem backend
`index.html` é um CRM completo de ~1.600 linhas que roda direto no browser. Conecta ao Supabase via PostgREST, mostra histórico bidirecional de conversas, dashboard com gráficos (Chart.js), painel de ASOs e gestão de empresas — tudo sem servidor Node, sem framework, sem build step.

### Pipeline idempotente com deduplicação composta
Cada ASO tem uma chave natural `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` consultada no Supabase antes de qualquer processamento. Rodar o pipeline duas vezes no mesmo dia é seguro — nenhum documento é reenviado.

### Blacklist e override de contatos via CRM
Empresas que optam por não receber mensagens são bloqueadas pelo operador no CRM (toggle na interface). O pipeline consulta essa configuração antes de processar — empresas bloqueadas são removidas do lote antes de qualquer download, economizando tempo e requisições. O campo "telefone escolhido" permite sobrescrever o número que vem do SOC sem alterar o sistema de origem.

---

## Stack

| Camada | Tecnologia | Por quê |
|---|---|---|
| Pipeline | **Python 3.10+** | Legibilidade, ecossistema de requests/crypto |
| Sistema de origem | **SOC** (REST + SOAP WS-Security) | Sistema legado do cliente — sem alternativa |
| Mensageria | **Meta Cloud API v19.0** | Única forma oficial de WhatsApp Business em escala |
| Banco / estado | **Supabase** (PostgreSQL + PostgREST) | REST nativo, realtime para o CRM, auth integrada |
| Webhook inbound | **n8n** | Orquestração visual, fácil de manter por não-devs |
| Deploy | **VPS Ubuntu 24.04** + cron + systemd | Sem overhead de K8s para um job diário simples |
| CRM | **HTML/CSS/JS vanilla** | Zero dependências, zero build, funciona em qualquer CDN |
| Alertas | **Gmail SMTP** | Relatório de erros sem custo adicional |

**Dependências Python** (intencionalmente mínimas):
```
python-dotenv  — carrega .env
requests       — HTTP unificado (REST + SOAP)
cryptography   — SHA-1 + base64 para WS-Security PasswordDigest
```

---

## Início rápido

### Pré-requisitos

- Python 3.10+
- Conta SOC com chaves Exporta Dados (`192392`, `191710`, `193815`) e credenciais WS SOAP
- App Meta Business com template aprovado para documento
- Projeto Supabase com as tabelas do [modelo de dados](ARCHITECTURE.md#5-modelo-de-dados-supabase)

### Instalação

```bash
git clone <repo-url> /opt/safework
cd /opt/safework

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env          # preencha as variáveis (seção abaixo)
```

### Executando

```bash
# Modo seguro — envia só para o número de teste, usa data de hoje
python main.py

# Reprocessa ASOs emitidos ontem
python main.py --ontem

# Reprocessa uma data específica
python main.py --data "09/05/2026"
```

> **Por padrão `ENVIO_REAL_EMPRESAS=false`** — nenhum documento chega ao número real até você ativar essa flag. Há ainda um bloqueio explícito em `_validar_numero_destino()` que aborta o envio se a flag estiver desativada e o número destino não for o de teste.

---

## Configuração

Todas as variáveis vivem no `.env`. Use `.env.example` como base.

### SOC

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SOC_EMPRESA` | sim | Código da empresa principal |
| `SOC_CHAVE_EMPRESAS` | sim | Chave exportador `192392` |
| `SOC_CHAVE_GED` | sim | Chave exportador `191710` (ASOs) |
| `SOC_CHAVE_CONTATOS` | sim | Chave exportador `193815` |
| `SOC_WS_USUARIO` | sim | Usuário SOAP |
| `SOC_WS_PASSWORD` | sim | Senha SOAP |

### Meta / WhatsApp

| Variável | Obrigatório | Descrição |
|---|---|---|
| `META_WA_TOKEN` | sim | Token Bearer do app Meta Business |
| `META_PHONE_NUMBER_ID` | sim | ID do número WhatsApp |
| `META_TEMPLATE_NAME` | sim | Nome do template aprovado |
| `META_NUMERO_TESTE` | sim | Número de destino quando `ENVIO_REAL_EMPRESAS=false` |
| `META_ENVIAR` | não | `true` para enviar de fato. Padrão: `false` |
| `ENVIO_REAL_EMPRESAS` | não | `true` libera envio aos números reais. Padrão: `false` |

### Supabase

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SUPABASE_URL` | sim* | URL do projeto |
| `SUPABASE_SECRET_KEY` | sim* | Service role key |

\* Sem Supabase o pipeline roda, mas sem deduplicação nem CRM.

### Alertas por e-mail

| Variável | Descrição |
|---|---|
| `EMAIL_REMETENTE` | Conta Gmail que envia o relatório |
| `EMAIL_SENHA_APP` | [App Password](https://myaccount.google.com/apppasswords) do Gmail |
| `EMAIL_DESTINO` | Destinatário do relatório de erros |
| `EMAIL_ENVIAR` | `true`/`false`. Padrão: `false` |

---

## Operação

### Agendamento via cron

```cron
# Envia ASOs do dia atual às 18h (dias úteis)
0 18 * * 1-5 cd /opt/safework && .venv/bin/python main.py >> /var/log/safework/aso.log 2>&1

# Captura o que ficou de ontem às 8h
0 8  * * 1-5 cd /opt/safework && .venv/bin/python main.py --ontem >> /var/log/safework/aso.log 2>&1
```

### Saídas de cada execução

| Arquivo | Conteúdo |
|---|---|
| `output/saida_asos/asos_DD-MM-YYYY.json` | Todos os ASOs encontrados no SOC |
| `output/saida_asos/resumo_execucao.json` | Status por empresa (downloads, envios, erros) |
| `output/debug_downloads/` | Dumps de respostas SOAP problemáticas (evidência forense) |

> `output/temp_asos/` é apagado no início de cada execução — PDFs não ficam no disco.

### Deploy

```bash
./deploy.sh    # git pull + pip install + restart docker (n8n)
```

---

## Estrutura do projeto

```
safework/
├── main.py                  # Orquestrador — 7 etapas explicitamente numeradas
├── config.py                # Carrega .env, expõe constantes
├── index.html               # CRM completo single-file (sem build)
│
└── src/
    ├── soc/
    │   ├── api.py           # Cliente REST Exporta Dados
    │   └── downloader.py    # Cliente SOAP WS-Security + parser MTOM
    │
    ├── meta/
    │   └── whatsapp.py      # Upload + envio template + documentos (1 conversa)
    │
    ├── pipeline/
    │   └── processor.py     # Coleta em lote, download, extração ZIP
    │
    ├── state/
    │   └── manager.py       # Chave de identidade, deduplicação
    │
    ├── integrations/
    │   ├── supabase.py      # PostgREST — empresas, asos_enviados, mensagens
    │   └── email.py         # Relatório de erros via SMTP
    │
    └── utils/
        └── helpers.py       # Retry com backoff, normalização de números, detecção PDF/ZIP
```

---

## Segurança

- **Trava de envio real** — `ENVIO_REAL_EMPRESAS=false` por padrão com bloqueio explícito no código, não apenas em config.
- **Deduplicação idempotente** — chave composta `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` consultada no Supabase antes de qualquer operação.
- **WS-Security com nonce único** — cada chamada SOAP gera 16 bytes aleatórios; tokens expiram em 5 minutos.
- **Retry seletivo** — backoff exponencial (2s/4s/8s) só em erros 5xx e de transporte; erros 4xx falham imediatamente.
- **Credenciais fora do repositório** — `.env` no `.gitignore`; `.env.example` documenta as variáveis sem valores.

```bash
# Auditoria rápida
git ls-files | grep -E '\.env$|\.key$|\.pem$'
git log --all --oneline -- .env
```

---

## Troubleshooting

| Sintoma | Causa provável | Como resolver |
|---|---|---|
| `codigoMensagem != SOC-100` | Credenciais SOAP ou chave GED inválida | Inspecionar `output/debug_downloads/<chave>_xml.txt` |
| `Payload em formato inesperado` | SOC devolveu HTML de erro em vez do PDF | Mesmo dump acima |
| `Erro upload PDF Meta: HTTP 401` | Token Meta expirado | Renovar em developers.facebook.com |
| `Erro envio template: HTTP 400` | Template não aprovado ou nome errado | Painel Meta → Message Templates |
| `BLOQUEIO DE SEGURANÇA` | Trava funcionando corretamente | Ativar `ENVIO_REAL_EMPRESAS=true` para produção |
| `[SUPABASE] Erro upsert: 403` | RLS bloqueando ou secret key errada | Verificar chave em supabase.com → Settings → API |
| Mensagens inbound não aparecem | `phone_number_id` divergente entre Meta e n8n | Conferir nos logs do n8n |

---

## Roadmap

- [ ] Migrar `print()` para `logging` com rotação de arquivos
- [ ] Testes unitários para `helpers.py`, `state/manager.py` e o parser SOAP
- [ ] Containerizar o pipeline em vez de cron + venv
- [ ] Métricas Prometheus + dashboard Grafana

---

<div align="center">

Desenvolvido por **Herick Campos** para **SafeWork** · Maio de 2026

</div>
