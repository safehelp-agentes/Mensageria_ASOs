"""
build.py — Injeta credenciais Supabase no CRM (index.html).

IMPORTANTE:
  - Use SUPABASE_ANON_KEY (chave pública/anon) no frontend — NUNCA a service_role key.
  - A service_role ignora o Row Level Security e daria acesso total ao banco no browser.
  - Configure SUPABASE_ANON_KEY em: Supabase → Project Settings → API → anon/public
"""
import os
import sys

url      = os.environ.get("SUPABASE_URL", "")
anon_key = os.environ.get("SUPABASE_ANON_KEY", "")

erros = []
if not url:
    erros.append("SUPABASE_URL não definido")
if not anon_key:
    erros.append("SUPABASE_ANON_KEY não definido (use a chave 'anon/public', NÃO a service_role)")

if erros:
    for e in erros:
        print(f"ERRO: {e}")
    sys.exit(1)

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

if "__SUPABASE_URL__" not in html or "__SUPABASE_KEY__" not in html:
    print("AVISO: placeholders não encontrados no index.html — verifique se já foi buildado antes.")

html = html.replace("__SUPABASE_URL__", url)
html = html.replace("__SUPABASE_KEY__", anon_key)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Build OK — URL=OK, ANON_KEY=OK")
