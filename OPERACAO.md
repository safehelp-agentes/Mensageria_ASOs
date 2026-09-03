# OPERAÇÃO — SafeWork ASO

Runbook de deploy e operação em produção. Complementa o [README.md](README.md) (produto) e o [ARCHITECTURE.md](ARCHITECTURE.md) (interno). Para o contexto rápido, [CLAUDE.md](CLAUDE.md).

> ⚠️ Boa parte do que está aqui **não vive no repositório** — as unidades systemd, a config do Traefik e o painel da Meta ficam na VPS / nos consoles externos. Este arquivo é a memória desse conhecimento; mantenha-o atualizado quando algo mudar.

## Infraestrutura

- **Servidor:** VPS Ubuntu 24.04 (Hostinger) — `srv1564091.hstgr.cloud`, IP `191.5.48.4`. DNS **wildcard** `*.srv1564091.hstgr.cloud` aponta para o mesmo IP (subdomínios novos já resolvem, sem configurar DNS).
- **Projeto:** `/opt/safework/envio_ASO/` (git). Venv em `.venv/` (precisa das deps de `requirements.txt`).
- **Docker / Traefik:** rede externa `n8n_default`; certresolver `mytlschallenge`; entrypoint `websecure`. O roteamento do n8n é por *path* em `n8n.srv1564091.hstgr.cloud`; o Inbox usa subdomínio próprio `inbox.srv1564091.hstgr.cloud`.
- **Banco:** Supabase (projeto `sjtjldxvjjjadtckhfkp` — *safework-crm*), PostgreSQL via PostgREST. Tabelas em uso: `asos_enviados` (estado de envio) e `mensagens` (Inbox). Não há tabela `empresas`.

## Atualizar o código

```bash
cd /opt/safework/envio_ASO
git pull origin main       # ou ./deploy.sh, que faz git pull + pip install -r requirements.txt
```

O pipeline não é serviço contínuo — o `git pull` basta e a próxima execução do timer já usa o código novo. O Inbox está montado por volume `:ro`, então também recebe o código novo no `git pull` (ver [Inbox](#deploy-do-inbox--crm) para quando é preciso `restart`/`rebuild`).

## Deploy do pipeline (systemd timer)

O agendamento é por **systemd timer** (substituiu o cron em jul/2026). Unidades em `/etc/systemd/system/`:

- `safework-aso.service` — `Type=oneshot`, `ExecStart=/opt/safework/envio_ASO/.venv/bin/python main.py`, **`TimeoutStartSec=0`**.
- `safework-aso.timer` — `OnCalendar` 07:30, 12:00, 19:00; `Persistent=true`.

> **`TimeoutStartSec=0` é obrigatório.** Sem ele o systemd mata o processo em ~90s (default) no meio do envio → o run manda ASOs mas não marca no Supabase → **reenvio no próximo run**. Foi assim que se observou duplicidade em testes.

Comandos do dia a dia:

```bash
systemctl start --no-block safework-aso.service   # roda agora sem travar o terminal
journalctl -u safework-aso -f                      # acompanha os logs em tempo real
systemctl list-timers safework-aso.timer           # confere os próximos disparos
```

> `systemctl start` de um `oneshot` **bloqueia** o terminal até o run terminar — é normal, use `--no-block`. **Cada `start` é envio real** (idempotente, mas real). Não rode de novo um run que morreu no meio sem antes verificar o que já foi marcado no Supabase — pode reenviar.

Os serviços antigos `envio-aso.service` / `webhook-meta.service` / `bot-aso.service` são do bot removido (estão mortos; podem ser ignorados ou removidos).

## Deploy do Inbox / CRM

Container FastAPI atrás do Traefik, host `https://inbox.srv1564091.hstgr.cloud`.

```bash
cd /opt/safework/envio_ASO/inbox
docker compose up -d --build
```

- Código montado por volume `:ro`:
  - `templates/index.html` é lido a cada request → muda na hora.
  - Mudança em `.py` exige `docker compose -f inbox/docker-compose.yml restart inbox`.
  - **Rebuild só quando mudar `inbox/requirements.txt`.**
- **Três rotas no mesmo serviço (roteamento Traefik):**
  - `/webhook` — **sem** Basic Auth (a Meta não manda credenciais), prioridade alta.
  - `/api/internal/*` — sem Basic Auth, protegido pelo `INBOX_INTERNAL_TOKEN` (Bearer).
  - resto — **Basic Auth** (middleware `inbox-auth`, usuário `safework`; hash apr1 no label `basicauth.users` do `docker-compose.yml`).
- **Pré-requisitos no `.env` da VPS:** `WEBHOOK_VERIFY_TOKEN` e `INBOX_INTERNAL_TOKEN`.

### Painel Meta (WhatsApp)

- Callback URL: `https://inbox.srv1564091.hstgr.cloud/webhook`
- Verify token: o mesmo valor de `WEBHOOK_VERIFY_TOKEN` do `.env`
- Assinar o campo **`messages`**. (A Meta permite 1 webhook por app; o pipeline não usa webhook, então o slot fica livre para o Inbox.)

### Integração interna (bot de agendamento → Inbox)

`POST /api/internal/mensagem` com `Authorization: Bearer <INBOX_INTERNAL_TOKEN>` espelha mensagens inbound/outbound de outras automações no mesmo histórico (`mensagens`). No n8n, configurar em `/docker/n8n/bot.env`:

```env
ASO_INBOX_SYNC_URL=https://inbox.srv1564091.hstgr.cloud/api/internal/mensagem
ASO_INBOX_SYNC_TOKEN=<mesmo valor do INBOX_INTERNAL_TOKEN>
```

Depois de editar `bot.env`, recrie os containers (um `restart` pode não recarregar variáveis novas):

```bash
cd /docker/n8n && docker compose up -d --no-deps --force-recreate n8n n8n-worker
```

## Post-mortem — reenvio de ASOs (jul/2026)

**Sintoma:** clientes reportaram receber ASOs repetidos; o número de "empresas processadas" no `journalctl -u safework-aso` crescia de forma anormal ao longo do dia (ex.: 32 → 59 → 67 → 84 nas execuções seguidas).

**Causa raiz:** `buscar_chaves_enviadas()` consultava `asos_enviados` **sem paginação nem filtro**. O Supabase/PostgREST tem um limite padrão de linhas por requisição (10.000 no projeto). Quando `asos_enviados` passou de 10.000, a consulta passou a trazer só 10.000 — **sem erro nenhum**, truncando em silêncio. Os ASOs "esquecidos" eram vistos como novos e reenviados a cada run.

**Correção aplicada:** filtrar a consulta pela **mesma janela de `data_emissao`** já usada para buscar no SOC (`JANELA_DIAS`, hoje 30 dias) — join lógico correto (todo ASO candidato já vem do SOC dentro dessa janela) e a janela não cresce com o histórico. Aplicado em `buscar_chaves_enviadas` e `buscar_asos_pendentes` (`src/integrations/supabase.py`); o cálculo da janela foi movido para antes da etapa 1 no `main.py`.

**Como detectar truncamento de novo:**

```bash
# total real (count exato)
curl ".../asos_enviados?enviado=eq.true&select=id" -H "Prefer: count=exact" -H "Range: 0-0" -I | grep -i content-range
# quanto uma query SEM Range/limit realmente traz
curl ".../asos_enviados?enviado=eq.true&select=chave_aso" | python3 -c "import sys,json;print(len(json.load(sys.stdin)))"
```

Se o 2º número for menor que o total do 1º e bater num número redondo (1000/10000), é truncamento do limite padrão.

**Lição:** toda query nova ao Supabase precisa ser naturalmente limitada — por **janela de data** (quando o caso de uso já é *bounded* por uma janela de negócio) ou **paginação explícita via `Range`** (ex.: `inbox/repo.py:_get_all`, página de 50.000). O PostgREST **nunca** retorna erro no truncamento; só corta a resposta.

## Diagnóstico: "empresa não recebeu ASO"

Verifique nesta ordem:

1. **Está em `EMPRESAS_BLOQUEADAS`** (`config.py`)? Lista fixa, pulada antes de qualquer consulta ao SOC.
2. **Inadimplente ou erro no `200410`?** Sai no resumo enviado por WhatsApp ao `META_NUMERO_TESTE` no fim do run, e no log (`inadimplente` / `erro na consulta de inadimplência`).
3. **Sem contato válido** no exportador `193815`? Log: `sem contato com número válido — ignorada`. Cadastrar contato com telefone (ver "Cadastro de Contatos" no [README.md](README.md#cadastro-de-contatos)).
4. **`ENVIO_REAL_EMPRESAS=false`?** Nesse modo tudo vai só para o número de teste, por design.

## Troubleshooting rápido

| Sintoma | Causa provável | Ação |
|---|---|---|
| `BLOQUEIO DE SEGURANÇA` no log | Trava funcionando: número real com a flag desligada | Ativar `ENVIO_REAL_EMPRESAS=true` só quando for produção |
| Run morre ~90s no meio | `TimeoutStartSec` da unit não é `0` | Corrigir a unit e `systemctl daemon-reload` |
| ASOs repetidos | Truncamento do Supabase, ou run morto no meio | Ver o post-mortem acima |
| `[SUPABASE] ... 403` | RLS/chave errada | Conferir `SUPABASE_SERVICE_KEY` no `.env` |
| Webhook da Meta não verifica | `WEBHOOK_VERIFY_TOKEN` diferente do painel | Igualar `.env` ↔ painel Meta e reassinar `messages` |
| `codigoMensagem != SOC-100` no download | Credenciais SOAP ou chave GED inválida | Inspecionar `output/debug_downloads/<chave>_xml.txt` |
| `Erro upload PDF Meta: HTTP 401` | Token Meta expirado | Renovar em developers.facebook.com |
