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
| `SUPABASE_SERVICE_KEY` | Backend/pipeline (server-side) | Frontend, browser, repositório |
| `SUPABASE_SECRET_KEY` | Backend/pipeline (cliente PostgREST) | Frontend, browser, repositório |
| `META_WA_TOKEN` | Backend apenas | Logs, frontend, repositório |
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

- O pipeline persiste no Supabase apenas o **estado de envio** dos ASOs (tabelas `empresas` e `asos_enviados`) — usado para deduplicação/idempotência. Não há histórico de conversas.
- PDFs de ASOs **não são armazenados** — apenas transitam em memória/temp e são deletados após o envio

---

## Reportar vulnerabilidades

Encontrou uma vulnerabilidade? Entre em contato diretamente com o mantenedor antes de divulgação pública.
