# Contexto da aplicação SafeWork — CRM ASOs

Cole este documento no início de uma nova conversa com o Claude para passar o contexto completo do projeto.

---

## O que é este projeto

Sistema da SafeWork que automatiza o envio de ASOs (Atestados de Saúde Ocupacional) via WhatsApp para empresas clientes. Tem dois componentes principais:

1. **Backend Python** — roda em VPS Linux (cron diário). Consulta a API SOAP do SOC, baixa os PDFs dos ASOs assinados digitalmente e os envia via API do WhatsApp (Meta). Registra tudo no Supabase.
2. **Frontend HTML** — CRM single-page (`index.html`) hospedado no Netlify. Lê dados do Supabase via JS e exibe um dashboard + tabela de ASOs.

---

## Stack e serviços externos

| Componente | Tecnologia |
|---|---|
| Backend | Python 3.12, venv, argparse |
| Frontend | HTML/CSS/JS puro (sem framework), Chart.js 4, Supabase JS SDK v2 |
| Hospedagem frontend | Netlify (static site + serverless functions) |
| Banco de dados | Supabase (PostgreSQL) |
| WhatsApp | Meta Cloud API (Graph API v19.0) |
| Documentos | SOC Web Services — SOAP com WS-Security (PasswordDigest) |
| Email (erros) | Gmail SMTP (smtplib) |

---

## Estrutura de arquivos

```
projeto_aso_envio/
├── main.py                         # Entrypoint do backend — orquestra tudo
├── config.py                       # Configurações lidas do .env
├── index.html                      # Frontend CRM completo (single file)
├── netlify.toml                    # publish=".", functions="netlify/functions", NODE_VERSION=20
├── netlify/
│   └── functions/
│       └── enviar-mensagem.js      # Serverless function — proxy p/ Meta API (texto simples)
├── src/
│   ├── soc/
│   │   ├── api.py                  # Consultas SOC: empresas, ASOs, contatos
│   │   └── downloader.py           # Download GED via SOAP multipart/related
│   ├── meta/
│   │   └── whatsapp.py             # Upload PDF + envio template/documento pela Meta API
│   ├── pipeline/
│   │   └── processor.py            # Coleta ASOs, download PDFs, agrupamento por empresa
│   ├── state/
│   │   └── manager.py              # Filtra enviados, separa assinados/não-assinados
│   ├── integrations/
│   │   ├── supabase.py             # CRUD Supabase via REST (sem SDK Python)
│   │   └── email.py                # Envio de email de erros via Gmail SMTP
│   └── utils/
│       └── helpers.py              # Utilitários: normalizar número, retry, sanitizar nome...
└── output/                         # Gerado em runtime (gitignore)
    ├── temp_asos/                  # PDFs baixados temporariamente (apagados a cada run)
    ├── debug_downloads/            # XMLs de debug do SOAP
    └── saida_asos/                 # JSON com listagem de ASOs e resumo_execucao.json
```

---

## Fluxo do backend (`main.py`)

```
1. Busca chaves já enviadas no Supabase (evita reenvio)
2. Consulta SOC: lista todas empresas → para cada uma busca ASOs da data
3. Salva listagem JSON em output/saida_asos/
4. Filtra os não enviados
5. Separa: assinados digitalmente (prontos) vs não-assinados (pendentes)
6. Registra pendentes no Supabase (enviado=False, assinado=False)
7. Para cada empresa com ASOs assinados:
   a. Baixa PDFs do GED via SOAP
   b. Busca telefone de contato no SOC
   c. Faz upsert da empresa no Supabase
   d. Envia PDFs via Meta API (1º PDF = template, demais = documento simples)
   e. Marca como enviado no Supabase
8. Revisita pendentes de execuções anteriores (verifica se assinaram)
9. Salva resumo JSON
10. Envia email com erros (se houver)
```

**Como rodar:**
```bash
# No VPS Linux
cd /opt/safework
source .venv/bin/activate
python -u main.py --ontem          # consulta d-1
python -u main.py --data 09/05/2026  # data específica

# Cron (exemplo atual):
# 0 8 * * 1-5 cd /opt/safework && .venv/bin/python -u main.py --ontem >> output/cron.log 2>&1
```

---

## Supabase — tabelas relevantes

### `asos_enviados`
| Coluna | Tipo | Descrição |
|---|---|---|
| `chave_aso` | text (PK) | Chave única: `{CD_EMPRESA}_{CD_GED}_{CD_ARQUIVO_GED}` |
| `codigo_empresa` | text | Código SOC da empresa |
| `nome_empresa` | text | Razão social / nome abreviado |
| `data_emissao` | date | Data do ASO (YYYY-MM-DD) |
| `data_envio` | date | Data que foi enviado (YYYY-MM-DD) |
| `enviado` | boolean | True = WhatsApp enviado com sucesso |
| `assinado` | boolean | True = assinatura digital confirmada |
| `numero_destino` | text | Número WhatsApp que recebeu |
| `wamid` | text | ID da mensagem retornado pela Meta |
| `status` | text | "pendente" / "enviado" |
| `created_at` | timestamptz | Auto |

### `empresas`
| Coluna | Tipo | Descrição |
|---|---|---|
| `codigo` | text (PK) | Código SOC |
| `nome` | text | Nome da empresa |
| `cnpj` | text | CNPJ |
| `telefone` | text | Número WhatsApp coletado do SOC |

### `mensagens`
Registra mensagens outbound (PDF enviado) e inbound (respostas recebidas via n8n).
Colunas: `codigo_empresa`, `nome_empresa`, `numero_whatsapp`, `direcao` (inbound/outbound), `tipo`, `conteudo`, `nome_arquivo`, `wamid`, `timestamp_meta`.

---

## Frontend (`index.html`)

Single-page, sem build step. Carrega via CDN:
- `@supabase/supabase-js@2` (UMD)
- `chart.js@4` (UMD)
- Google Fonts (Inter)

**Configuração:** URL e chave do Supabase ficam em `localStorage` (`sw_url`, `sw_key`). Há valores default hardcoded no JS (chave anon pública — sem risco).

**Views:**
- **Dashboard** — 5 KPIs: Total Enviados, Pendentes, Prontos para Envio, Média Diária de Envio, Empresas Atendidas. Gráfico de barras (últimos 30 dias). Filtros: período de datas + busca por empresa.
- **ASOs** — Tabela paginada com filtros por aba (Todos / Enviados / Pendentes) e busca por texto.

**Estado JS global:**
```js
const TZ = 'America/Sao_Paulo';
let cfg, sb;                     // config e cliente Supabase
let todosASOs = [];              // cache dos registros da tabela asos_enviados
let asoFiltroTab = 'todos';      // filtro ativo na aba ASOs
let dashData = null;             // cache para o dashboard
let dashChart = null;            // instância Chart.js
```

**Fluxo de dados:**
- `carregarDashboard()` → SELECT asos_enviados (limit 2000) → `renderDashboard()`
- `carregarASOs()` → SELECT asos_enviados (limit 5000) → `aplicarFiltrosASO()` → `renderASOs()`
- Polling a cada 60s em ambas as views

---

## Netlify function (`netlify/functions/enviar-mensagem.js`)

Proxy POST para a Meta Graph API. Recebe `{ numero, texto }` e envia mensagem de texto simples (não template). Variáveis de ambiente necessárias no Netlify: `META_WA_TOKEN` e `META_PHONE_NUMBER_ID`.

Endpoint: `/.netlify/functions/enviar-mensagem`

---

## Variáveis de ambiente (`.env` no VPS / Netlify)

```
# SOC
SOC_URL=https://ws1.soc.com.br/WebSoc/exportadados
SOC_EMPRESA=<código empresa principal>
SOC_CHAVE_EMPRESAS=<chave>
SOC_CHAVE_GED=<chave>
SOC_WS_USUARIO=<usuário WS>
SOC_WS_PASSWORD=<senha WS>
SOC_CODIGO_EMPRESA_PRINCIPAL=289501
SOC_CODIGO_RESPONSAVEL=104404
SOC_CODIGO_USUARIO=3604573

# Meta WhatsApp
META_WA_TOKEN=<bearer token>
META_PHONE_NUMBER_ID=1093647177168631
META_TEMPLATE_NAME=<nome do template aprovado>
META_NUMERO_TESTE=<número para testes>
META_ENVIAR=true

# Supabase (backend Python)
SUPABASE_URL=https://sjtjldxvjjjadtckhfkp.supabase.co
SUPABASE_SECRET_KEY=<service role key>

# Email erros
EMAIL_REMETENTE=<gmail>
EMAIL_SENHA_APP=<app password>
EMAIL_DESTINO=<destinatário>
EMAIL_ENVIAR=true

# Comportamento
ENVIO_REAL_EMPRESAS=true   # false = tudo vai para META_NUMERO_TESTE
USAR_ONTEM=true
```

---

## Decisões e convenções importantes

- **Sem reenvio automático**: se `chave_aso` já está em `asos_enviados` com `enviado=True`, nunca sobrescreve.
- **1 conversa por empresa**: 1º PDF abre com template (custo de conversa), PDFs adicionais são enviados como documento simples dentro da janela de 24h.
- **Chave única ASO**: `{CD_EMPRESA}_{CD_GED}_{CD_ARQUIVO_GED}` — gerada em `src/state/manager.py::chave_aso()`.
- **Números WhatsApp**: sempre normalizados para `55{DDD}{numero}` sem símbolos via `normalizar_numero_whatsapp()`.
- **SOC SOAP download**: resposta é multipart/related — o XML aponta via `href="cid:..."` qual parte binária é o arquivo. Veja `src/soc/downloader.py`.
- **Frontend sem build**: qualquer mudança no `index.html` é commitada e o Netlify faz o deploy automaticamente (publish=".").
- **Design tokens**: CSS usa variáveis `--ink-*`, `--accent`, `--brand`, `--amber-*`, `--rose-*` definidas em `:root`.

---

## O que NÃO existe ainda (possíveis próximos módulos)

- Painel de contatos/telefones por empresa (edição manual)
- Histórico de conversas WhatsApp inbound no frontend
- Agendamento de reenvio manual com data programada
- Notificações push / alertas em tempo real
- Autenticação no frontend (hoje qualquer um com a URL acessa)
