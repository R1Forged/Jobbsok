from __future__ import annotations

import email
import imaplib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header

from .parse_linkedin_email import parse_linkedin_email
from .parser import JobListing


LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailFetchResult:
    emails_scanned: int
    jobs: list[JobListing]


class EmailIngestionNotConfigured(RuntimeError):
    pass


class EmailClient:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        folder: str = "INBOX",
        from_filter: str = "jobs-noreply@linkedin.com",
        subject_filter: str = "job",
        lookback_days: int = 7,
        max_emails_per_run: int = 20,
    ) -> None:
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.folder = folder
        self.from_filter = from_filter
        self.subject_filter = subject_filter
        self.lookback_days = lookback_days
        self.max_emails_per_run = max_emails_per_run

    def fetch_linkedin_jobs(self) -> EmailFetchResult:
        if not (self.host and self.username and self.password):
            raise EmailIngestionNotConfigured("Email ingestion enabled but EMAIL_HOST/EMAIL_USERNAME/EMAIL_PASSWORD is incomplete")
        if self.max_emails_per_run <= 0:
            return EmailFetchResult(emails_scanned=0, jobs=[])

        emails_scanned = 0
        jobs: list[JobListing] = []

        try:
            with imaplib.IMAP4_SSL(self.host, self.port) as mailbox:
                mailbox.login(self.username, self.password)
                status, _ = mailbox.select(self.folder, readonly=True)
                if status != "OK":
                    LOGGER.warning("Could not select email folder %s", self.folder)
                    return EmailFetchResult(emails_scanned=0, jobs=[])

                ids = self._search(mailbox)
                for message_id in ids[: self.max_emails_per_run]:
                    status, data = mailbox.fetch(message_id, "(BODY.PEEK[])")
                    if status != "OK" or not data:
                        continue
                    raw = _first_message_payload(data)
                    if raw is None:
                        continue
                    message = email.message_from_bytes(raw)
                    subject = _decode_mime_header(str(message.get("Subject", "")))
                    jobs.extend(parse_linkedin_email(message, subject=subject))
                    emails_scanned += 1
        except imaplib.IMAP4.error as exc:
            LOGGER.warning("Email ingestion failed: %s", exc)
        except OSError as exc:
            LOGGER.warning("Email ingestion connection failed: %s", exc)

        return EmailFetchResult(emails_scanned=emails_scanned, jobs=jobs)

    def _search(self, mailbox: imaplib.IMAP4_SSL) -> list[bytes]:
        since = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        criteria = ["SINCE", since.strftime("%d-%b-%Y")]
        if self.from_filter:
            criteria.extend(["FROM", f'"{self.from_filter}"'])
        if self.subject_filter:
            criteria.extend(["SUBJECT", f'"{self.subject_filter}"'])
        status, data = mailbox.search(None, *criteria)
        if status != "OK" or not data:
            LOGGER.warning("Email search returned %s", status)
            return []
        ids = data[0].split()
        ids.reverse()
        return ids


def _first_message_payload(data) -> bytes | None:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], bytes):
            return item[1]
    return None


def _decode_mime_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value
