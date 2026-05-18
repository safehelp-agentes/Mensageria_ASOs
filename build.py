import os

url = os.environ.get("SUPABASE_URL", "")
key = os.environ.get("SUPABASE_KEY", "")

if not url or not key:
    print("AVISO: SUPABASE_URL ou SUPABASE_KEY nao definidos — placeholders mantidos.")

with open("index.html", "r", encoding="utf-8") as f:
    html = f.read()

html = html.replace("__SUPABASE_URL__", url)
html = html.replace("__SUPABASE_KEY__", key)

with open("index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Build OK — URL={'OK' if url else 'VAZIO'}, KEY={'OK' if key else 'VAZIO'}")
