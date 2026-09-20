"""Outgoing transactional email. Configuration is supplied by the host."""

import json
import smtplib
from email.message import EmailMessage
from urllib import error, request
from urllib.parse import urlsplit

from app.config import settings


class EmailDeliveryError(Exception):
    pass


def _apps_script_url_is_valid(url: str) -> bool:
    parsed = urlsplit(url)
    parts = parsed.path.split("/")
    return (
        parsed.scheme == "https"
        and parsed.netloc == "script.google.com"
        and len(parts) == 5
        and parts[:3] == ["", "macros", "s"]
        and bool(parts[3])
        and parts[4] == "exec"
        and not parsed.query
        and not parsed.fragment
    )


def email_is_configured() -> bool:
    if settings.APPS_SCRIPT_MAIL_URL or settings.APPS_SCRIPT_MAIL_SECRET:
        return bool(
            settings.APPS_SCRIPT_MAIL_SECRET
            and _apps_script_url_is_valid(settings.APPS_SCRIPT_MAIL_URL)
        )
    sender = settings.EMAIL_FROM or settings.SMTP_FROM
    provider = sender and (
        settings.RESEND_API_KEY
        or (settings.SMTP_HOST and bool(settings.SMTP_USER) == bool(settings.SMTP_PASSWORD))
    )
    return bool(provider)


def send_email(to_address: str, subject: str, body: str) -> None:
    if not email_is_configured():
        raise EmailDeliveryError("Email delivery is not configured")

    if settings.APPS_SCRIPT_MAIL_URL and settings.APPS_SCRIPT_MAIL_SECRET:
        payload = json.dumps({
            "secret": settings.APPS_SCRIPT_MAIL_SECRET,
            "to": to_address,
            "subject": subject,
            "body": body,
        }).encode("utf-8")
        email_request = request.Request(
            settings.APPS_SCRIPT_MAIL_URL,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(email_request, timeout=20) as response:
                result = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise EmailDeliveryError(
                f"Apps Script returned HTTP {exc.code}; check the /exec URL and web app access"
            ) from exc
        except (error.URLError, TimeoutError, OSError) as exc:
            raise EmailDeliveryError("Apps Script request failed; check the deployment URL and access setting") from exc
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise EmailDeliveryError("Apps Script did not return JSON; check the /exec URL and access setting") from exc
        if not isinstance(result, dict) or result.get("ok") is not True:
            if isinstance(result, dict) and result.get("error") == "Unauthorized":
                raise EmailDeliveryError("Apps Script MAIL_SECRET does not match APPS_SCRIPT_MAIL_SECRET")
            raise EmailDeliveryError("Apps Script could not send email; check its Executions log")
        return

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
