# Política de Segurança

## Dados tratados

Este sistema processa **dados de saúde ocupacional (ASOs)**, classificados como **dados sensíveis** pela Lei Geral de Proteção de Dados (LGPD — Lei nº 13.709/2018). O tratamento é realizado com base legal no **legítimo interesse** do empregador para fins de medicina do trabalho (art. 7º, IX e art. 11, II, "f").

---

## O que nunca deve ir para o repositório

| Arquivo / padrão | Motivo |
|---|---|
| `.env` | Todas as chaves de API e senhas |
| `service_account*.json` | Chave privada Google Service Account |
| `*.pem`, `*.key`, `*.p12` | Certificados e chaves privadas |
| `index.html` após `build.py` | Contém chave Supabase embutida |
| `output/`, `data/*.json` | Podem conter PDFs ou dados de funcionários |

Verifique antes de qualquer commit:

```bash
# Verifica se há credenciais rastreadas acidentalmente
git ls-files | grep -E '\.env$|service_account|\.pem$|\.key$'

# Inspeciona o último diff antes de commitar
git diff --staged
```

---

## Chaves e suas permissões

| Chave | Onde usar | Nunca usar em |
|---|---|---|
| `SUPABASE_SERVICE_KEY` | Backend/pipeline (server-side) | Frontend, browser, `index.html` |
| `SUPABASE_ANON_KEY` | Frontend CRM (`build.py` → `index.html`) | Operações administrativas |
| `META_WA_TOKEN` | Backend apenas | Logs, frontend, repositório |
| `GROQ_API_KEY` | Backend apenas | Logs, frontend, repositório |
| `SOC_CHAVE_*` | Backend apenas | Logs, frontend, repositório |

---

## Rotação de credenciais

Recomenda-se rotacionar as chaves a cada 90 dias ou imediatamente em caso de:

- Suspeita de vazamento
- Saída de colaborador com acesso
- Commit acidental de segredos (usar `git filter-repo` para limpar o histórico)

### Como limpar um segredo do histórico git

```bash
# Instale git-filter-repo
pip install git-filter-repo

# Remove o arquivo do histórico inteiro
git filter-repo --invert-paths --path .env

# Force push (coordenar com o time)
git push origin --force-with-lease
```

---

## Retenção de dados (LGPD)

- Conversas do bot são armazenadas no Supabase por **90 dias**
- Após esse período, devem ser deletadas via rotina automatizada
- PDFs de ASOs **não são armazenados** — apenas transitam em memória/temp e são deletados após o envio

```sql
-- Executar periodicamente no Supabase (ou via pg_cron)
DELETE FROM conversas_bot WHERE updated_at < NOW() - INTERVAL '90 days';
DELETE FROM mensagens    WHERE created_at  < NOW() - INTERVAL '90 days';
```

---

## Reportar vulnerabilidades

Encontrou uma vulnerabilidade? Entre em contato diretamente com o mantenedor antes de divulgação pública.
