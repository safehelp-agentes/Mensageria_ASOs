# 🏛️ Arquitetura — SafeWork ASO

Este documento descreve **como** o pipeline funciona por dentro: o fluxo de dados, os contratos entre módulos, o modelo de dados externo e os pontos de falha esperados.

Para visão de produto e como rodar, veja o [README.md](README.md).

---

## 📑 Sumário

1. [Diagrama de contexto](#1-diagrama-de-contexto)
2. [Fluxo de execução do `main.py`](#2-fluxo-de-execução-do-mainpy)
3. [Estados de um ASO](#3-estados-de-um-aso)
4. [Camadas e responsabilidades](#4-camadas-e-responsabilidades)
5. [Modelo de dados (Supabase)](#5-modelo-de-dados-supabase)
6. [Integração com a API do SOC](#6-integração-com-a-api-do-soc)
7. [Estratégia de envio WhatsApp](#7-estratégia-de-envio-whatsapp)
8. [Webhook inbound (Meta → CRM)](#8-webhook-inbound-meta-crm)
9. [Tratamento de erros e retries](#9-tratamento-de-erros-e-retries)
10. [Decisões de design](#10-decisões-de-design)

---

## 1. Diagrama de contexto

```mermaid
graph LR
    subgraph SafeWork["🖥️ VPS Hostinger — /opt/safework"]
        Cron[⏰ cron] --> Main[main.py]
        Webhook[webhook_meta.py<br/>:8001]
    end

    subgraph Externos["☁️ Serviços externos"]
        SOC[(📋 SOC<br/>Exporta Dados + WS)]
        Meta[(💬 Meta Cloud API<br/>WhatsApp Business)]
        SB[(🗄️ Supabase<br/>PostgreSQL)]
        Sheets[(📊 Google Sheets)]
        Gmail[(📧 Gmail SMTP)]
    end

    subgraph Consumo["👀 Consumo"]
        CRM[chat.html<br/>CRM SafeWork]
        Empresas[🏢 Empresas clientes<br/>WhatsApp]
    end

    Main -->|"1. lista empresas<br/>2. busca ASOs<br/>3. baixa PDFs"| SOC
    Main -->|"4. upload + send"| Meta
    Main -->|"5. persiste estado"| SB
    Main -->|"6. log volumetria"| Sheets
    Main -->|"7. relatório de erros"| Gmail
    Meta -->|"PDFs"| Empresas
    Empresas -->|"respostas"| Meta
    Meta -->|"webhook"| Webhook
    Webhook -->|"insert inbound"| SB
    SB -->|"select realtime"| CRM
```

---

## 2. Fluxo de execução do `main.py`

Toda execução é determinística e segue **oito etapas numeradas explicitamente no código-fonte**:

```mermaid
flowchart TD
    Start([🚀 python main.py]) --> Prep[Prepara diretórios<br/>limpa temp_asos]
    Prep --> E1[1. buscar_chaves_enviadas<br/>Supabase: asos_enviados WHERE enviado=true]
    E1 --> E2[2. coletar_asos_por_data<br/>SOC: para cada empresa ativa,<br/>busca ASOs da data]
    E2 --> Save[salvar_listagem_asos<br/>output/saida_asos/*.json]
    Save --> E3[3. filtrar_nao_enviados<br/>remove duplicatas + chaves já enviadas]
    E3 --> E4{4. separar_por<br/>assinatura}
    E4 -->|sem assinatura| E5[5. registrar_aso_pendente<br/>Supabase: enviado=false]
    E4 -->|assinados| E6
    E5 --> E6[6. agrupar_por_empresa]
    E6 --> Loop{Para cada<br/>empresa}
    Loop --> DL[baixar_pdfs_empresa<br/>SOC SOAP → PDF/ZIP]
    DL --> Contatos[buscar_contatos_empresa<br/>SOC: telefones]
    Contatos --> Resolve[resolver_destino_envio<br/>número real OU teste]
    Resolve --> Guard{ENVIO_REAL<br/>EMPRESAS?}
    Guard -->|false + número real| Abort[❌ BLOQUEIO<br/>DE SEGURANÇA]
    Guard -->|ok| Send[enviar_pdfs_empresa_meta<br/>1º PDF: template<br/>demais: documento]
    Send --> Mark[marcar_aso_enviado<br/>+ registrar_mensagem_outbound]
    Mark --> Loop
    Loop -->|fim do laço| E7[7. registrar_no_sheets<br/>volumetria por empresa]
    Abort --> Erro[registrar_erro]
    Erro --> Loop
    E7 --> E8[8. salvar resumo<br/>output/saida_asos/resumo_execucao.json]
    E8 --> Email[enviar_email_erros<br/>se houver erros]
    Email --> End([✅ fim])

    style Abort fill:#fee,stroke:#c33,stroke-width:2px
    style Send fill:#efe,stroke:#3a3
    style E1 fill:#eef
    style E2 fill:#eef
    style E3 fill:#eef
    style E4 fill:#eef
    style E5 fill:#eef
    style E6 fill:#eef
    style E7 fill:#eef
    style E8 fill:#eef
```

> 📌 Cada caixa azul é uma etapa numerada explicitamente no `main.py`. Use os números (`# ── N. Descrição ──`) para localizar.

---

## 3. Estados de um ASO

```mermaid
stateDiagram-v2
    [*] --> Descoberto: SOC retorna ASO
    Descoberto --> JaEnviado: chave_aso ∈ chaves_enviadas
    Descoberto --> Pendente: assinado=false
    Descoberto --> Pronto: assinado=true
    Pendente --> Pronto: assinatura concluída<br/>(próxima execução)
    Pronto --> EmDownload: baixar_pdfs_empresa
    EmDownload --> ErroDownload: SOAP falhou
    EmDownload --> EmEnvio: PDF salvo
    EmEnvio --> ErroEnvio: Meta retornou erro
    EmEnvio --> Enviado: HTTP 200 + wamid
    ErroDownload --> [*]: registrado em erros_execucao
    ErroEnvio --> [*]: registrado em erros_execucao
    Enviado --> [*]: asos_enviados.enviado=true
    JaEnviado --> [*]: ignorado (idempotência)

    note right of JaEnviado
        Garantia de idempotência:
        executar 2x no mesmo dia
        não duplica envio.
    end note

    note right of Pendente
        Próximas execuções vão re-buscar
        e mover para Pronto quando
        a assinatura digital for concluída.
    end note
```

**Chave de identidade**: `CD_EMPRESA | CD_GED | CD_ARQUIVO_GED` (campos do SOC). Construída em `src/state/manager.py:chave_aso()`.

---

## 4. Camadas e responsabilidades

Cada módulo tem uma **única** razão para existir. A regra: `src/soc/` não importa `src/meta/`, e vice-versa. O `main.py` é o único ponto que conhece todos.

```mermaid
graph TB
    Main[main.py<br/>🎯 Orquestrador]

    subgraph Fontes
        SOC[src/soc/<br/>📋 SOC]
        SOC_API[api.py<br/>REST Exporta Dados]
        SOC_DL[downloader.py<br/>SOAP WS-Security]
        SOC --> SOC_API
        SOC --> SOC_DL
    end

    subgraph Pipeline
        Proc[src/pipeline/processor.py<br/>🔄 Coleta + download em lote]
        State[src/state/manager.py<br/>🔑 Dedup + filtros]
    end

    subgraph Saidas
        Meta[src/meta/whatsapp.py<br/>💬 Meta API]
        Int[src/integrations/]
        SBint[supabase.py]
        Shint[sheets.py]
        Emint[email.py]
        Int --> SBint
        Int --> Shint
        Int --> Emint
    end

    Utils[src/utils/helpers.py<br/>🧰 Retry, números, datas]

    Main --> SOC
    Main --> Pipeline
    Main --> Meta
    Main --> Int
    Pipeline --> SOC
    Meta --> Utils
    SOC --> Utils
    Pipeline --> Utils
    Int --> Utils

    style Main fill:#fef3c7
    style Utils fill:#e0e7ff
```

### Por que essa separação importa

- **Trocar de provedor SOC** afeta só `src/soc/` (e o nome dos campos no `main.py`).
- **Trocar de WhatsApp para Telegram** afeta só `src/meta/` (e a flag de envio no `main.py`).
- **Adicionar uma nova integração de log** entra como mais um arquivo em `src/integrations/` sem mexer no resto.

---

## 5. Modelo de dados (Supabase)

### Tabela `empresas`

Cadastro mestre. Upsert em `upsert_empresa()` (PostgREST com `Prefer: resolution=merge-duplicates`, conflito em `codigo`).

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | int8 PK | |
| `codigo` | text **UNIQUE** | Código da empresa no SOC |
| `nome` | text | Razão social ou nome abreviado |
| `cnpj` | text | |
| `telefone` | text | Telefone normalizado WhatsApp (com `55`) |
| `created_at`, `updated_at` | timestamptz | |

### Tabela `asos_enviados`

Controle de quem foi enviado, quando, para quem. Único índice em `chave_aso` garante a idempotência.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | int8 PK | |
| `chave_aso` | text **UNIQUE** | `CD_EMPRESA\|CD_GED\|CD_ARQUIVO_GED` |
| `cd_empresa_soc` | text | Redundante para query, espelha parte da chave |
| `cd_ged`, `cd_arquivo_ged` | text | Idem |
| `codigo_empresa` | text FK → empresas.codigo | |
| `nome_empresa` | text | |
| `nome_arquivo` | text | Nome do PDF enviado |
| `data_envio` | date | YYYY-MM-DD |
| `data_emissao` | date | YYYY-MM-DD |
| `assinado` | bool | `true` quando enviado |
| `enviado` | bool | `true` apenas após sucesso na Meta |
| `numero_destino` | text | Para onde foi (real ou teste) |
| `wamid` | text | ID retornado pela Meta |
| `status` | text | `pendente` \| `enviado` |
| `created_at` | timestamptz | |

### Tabela `mensagens`

Histórico bidirecional para o CRM `chat.html`. Append-only.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | int8 PK | |
| `codigo_empresa` | text | Pode ser null para inbound de número não cadastrado |
| `nome_empresa` | text | Em inbound sem cadastro, vira o nome do perfil WhatsApp |
| `numero_whatsapp` | text | Chave de agrupamento das conversas no CRM |
| `direcao` | text | `inbound` \| `outbound` |
| `tipo` | text | `text` \| `document` \| `image` \| etc |
| `conteudo` | text | Mensagem ou descrição |
| `nome_arquivo` | text | Quando `tipo=document` |
| `wamid` | text | ID da Meta para correlação |
| `timestamp_meta` | int8 | Epoch ms do servidor Meta (inbound) |
| `created_at` | timestamptz | |

> ⚠️ **RLS recomendado**: ative Row Level Security e crie policies. A publishable key do CRM, sem RLS, deixa todas as conversas legíveis por qualquer um que tenha o URL do projeto.

---

## 6. Integração com a API do SOC

O SOC tem **duas portas distintas** que o sistema consome:

### 6.1. Exporta Dados (REST simples)

Endpoint: `https://ws1.soc.com.br/WebSoc/exportadados`

```http
GET /WebSoc/exportadados?parametro={"empresa":"289501","codigo":"192392","chave":"...","tipoSaida":"json"}
```

Usado para listar **empresas**, **ASOs** e **contatos** — três códigos diferentes:

| Tipo | Código exportador | Variável de chave |
|---|---|---|
| Empresas | `192392` | `SOC_CHAVE_EMPRESAS` |
| GED (ASOs) | `191710` | `SOC_CHAVE_GED` |
| Contatos | `193815` | `SOC_CHAVE_CONTATOS` |

Resposta de erro do SOC nunca vem como HTTP 4xx/5xx — vem como JSON `{"erro": true, "mensagem Erro": "..."}`. O cliente (`chamar_exporta_dados`) detecta isso e lança `RuntimeError`.

### 6.2. Download de GED (SOAP + WS-Security + MTOM)

Endpoint: `https://ws1.soc.com.br/WSSoc/DownloadArquivosWs`

Aqui está a parte cabeluda. O fluxo:

```mermaid
sequenceDiagram
    participant P as Pipeline
    participant D as downloader.py
    participant SOC as SOC SOAP

    P->>D: baixar_documento(cd_empresa, cd_ged, cd_arquivo)
    D->>D: gerar_wsse_password_digest()
    Note right of D: SHA1(nonce + created + senha)<br/>nonce = 16 bytes random base64
    D->>D: montar_xml_download_por_lote(...)
    D->>SOC: POST SOAP Envelope
    SOC-->>D: multipart/related<br/>(XML + binário MTOM)
    D->>D: _extrair_multipart()
    Note right of D: 1. Parse via email.message_from_bytes<br/>2. Extrai href="cid:..." do XML<br/>3. Acha parte binária com Content-ID match
    D->>D: payload_tipo() — magic bytes
    Note right of D: %PDF → 'pdf'<br/>PK\x03\x04 → 'zip'
    D->>D: Valida codigoMensagem == 'SOC-100'
    D-->>P: (payload_bytes, 'pdf'|'zip', filename)
```

**Pontos finos do parser SOAP:**

- Resposta vem em **multipart/related** com referência por `Content-ID` (MTOM).
- O XML aponta para o anexo via `href="cid:..."` — esse é o caminho preferencial para escolher qual parte é "o documento".
- Fallback: se não houver `href`, varre os anexos e escolhe a primeira parte cujos magic bytes sejam PDF ou ZIP.
- **ZIPs são extraídos em runtime** (`processor.py:baixar_pdfs_empresa`) — o SOC eventualmente devolve um ZIP contendo um único PDF.

**Quando dá errado**, todos os dados crus vão para `output/debug_downloads/`:

```
debug_downloads/
├── <chave>_xml.txt         # XML da resposta
├── <chave>_partes.json     # metadados de cada parte multipart
└── <chave>_payload.bin     # o binário extraído (ou nada)
```

---

## 7. Estratégia de envio WhatsApp

A Meta cobra por **conversa iniciada** dentro de uma janela de 24h. Para empresas com vários ASOs no mesmo dia, mandar cada PDF separadamente custaria N conversas. A estratégia minimiza isso para **1 conversa por empresa por dia**:

```mermaid
sequenceDiagram
    participant P as Pipeline
    participant M as Meta API
    participant E as Empresa

    Note over P: Empresa tem 3 ASOs no dia
    P->>M: POST /media (PDF 1)
    M-->>P: media_id_1
    P->>M: POST /messages (template + media_id_1)
    Note right of M: 💰 1 conversa iniciada<br/>(janela 24h aberta)
    M-->>E: 📄 Template + PDF 1
    Note over P: sleep 3s — espera Meta processar
    P->>M: POST /media (PDF 2)
    M-->>P: media_id_2
    P->>M: POST /messages (document + media_id_2)
    Note right of M: ✅ Dentro da janela = grátis
    M-->>E: 📄 PDF 2
    P->>M: POST /media (PDF 3)
    M-->>P: media_id_3
    P->>M: POST /messages (document + media_id_3)
    M-->>E: 📄 PDF 3
```

Implementado em `src/meta/whatsapp.py:enviar_pdfs_empresa_meta`. Se o primeiro envio (template) falhar, o restante **não** é tentado — falha cedo evita gastar a janela de 24h em conversa que não vai render.

---

## 8. Webhook inbound (Meta → CRM)

Mensagens que as empresas mandam de volta passam por **um dos dois caminhos** (não os dois):

```mermaid
graph LR
    Empresa[🏢 Empresa] -->|envia WhatsApp| Meta[(Meta API)]
    Meta -->|webhook callback| Choice{Qual entrada<br/>está ativa?}
    Choice -->|opção A| Webhook[webhook_meta.py<br/>:8001]
    Choice -->|opção B| N8N[n8n workflow]
    Webhook -->|INSERT mensagens| SB[(Supabase)]
    N8N -->|INSERT mensagens| SB
    SB -->|poll 8s| CRM[chat.html]
```

Os dois fazem essencialmente a mesma coisa:

1. Valida o `verify_token` no GET inicial da Meta.
2. No POST do callback, valida HMAC SHA256 (opcional) e o `phone_number_id` (filtro de segurança — ignora mensagens de outros números do mesmo app).
3. Tenta resolver o número remetente em `empresas.telefone` para preencher `codigo_empresa` e `nome_empresa`.
4. Insere em `mensagens` com `direcao=inbound`.

**Decisão a tomar em produção**: manter `webhook_meta.py` (servidor Python nativo, sem dependências) **ou** o n8n (mais visual, mais fácil de ramificar com lógica adicional). Não rodar os dois simultaneamente apontando para o mesmo número — dupliça as mensagens.

---

## 9. Tratamento de erros e retries

### Camada de transporte (`_requisicao_com_retry`)

```python
# src/utils/helpers.py
- 3 tentativas
- backoff exponencial: 2s, 4s, 8s
- retry em: ConnectionError, Timeout, HTTP 5xx
- NÃO retry em: HTTP 4xx (erros de cliente)
```

### Camada de domínio

Falhas em **uma empresa** não interrompem o lote:

```python
try:
    enviar_pdfs_empresa_meta(...)
except Exception as e:
    resultado["meta_erro"] = str(e)
    registrar_erro(f"Empresa {codigo_empresa} erro envio Meta: {e}")
# segue para a próxima empresa
```

Erros são acumulados em `erros_execucao` (lista global em `helpers.py`) e disparados por email no `finally` do `main`.

### Camada de orquestração

O `main()` inteiro está dentro de um `try/except/finally`:

```python
try:
    main(usar_ontem=args.ontem)
except Exception as e:
    registrar_erro(f"Erro geral na execução: {e}")
    raise           # propaga para o cron logar
finally:
    enviar_email_erros(erros_execucao)   # SEMPRE dispara o email
```

Resultado: **mesmo em crash catastrófico**, o relatório de erros sai.

---

## 10. Decisões de design

### Por que Português no código?

Domínio fortemente brasileiro: SOC, ASO, CNPJ, ESocial. Traduzir vira "Health Certificate" e perde o significado. Os campos da API do SOC já vêm em português (`EMPRESA_CONSULTADA`, `DT_EMISSAO`, `CD_GED`), então a base já está nesse idioma — misturar seria pior.

### Por que JWT manual no Sheets em vez de `google-auth`?

Para manter o `requirements.txt` minúsculo (3 libs). Service Account JWT é simples: header + payload + RSA-SHA256, e `cryptography` já estava lá para outra coisa. Adicionar `google-auth` traria dezenas de dependências transitivas para uma função.

### Por que `http.server` nativo no webhook?

Mesmo motivo. O endpoint tem três coisas: validar token, validar HMAC, fazer INSERT. FastAPI + uvicorn + Pydantic seria over-engineering para 95 linhas.

> Quando o webhook crescer (filas, validações complexas, múltiplos endpoints), migrar para FastAPI vale a pena. **Está no roadmap**.

### Por que PostgREST direto em vez do client `supabase-py`?

`supabase-py` traz mais dependências, faz pool de conexões que não precisamos para 1 execução/dia, e a API REST do PostgREST é suficiente. Cinco endpoints, todos com `requests.post/get` simples.

### Por que limpar `temp_asos/` a cada execução?

Garante reprodutibilidade: a próxima execução não vê PDFs de execuções anteriores. A pasta `debug_downloads/` **não** é limpa porque é evidência forense para troubleshooting.

### Por que separar `chave_aso` em três campos no banco?

A chave composta `CD_EMPRESA|CD_GED|CD_ARQUIVO_GED` é ótima para uniqueness, mas terrível para queries do tipo "todos os ASOs da empresa X". Separar permite indexar e filtrar individualmente sem parsing.

---

<div align="center">

Voltar ao [README.md](README.md)

</div>
