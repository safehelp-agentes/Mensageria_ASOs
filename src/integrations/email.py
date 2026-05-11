import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from config import EMAIL_REMETENTE, EMAIL_SENHA_APP, EMAIL_DESTINO, EMAIL_ENVIAR


def enviar_email_erros(lista_erros: list):
    """Envia relatório de erros da execução por email (Gmail SMTP)."""
    if not EMAIL_ENVIAR:
        return
    if not lista_erros:
        return
    if not EMAIL_REMETENTE or not EMAIL_SENHA_APP or not EMAIL_DESTINO:
        print("Email não enviado: configuração incompleta no .env")
        return

    try:
        msg = MIMEMultipart()
        msg["From"]    = EMAIL_REMETENTE
        msg["To"]      = EMAIL_DESTINO
        msg["Subject"] = "Relatório diário - erros envio ASOs"
        msg.attach(MIMEText("\n\n".join(lista_erros), "plain", "utf-8"))

        servidor = smtplib.SMTP("smtp.gmail.com", 587)
        servidor.starttls()
        servidor.login(EMAIL_REMETENTE, EMAIL_SENHA_APP)
        servidor.send_message(msg)
        servidor.quit()

        print("Email de erros enviado com sucesso.")
    except Exception as e:
        print("Falha ao enviar email:", e)
