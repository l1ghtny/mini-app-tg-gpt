import asyncio
import smtplib
from email.message import EmailMessage

from app.core.config import settings


def _send_message(message: EmailMessage) -> None:
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as client:
        if settings.SMTP_STARTTLS:
            client.starttls()
        if settings.SMTP_USERNAME:
            client.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        client.send_message(message)


async def send_web_login_link(email: str, login_url: str) -> None:
    if not settings.SMTP_HOST or not settings.WEB_AUTH_FROM_EMAIL:
        raise RuntimeError("Web authentication email delivery is not configured")

    message = EmailMessage()
    message["Subject"] = "Sign in to Lightny AI / Вход в Lightny AI"
    message["From"] = settings.WEB_AUTH_FROM_EMAIL
    message["To"] = email
    message.set_content(
        "Open this link, then confirm sign-in on the page. The link expires in "
        f"{settings.WEB_AUTH_LINK_TTL_MINUTES} minutes:\n\n{login_url}\n\n"
        "Откройте ссылку и подтвердите вход на странице. "
        f"Ссылка действует {settings.WEB_AUTH_LINK_TTL_MINUTES} минут:\n\n{login_url}\n"
    )
    await asyncio.to_thread(_send_message, message)
