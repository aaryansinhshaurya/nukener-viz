"""Outgoing transactional email. Configuration is supplied by the host."""

import smtplib
from email.message import EmailMessage

from app.config import settings


class EmailDeliveryError(Exception):
    pass


def email_is_configured() -> bool:
    return bool(
        settings.SMTP_HOST
        and settings.SMTP_FROM
        and bool(settings.SMTP_USER) == bool(settings.SMTP_PASSWORD)
    )


def send_email(to_address: str, subject: str, body: str) -> None:
    if not email_is_configured():
        raise EmailDeliveryError("Email delivery is not configured")

    message = EmailMessage()
    message["From"] = settings.SMTP_FROM
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)
    try:
        if settings.SMTP_PORT == 465:
            client = smtplib.SMTP_SSL(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        else:
            client = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15)
        with client:
            if settings.SMTP_PORT != 465 and settings.SMTP_TLS:
                client.starttls()
            if settings.SMTP_USER:
                client.login(settings.SMTP_USER, settings.SMTP_PASSWORD)
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        raise EmailDeliveryError("Email could not be delivered") from exc
