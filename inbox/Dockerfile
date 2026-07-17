# Inbox — webhook Meta + dashboard read-only (FastAPI).
# O código NÃO é copiado: é montado por volume (:ro) no compose, igual ao padrão
# do projeto — assim `git pull` atualiza sem rebuild. A imagem só carrega as deps.
FROM python:3.11-slim

WORKDIR /app

COPY inbox/requirements.txt ./inbox/requirements.txt
RUN pip install --no-cache-dir -r inbox/requirements.txt

EXPOSE 8002
CMD ["uvicorn", "inbox.app:app", "--host", "0.0.0.0", "--port", "8002"]
