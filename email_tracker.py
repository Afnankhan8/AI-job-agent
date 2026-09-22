"""Synchronize LinkedIn application emails into the application tracker."""

import email
import imaplib
import re
import time
from datetime import datetime, timezone
from email.header import decode_header
from email.message import Message
from typing import Optional

from config.settings import SETTINGS
from database.models import get_connection, init_db
from database.repository import JobRepository


def _decode(value: Optional[str]) -> str:
    if not value:
        return ""
    parts = []
    for text, encoding in decode_header(value):
        if isinstance(text, bytes):
            charset = (encoding or "utf-8").lower()
            if charset in {"unknown-8bit", "x-unknown", "binary"}:
                charset = "utf-8"
            try:
                parts.append(text.decode(charset, errors="replace"))
            except (LookupError, UnicodeError):
                parts.append(text.decode("latin-1", errors="replace"))
        else:
            parts.append(text)
    return "".join(parts)


def _body(message: Message) -> str:
    if message.is_multipart():
        chunks = [_body(part) for part in message.walk() if part.get_content_type() == "text/plain"]
        return "\n".join(chunks)
    payload = message.get_payload(decode=True)
    if isinstance(payload, bytes):
        return payload.decode(message.get_content_charset() or "utf-8", errors="replace")
    return str(payload or "")


def _response_status(subject: str, body: str) -> str:
    text = f"{subject} {body}".lower()
    if any(phrase in text for phrase in (
        "thanks for applying",
        "thank you for applying",
        "application was submitted",
        "application submitted",
    )):
        return "APPLICATION_CONFIRMED"
    if any(word in text for word in ("interview", "phone screen", "assessment", "next steps")):
        return "INTERVIEW_OR_NEXT_STEP"
    if any(word in text for word in ("not selected", "rejected", "unfortunately", "position has been filled")):
        return "REJECTED"
    return "RESPONSE_RECEIVED"


def _matches_linkedin(message: Message, subject: str, body: str) -> bool:
    sender = _decode(message.get("From")).lower()
    text = f"{sender} {subject} {body}".lower()
    return "linkedin" in text or "linkedin.com" in sender


def _imap_since(applied_at: str) -> str:
    try:
        return datetime.fromisoformat(applied_at.replace("Z", "+00:00")).strftime("%d-%b-%Y")
    except ValueError:
        return datetime.now(timezone.utc).strftime("%d-%b-%Y")


def sync_linkedin_emails() -> tuple[int, str]:
    """Import matching LinkedIn emails; return (count, human-readable result)."""
    if not all((SETTINGS.imap_host, SETTINGS.imap_username, SETTINGS.imap_password)):
        return 0, "IMAP is not configured"

    init_db(SETTINGS.database_path)
    conn = get_connection(SETTINGS.database_path)
    repo = JobRepository(conn)
    password = "".join((SETTINGS.imap_password or "").split())
    mailbox = None
    connection_error = None
    for attempt in range(1, 4):
        try:
            mailbox_class = imaplib.IMAP4_SSL if SETTINGS.imap_use_ssl else imaplib.IMAP4
            mailbox = mailbox_class(SETTINGS.imap_host, SETTINGS.imap_port, timeout=20)
            break
        except (OSError, imaplib.IMAP4.error) as exc:
            connection_error = exc
            if attempt < 3:
                time.sleep(attempt)
    if mailbox is None:
        conn.close()
        return 0, f"IMAP connection failed after 3 attempts: {connection_error}"
    try:
        try:
            mailbox.login(SETTINGS.imap_username, password)
        except (OSError, imaplib.IMAP4.error) as exc:
            return 0, (
                "IMAP authentication failed. Check the mailbox username and Gmail app password; "
                "the password must be current and IMAP-enabled."
            )
        mailbox.select("INBOX")
        rows = conn.execute(
            """
            SELECT a.id, a.applied_at, j.title, j.company
            FROM applications a JOIN jobs j ON j.id = a.job_id
            WHERE a.status IN ('APPLIED', 'DRY_RUN')
              AND a.response_received_at IS NULL
            """
        ).fetchall()
        updated = 0
        for row in rows:
            applied_at = row["applied_at"] or ""
            since = _imap_since(applied_at)
            typ, data = mailbox.search(None, "SINCE", since)
            if typ != "OK" or not data or not data[0]:
                continue
            for message_id in data[0].split():
                typ, fetched = mailbox.fetch(message_id, "(RFC822)")
                if typ != "OK" or not fetched or not isinstance(fetched[0], tuple):
                    continue
                message = email.message_from_bytes(fetched[0][1])
                subject = _decode(message.get("Subject"))
                body = _body(message)
                if not _matches_linkedin(message, subject, body):
                    continue
                message_text = f"{subject} {body}".lower()
                role_match = row["title"].lower() in message_text
                company = (row["company"] or "").lower()
                company_match = bool(company) and company in message_text
                if company and not (role_match and company_match):
                    continue
                if not company and not role_match:
                    continue
                received = _decode(message.get("Date")) or datetime.now(timezone.utc).isoformat()
                snippet = re.sub(r"\s+", " ", body).strip()[:500]
                repo.record_application_response(
                    row["id"], _response_status(subject, body), received,
                    subject[:500], _decode(message.get("From"))[:500], snippet,
                )
                updated += 1
                break
        return updated, f"Synced {updated} LinkedIn response(s)"
    finally:
        try:
            mailbox.logout()
        except (OSError, imaplib.IMAP4.error):
            pass
        finally:
            conn.close()


if __name__ == "__main__":
    count, message = sync_linkedin_emails()
    print(message)