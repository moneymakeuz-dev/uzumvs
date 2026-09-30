import asyncio
import logging
from email.message import EmailMessage
from uuid import uuid4

import aiosmtplib

from app.config import Settings
from app.errors import AppError

logger = logging.getLogger(__name__)


def auth_message(settings: Settings, email: str, token: str, purpose: str) -> EmailMessage:
    verification = purpose == "verify_email"
    path = "verify-email" if verification else "reset-password"
    subject = "Emailni tasdiqlash" if verification else "Parolni tiklash"
    url = f"{settings.app_base_url.rstrip('/')}/auth/{path}?token={token}"
    message = EmailMessage()
    message["To"] = email
    message["From"] = settings.smtp_from or "Karto <no-reply@localhost>"
    message["Subject"] = f"Karto: {subject}"
    message.set_content(f"{subject}\n\n{url}\n\nBu so'rov sizdan bo'lmasa, xatni e'tiborsiz qoldiring.")
    return message


async def send_auth_link(settings: Settings, email: str, token: str, purpose: str) -> None:
    message = auth_message(settings, email, token, purpose)
    try:
        if settings.mail_backend == "file":
            await asyncio.to_thread(store_local_message, settings, message)
        else:
            await aiosmtplib.send(
                message, hostname=settings.smtp_host, port=settings.smtp_port,
                username=settings.smtp_username or None,
                password=settings.smtp_password.get_secret_value() or None,
                start_tls=settings.smtp_starttls, use_tls=settings.smtp_use_tls, timeout=15,
            )
    except (OSError, aiosmtplib.SMTPException, TimeoutError):
        logger.error("mail_delivery_failed")
        raise AppError("mail_unavailable", "Xat xizmati vaqtincha ishlamayapti.", 503) from None


def store_local_message(settings: Settings, message: EmailMessage) -> None:
    if settings.app_env == "production":
        raise AppError("mail_configuration", "Email xizmati sozlanmagan.", 503)
    settings.mail_dir.mkdir(parents=True, exist_ok=True)
    with (settings.mail_dir / f"{uuid4().hex}.eml").open("xb") as destination:
        destination.write(message.as_bytes())
