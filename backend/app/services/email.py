"""Outgoing transactional email. Configuration is supplied by the host."""

import json
import smtplib
from email.message import EmailMessage
from urllib import error, request

from app.config import settings


class EmailDeliveryError(Exception):
    pass


def email_is_configured() -> bool:
    sender = settings.EMAIL_FROM or settings.SMTP_FROM
    return bool(
        sender and (
            settings.RESEND_API_KEY
            or (
                settings.SMTP_HOST
                and bool(settings.SMTP_USER) == bool(settings.SMTP_PASSWORD)
            )
        )
    )


def send_email(to_address: str, subject: str, body: str) -> None:
    if not email_is_configured():
        raise EmailDeliveryError("Email delivery is not configured")

    sender = settings.EMAIL_FROM or settings.SMTP_FROM
    if settings.RESEND_API_KEY:
        payload = json.dumps({
            "from": sender,
            "to": [to_address],
            "subject": subject,
            "text": body,
        }).encode("utf-8")
        email_request = request.Request(
            "https://api.resend.com/emails",
            data=payload,
            headers={
                "Authorization": f"Bearer {settings.RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(email_request, timeout=15):
                return
        except (error.HTTPError, error.URLError, TimeoutError, OSError) as exc:
            raise EmailDeliveryError("Email provider rejected the message") from exc

    message = EmailMessage()
    message["From"] = sender
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
