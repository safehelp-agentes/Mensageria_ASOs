<div align="center">

# 📋 SafeWork — Automação de envio de ASOs

**Pipeline diário que busca ASOs (Atestados de Saúde Ocupacional) no SOC, valida assinatura digital, baixa os PDFs e despacha por WhatsApp Business para a empresa correspondente.**

![Status](https://img.shields.io/badge/status-em%20produção-success)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-proprietary-lightgrey)
![Plataforma](https://img.shields.io/badge/deploy-Ubuntu%2024.04-E95420?logo=ubuntu&logoColor=white)

[Arquitetura](ARCHITECTURE.md) · [Configuração](#configuração) · [Operação](#operação) · [Estrutura](#estrutura-do-projeto)

</div>

---

## ✨ O que o sistema faz

Toda execução repete o mesmo ciclo, atomicamente:

1. **Consulta** a Web Service do SOC para listar todas as empresas ativas da conta.
2. Para cada empresa, **busca os ASOs emitidos na data alvo** (hoje, `--ontem`, ou qualquer data via `--data DD/MM/AAAA`).
3. **Filtra duplicatas e ASOs já enviados** consultando o histórico no Supabase.
4. **Separa por status de assinatura digital** — só ASOs assinados seguem para envio; os pendentes ficam registrados.
5. **Baixa o PDF** via SOAP/WS-Security (resposta multipart MTOM). ZIPs são desempacotados automaticamente.
6. **Resolve o destino** consultando os contatos da empresa no SOC.
7. **Envia pelo WhatsApp Business (Meta Cloud API)** — primeiro PDF via template aprovado (abre a janela de 24h), demais como documento simples dentro da mesma conversa (sem custo extra).
8. **Persiste o resultado** em três lugares: Supabase (`asos_enviados`, `mensagens`, `empresas`), Google Sheets (volumetria) e e-mail (relatório de erros).

> 📐 Para o fluxo completo com diagrama, integrações e contratos de dados, veja **[ARCHITECTURE.md](ARCHITECTURE.md)**.

---

## 🧰 Stack

| Camada | Tecnologia |
|---|---|
| Runtime | **Python 3.10+** |
| Fonte dos ASOs | **SOC** (Web Service REST + SOAP/WS-Security) |
| Mensageria | **Meta Cloud API** (WhatsApp Business v19.0) |
| Banco / CRM | **Supabase** (PostgreSQL) |
| Volumetria | **Google Sheets API** (Service Account + JWT) |
| Alertas | **Gmail SMTP** |
| Webhook inbound | **n8n** |
| Deploy | **VPS Hostinger Ubuntu 24.04** · `systemd` + `docker compose` |

**Dependências runtime** (`requirements.txt`):
```
python-dotenv>=1.0.0
requests>=2.31.0
cryptography>=41.0.0
```

---

## 🚀 Início rápido

### Pré-requisitos

- Python 3.10 ou superior
- Acesso a uma conta SOC com chaves de Exporta Dados e WS Download
- App Meta Business com template aprovado para envio de ASO
- Projeto Supabase com as tabelas descritas em [ARCHITECTURE.md](ARCHITECTURE.md#5-modelo-de-dados-supabase)
- Service Account do Google Cloud com permissão na planilha alvo

### Instalação

```bash
# Clone o repositório
git clone <repo-url> /opt/safework
cd /opt/safework

# Crie e ative o virtualenv
python3 -m venv .venv
source .venv/bin/activate

# Instale as dependências
pip install -r requirements.txt

# Configure as credenciais (veja .env.example)
cp .env.example .env
nano .env

# Coloque o service_account.json do Google Cloud na raiz
# (NÃO commitar — já está no .gitignore)
```

### Executando uma vez

```bash
# Modo seguro (envio só para o número de teste) — usa a data de hoje
python main.py

# Consulta ASOs emitidos ontem
python main.py --ontem

# Reprocessa (ou executa) um dia específico qualquer
python main.py --data "09/05/2026"
```

> ⚠ `--ontem` e `--data` são mutuamente exclusivos — usar os dois ao mesmo tempo gera erro.

> 💡 **Por padrão `ENVIO_REAL_EMPRESAS=false`**. Nenhum ASO chega ao número real até você alterar isso no `.env`. Existe ainda um *bloqueio de segurança* explícito em `main.py:_validar_numero_destino()` que aborta o envio se a flag estiver desativada e o número resolvido não for o de teste.

---

## ⚙️ Configuração

Todas as variáveis vivem no `.env` na raiz. Use `.env.example` como ponto de partida.

### SOC

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SOC_URL` | não | Endpoint REST do Exporta Dados. Padrão: `https://ws1.soc.com.br/WebSoc/exportadados` |
| `SOC_EMPRESA` | **sim** | Código da empresa principal no SOC |
| `SOC_CHAVE_EMPRESAS` | **sim** | Chave de Exporta Dados — código `192392` (lista de empresas) |
| `SOC_CHAVE_GED` | **sim** | Chave de Exporta Dados — código `191710` (GED / ASOs) |
| `SOC_CHAVE_CONTATOS` | **sim** | Chave de Exporta Dados — código `193815` (contatos) |
| `SOC_WS_USUARIO` | **sim** | Usuário do Web Service SOAP (download de PDFs) |
| `SOC_WS_PASSWORD` | **sim** | Senha do Web Service SOAP |

### Meta / WhatsApp

| Variável | Obrigatório | Descrição |
|---|---|---|
| `META_WA_TOKEN` | **sim** | Token Bearer do app Meta Business |
| `META_PHONE_NUMBER_ID` | **sim** | ID do número de WhatsApp |
| `META_TEMPLATE_NAME` | **sim** | Nome do template aprovado (ex.: `entrega_aso`) |
| `META_NUMERO_TESTE` | **sim** | Número que recebe todos os envios quando `ENVIO_REAL_EMPRESAS=false` |
| `META_ENVIAR` | não | `true` para realmente enviar. Padrão `false` |
| `META_TESTAR_SEM_ASO` | não | `true` para enviar mensagem de confirmação quando o dia não tem ASOs |
| `ENVIO_REAL_EMPRESAS` | não | **⚠ Trava de segurança.** `true` libera envio aos números reais. Padrão `false` |

### Supabase

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SUPABASE_URL` | sim* | URL do projeto Supabase |
| `SUPABASE_SECRET_KEY` | sim* | Service role key (chamada **secret key** na nova UI) |

\* O pipeline funciona sem Supabase, mas perde controle de duplicatas e CRM.

### Google Sheets

| Variável | Obrigatório | Descrição |
|---|---|---|
| `SHEETS_CREDENTIALS_FILE` | não | Caminho do JSON da Service Account. Padrão `./service_account.json` |
| `SHEETS_SPREADSHEET_ID` | sim* | ID da planilha (parte do URL entre `/d/` e `/edit`) |
| `SHEETS_ABA` | não | Nome da aba. Padrão `ASOs` |
| `SHEETS_ENVIAR` | não | `true`/`false`. Padrão `true` |

### E-mail (relatório de erros)

| Variável | Obrigatório | Descrição |
|---|---|---|
| `EMAIL_REMETENTE` | sim* | Conta Gmail que envia |
| `EMAIL_SENHA_APP` | sim* | [App Password](https://myaccount.google.com/apppasswords) do Gmail (não a senha normal) |
| `EMAIL_DESTINO` | sim* | Quem recebe o relatório |
| `EMAIL_ENVIAR` | não | `true`/`false`. Padrão `false` |

---

## 🔁 Operação

### Execução agendada (cron)

```cron
# Roda todo dia útil às 18h
0 18 * * 1-5 cd /opt/safework && /opt/safework/.venv/bin/python main.py >> /var/log/safework/aso.log 2>&1

# Pega o que ficou de ontem, todo dia às 8h
0 8 * * 1-5 cd /opt/safework && /opt/safework/.venv/bin/python main.py --ontem >> /var/log/safework/aso.log 2>&1
```

### Reprocessar um dia específico

Para repetir a execução de uma data qualquer (por erro, reprocessamento ou validação):

```bash
python main.py --data "09/05/2026"
```

O script usa exatamente essa data como `data_inicio` e `data_fim` na consulta ao SOC. ASOs já marcados como enviados no Supabase continuam sendo filtrados normalmente.

### Deploy de novas versões

```bash
./deploy.sh
```

O script faz `git pull`, atualiza dependências e reinicia o serviço `webhook-aso` no docker compose do n8n.

### Inspeção de execução

| Saída | Onde fica |
|---|---|
| Listagem JSON dos ASOs do dia | `output/saida_asos/asos_DD-MM-YYYY.json` |
| Resumo da execução | `output/saida_asos/resumo_execucao.json` |
| PDFs baixados (temporário) | `output/temp_asos/<código> - <nome>/` |
| Debug de respostas SOAP problemáticas | `output/debug_downloads/` |

> ⚠ A pasta `output/temp_asos` é **limpa a cada execução** (`shutil.rmtree` no início do `main()`).

---

## 📁 Estrutura do projeto

```
safework/
├── main.py                      # 🎯 Orquestrador — fluxo completo em 8 etapas numeradas
├── config.py                    # 🔧 Carrega .env, expõe constantes globais
├── deploy.sh                    # 🚢 git pull + pip install + restart docker
├── chat.html                    # 💬 CRM single-file (ver repositório do CRM)
├── requirements.txt
├── .env                         # 🔒 NÃO commitado
├── .env.example                 # 📋 Template público das variáveis
├── service_account.json         # 🔒 NÃO commitado (Google)
│
├── src/
│   ├── soc/
│   │   ├── api.py              # Cliente REST do Exporta Dados
│   │   │                       #   - buscar_empresas / buscar_asos_empresa
│   │   │                       #   - buscar_contatos_empresa
│   │   │                       #   - esta_assinado_digitalmente
│   │   └── downloader.py       # Cliente SOAP com WS-Security para download de PDFs
│   │                           #   - Gera PasswordDigest + Nonce
│   │                           #   - Parser de resposta MTOM (multipart/related)
│   │
│   ├── meta/
│   │   └── whatsapp.py         # Cliente Meta Cloud API
│   │                           #   - _fazer_upload_pdf (retorna media_id)
│   │                           #   - enviar_template_com_pdf
│   │                           #   - _enviar_documento_simples
│   │                           #   - enviar_pdfs_empresa_meta (orquestrador 1-conversa)
│   │
│   ├── pipeline/
│   │   └── processor.py        # Coleta + download em lote
│   │                           #   - coletar_asos_por_data
│   │                           #   - agrupar_por_empresa
│   │                           #   - baixar_pdfs_empresa (PDF direto ou ZIP→PDF)
│   │
│   ├── state/
│   │   └── manager.py          # Lógica de deduplicação e separação
│   │                           #   - chave_aso(CD_EMPRESA|CD_GED|CD_ARQUIVO_GED)
│   │                           #   - filtrar_nao_enviados
│   │                           #   - separar_por_assinatura
│   │
│   ├── integrations/
│   │   ├── supabase.py         # REST PostgREST — empresas, asos_enviados, mensagens
│   │   ├── sheets.py           # JWT manual + Sheets append
│   │   └── email.py            # SMTP Gmail (relatório de erros)
│   │
│   └── utils/
│       └── helpers.py          # Núcleo compartilhado
│                               #   - _requisicao_com_retry (backoff exponencial)
│                               #   - normalizar_numero_whatsapp (+55, sem zeros)
│                               #   - payload_tipo (detecta PDF/ZIP por magic bytes)
│                               #   - sanitizar_nome, registrar_erro
│                               #   - obter_data_consulta (hoje / ontem / data livre)
│
└── output/                      # Gerado em runtime — não commitar
    ├── temp_asos/              # PDFs baixados (limpo a cada execução)
    ├── saida_asos/             # JSONs de listagem e resumo
    └── debug_downloads/        # Dumps de respostas SOC com erro
```

---

## 🛡️ Segurança e boas práticas

### Já implementado

- ✅ **Trava de envio real** — `ENVIO_REAL_EMPRESAS=false` por padrão; bloqueio explícito em `_validar_numero_destino()`.
- ✅ **Deduplicação por chave composta** `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` consultada no Supabase antes de cada envio.
- ✅ **Retry com backoff exponencial** em todas as requisições HTTP (`_requisicao_com_retry`).
- ✅ **WS-Security PasswordDigest** com nonce aleatório e timestamp de 5min para o SOAP do SOC.

### Auditoria rápida

```bash
# Confirma que nada sensível foi commitado
git ls-files | grep -E '\.env$|service_account|\.pem$|\.key$'
git log --all --full-history --oneline -- .env service_account.json
```

---

## 🩺 Troubleshooting

| Sintoma | Causa provável | Onde investigar |
|---|---|---|
| `SOC_EMPRESA não definido no .env` | `.env` ausente ou variável vazia | `config.py` carrega o `.env` da pasta atual ou de qualquer pasta-pai |
| `codigoMensagem != SOC-100` | Credenciais WS, chave GED inválida ou empresa sem permissão | Dump em `output/debug_downloads/<chave>_*.{txt,json,bin}` |
| `Payload em formato inesperado` | SOC devolveu HTML de erro em vez do PDF | Mesmo dump acima |
| `Erro upload PDF Meta: HTTP 401` | Token Meta expirado | Renove em developers.facebook.com → seu app → WhatsApp |
| `Erro envio template Meta: HTTP 400` | Template não aprovado ou nome errado em `META_TEMPLATE_NAME` | Painel da Meta → Templates |
| `BLOQUEIO DE SEGURANÇA: empresa X usaria número real` | A trava está funcionando — não é bug | Ative `ENVIO_REAL_EMPRESAS=true` quando quiser produção |
| `[SUPABASE] Erro upsert empresa` HTTP 401/403 | Secret key inválida ou RLS bloqueando | Confira chave em `supabase.com/dashboard/project/_/settings/api` |
| Mensagens inbound não aparecem no CRM | `phone_number_id` não bate com o configurado no n8n | Logs do n8n |

---

## 🗺️ Roadmap (sugerido, não comprometido)

- [ ] Migrar `print()` para `logging` com níveis (INFO/WARNING/ERROR) e rotação.
- [ ] Suíte de testes mínima cobrindo `helpers.py`, `state/manager.py`, parser SOAP.
- [ ] Containerizar o `main.py` em vez de rodar via cron + venv.
- [ ] Retentativa automática de ASOs em status `pendente` quando passarem a `assinado=true`.
- [ ] Métricas Prometheus + dashboard Grafana (envios/dia, taxa de erro, latência por etapa).

---

## 📜 Convenções do código

- **Português** em nomes de funções, variáveis e mensagens (escolha consciente — domínio é brasileiro: SOC, ASO, CNPJ).
- **Camadas isoladas**: `src/soc/` não conhece Meta; `src/meta/` não conhece SOC. O `main.py` é o único ponto que orquestra tudo.
- **Erros não interrompem o lote**: falha de uma empresa registra em `erros_execucao` e segue para a próxima. Erros graves do orquestrador caem no `except` final que ainda dispara o email.
- **Estado é externo**: o pipeline em si é sem estado entre execuções — toda persistência mora no Supabase ou nos arquivos de `output/`.

---

<div align="center">

**Mantenedor**: equipe SafeWork · **Última revisão da documentação**: maio de 2026

Encontrou um bug ou tem uma dúvida? Abra uma issue no repositório interno.

</div>
