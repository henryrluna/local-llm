from __future__ import annotations

import smtplib
from email.message import EmailMessage

import httpx

from .config import Settings


def notify(settings: Settings, job_id: str, title: str) -> list[str]:
    link = f"{settings.public_base_url.rstrip('/')}/?job={job_id}"
    errors: list[str] = []
    if settings.ntfy_url:
        try:
            httpx.post(
                settings.ntfy_url,
                content=f"Your research report is ready: {link}",
                headers={"Title": "Research complete", "Click": link},
                timeout=15,
            ).raise_for_status()
        except httpx.HTTPError as exc:
            errors.append(f"Push notification failed: {exc}")
    if settings.smtp_host and settings.notify_email_from and settings.notify_email_to:
        message = EmailMessage()
        message["Subject"] = f"Research complete: {title[:80]}"
        message["From"] = settings.notify_email_from
        message["To"] = settings.notify_email_to
        message.set_content(f"Your research report is ready.\n\n{link}\n\nNo private source content is included in this notification.")
        try:
            with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
                smtp.starttls()
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, settings.smtp_password)
                smtp.send_message(message)
        except (OSError, smtplib.SMTPException) as exc:
            errors.append(f"Email notification failed: {exc}")
    return errors

