from __future__ import annotations

import base64
import email
import hashlib
import logging
import re
from dataclasses import dataclass, field
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .parse_linkedin_email import parse_linkedin_email
from .parser import JobListing, canonicalize_url, clean_text, derive_job_id


LOGGER = logging.getLogger(__name__)

DEFAULT_GMAIL_QUERY = "in:inbox from:(linkedin.com OR finn.no OR indeed.com) newer_than:14d"
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]


@dataclass(frozen=True)
class GmailEmail:
    message_id: str
    source: str
    subject: str
    from_email: str
    jobs: list[JobListing] = field(default_factory=list)
    error_message: str = ""


@dataclass(frozen=True)
class GmailFetchResult:
    emails_found: int
    emails_processed: int
    emails_skipped_error: int
    jobs: list[JobListing]
    emails: list[GmailEmail]


class GmailIngestionNotConfigured(RuntimeError):
    pass


class GmailClient:
    def __init__(self, credentials_path: Path, token_path: Path, query: str, max_emails_per_run: int) -> None:
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.query = query.strip() or DEFAULT_GMAIL_QUERY
        self.max_emails_per_run = max_emails_per_run
        self._service = None

    def fetch_job_alerts(self, already_processed: set[str] | None = None) -> GmailFetchResult:
        if self.max_emails_per_run <= 0:
            return GmailFetchResult(0, 0, 0, [], [])
        already_processed = already_processed or set()
        service = self._gmail_service()
        response = (
            service.users()
            .messages()
            .list(userId="me", q=self.query, maxResults=self.max_emails_per_run)
            .execute()
        )
        messages = response.get("messages", [])
        emails: list[GmailEmail] = []
        jobs: list[JobListing] = []
        skipped_errors = 0

        LOGGER.info("Gmail emails found=%s query=%r", len(messages), self.query)
        for item in messages:
            message_id = str(item.get("id", "")).strip()
            if not message_id or message_id in already_processed:
                continue
            try:
                gmail_message = (
                    service.users()
                    .messages()
                    .get(userId="me", id=message_id, format="raw")
                    .execute()
                )
                raw = base64.urlsafe_b64decode(str(gmail_message["raw"]).encode("ascii"))
                message = email.message_from_bytes(raw)
                subject = _decode_mime_header(str(message.get("Subject", "")))
                from_email = _decode_mime_header(str(message.get("From", "")))
                source = _source_from_email(from_email)
                parsed_jobs = _parse_job_alert_email(message, source, subject)
                for job in parsed_jobs:
                    job.source_message_id = message_id
                emails.append(
                    GmailEmail(
                        message_id=message_id,
                        source=source,
                        subject=subject,
                        from_email=from_email,
                        jobs=parsed_jobs,
                    )
                )
                jobs.extend(parsed_jobs)
                LOGGER.info(
                    "Gmail email parsed. message_id=%s source=%s jobs=%s subject=%r",
                    message_id,
                    source,
                    len(parsed_jobs),
                    subject[:120],
                )
            except Exception as exc:
                skipped_errors += 1
                LOGGER.exception("Failed parsing Gmail message_id=%s", message_id)
                emails.append(
                    GmailEmail(
                        message_id=message_id,
                        source="gmail_other",
                        subject="",
                        from_email="",
                        error_message=str(exc),
                    )
                )

        return GmailFetchResult(
            emails_found=len(messages),
            emails_processed=len([email_record for email_record in emails if not email_record.error_message]),
            emails_skipped_error=skipped_errors,
            jobs=jobs,
            emails=emails,
        )

    def cleanup_message(self, message_id: str, action: str) -> bool:
        action = _normalize_cleanup_action(action)
        if action == "none":
            return True
        service = self._gmail_service()
        if action == "trash":
            service.users().messages().trash(userId="me", id=message_id).execute()
            return True
        service.users().messages().modify(
            userId="me",
            id=message_id,
            body={"removeLabelIds": ["INBOX"]},
        ).execute()
        return True

    def _gmail_service(self):
        if self._service is not None:
            return self._service
        if not self.credentials_path.exists():
            raise GmailIngestionNotConfigured(
                f"ENABLE_GMAIL=true but GMAIL_CREDENTIALS_PATH does not exist: {self.credentials_path}"
            )
        if not self.token_path.exists():
            raise GmailIngestionNotConfigured(
                f"ENABLE_GMAIL=true but GMAIL_TOKEN_PATH does not exist: {self.token_path}. "
                "Generate an OAuth token locally and provide it securely in GitHub Actions."
            )
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ImportError as exc:
            raise GmailIngestionNotConfigured(
                "ENABLE_GMAIL=true but Google Gmail API dependencies are missing. Run pip install -r requirements.txt."
            ) from exc
        credentials = Credentials.from_authorized_user_file(str(self.token_path), GMAIL_SCOPES)
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.token_path.write_text(credentials.to_json(), encoding="utf-8")
        if not credentials.valid:
            raise GmailIngestionNotConfigured(
                "Gmail OAuth token is missing, expired, or invalid. Regenerate GMAIL_TOKEN_PATH locally."
            )
        self._service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        return self._service


def _parse_job_alert_email(message: Message, source: str, subject: str) -> list[JobListing]:
    if source == "gmail_linkedin":
        return parse_linkedin_email(message, subject=subject)
    html, plain = _message_bodies(message)
    if html:
        listings = _parse_generic_html(html, source, subject)
        if listings:
            return listings
    return _parse_generic_plaintext(plain, source, subject)


def _message_bodies(message: Message) -> tuple[str, str]:
    html_parts: list[str] = []
    plain_parts: list[str] = []
    for part in message.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition or content_type not in {"text/html", "text/plain"}:
            continue
        payload = part.get_payload(decode=True)
        if payload is None:
            continue
        charset = part.get_content_charset() or "utf-8"
        try:
            text = payload.decode(charset, errors="replace")
        except LookupError:
            text = payload.decode("utf-8", errors="replace")
        if content_type == "text/html":
            html_parts.append(text)
        else:
            plain_parts.append(text)
    return "\n".join(html_parts), "\n".join(plain_parts)


def _parse_generic_html(html: str, source: str, subject: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings_by_url: dict[str, JobListing] = {}
    for link in soup.find_all("a", href=True):
        raw_url = str(link.get("href") or "")
        if not _looks_like_supported_job_url(raw_url):
            continue
        url = _canonicalize_supported_url(raw_url)
        title = clean_text(link.get_text(" ")) or _title_from_url(url, source)
        if _is_generic_non_job_text(title):
            title = _title_from_url(url, source)
        if url in listings_by_url:
            existing = listings_by_url[url]
            if _is_generic_alert_title(existing.title) and not _is_generic_alert_title(title):
                listings_by_url[url] = _listing_from_url(source, url, title, existing.snippet)
            continue
        snippet = _nearby_text(link, subject)
        listings_by_url[url] = _listing_from_url(source, url, title, snippet)
    return list(listings_by_url.values())


def _parse_generic_plaintext(text: str, source: str, subject: str) -> list[JobListing]:
    listings: list[JobListing] = []
    seen: set[str] = set()
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]
    for index, line in enumerate(lines):
        if not _looks_like_supported_job_url(line):
            continue
        url = _canonicalize_supported_url(line)
        if url in seen:
            continue
        context = lines[max(0, index - 4) : min(len(lines), index + 4)]
        title = _first_title_candidate(context) or _title_from_url(url, source)
        snippet = clean_text(f"{subject} | {' | '.join(context)}")[:4000]
        listings.append(_listing_from_url(source, url, title, snippet))
        seen.add(url)
    return listings


def _listing_from_url(source: str, url: str, title: str, snippet: str) -> JobListing:
    listing_source = _source_from_job_url(url, source)
    if listing_source == "gmail_finn":
        job_id = derive_job_id(url)
    else:
        job_id = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return JobListing(
        job_id=job_id,
        title=title[:250],
        company="",
        location="",
        deadline="",
        url=url,
        snippet=snippet,
        full_description=snippet,
        source=listing_source,
        canonical_url=url,
    )


def _looks_like_supported_job_url(url: str) -> bool:
    lowered = unquote(url).lower()
    if "linkedin.com" in lowered and ("/jobs/view" in lowered or "currentjobid=" in lowered):
        return True
    if "finn.no" in lowered and ("/job/ad/" in lowered or "/job/fulltime/ad.html" in lowered or "/job/parttime/ad.html" in lowered):
        return True
    if "indeed." in lowered and ("/viewjob" in lowered or "jk=" in lowered):
        return True
    return False


def _canonicalize_supported_url(url: str) -> str:
    decoded = unquote(url.strip())
    parsed = urlparse(decoded)
    query = parse_qs(parsed.query)
    for key in ("url", "u", "redirect", "redirectUrl"):
        if query.get(key):
            return _canonicalize_supported_url(query[key][0])
    linkedin_id = _linkedin_job_id(parsed, query)
    if linkedin_id:
        return f"https://www.linkedin.com/jobs/view/{linkedin_id}/"
    if "finn.no" in parsed.netloc.lower():
        return canonicalize_url(decoded)
    return parsed._replace(fragment="").geturl()


def _source_from_email(from_email: str) -> str:
    lowered = from_email.lower()
    if "linkedin" in lowered:
        return "gmail_linkedin"
    if "finn.no" in lowered or "finn" in lowered:
        return "gmail_finn"
    return "gmail_other"


def _source_from_job_url(url: str, fallback: str) -> str:
    lowered = url.lower()
    if "linkedin.com" in lowered:
        return "gmail_linkedin"
    if "finn.no" in lowered:
        return "gmail_finn"
    if "indeed." in lowered:
        return "gmail_indeed"
    return fallback


def _linkedin_job_id(parsed, query: dict[str, list[str]]) -> str:
    if "linkedin.com" not in parsed.netloc.lower():
        return ""
    if query.get("currentJobId"):
        return query["currentJobId"][0].strip()
    match = re.search(r"/(?:comm/)?jobs/view/(\d+)", parsed.path)
    return match.group(1) if match else ""


def _decode_mime_header(value: str) -> str:
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return value


def _nearby_text(link, subject: str) -> str:
    for parent in link.parents:
        text = clean_text(parent.get_text(" "))
        if len(text) >= 40:
            return clean_text(f"{subject} | {text}")[:4000]
    return clean_text(f"{subject} | {link.get_text(' ')}")[:4000]


def _first_title_candidate(context: list[str]) -> str:
    for line in reversed(context):
        if not _looks_like_supported_job_url(line) and not _is_generic_non_job_text(line):
            return line
    return ""


def _is_generic_non_job_text(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"view job", "apply", "see job", "show all", "view all", "søk her"} or len(lowered) < 3


def _is_generic_alert_title(text: str) -> bool:
    lowered = text.strip().lower()
    return lowered in {"gmail job alert", "linkedin job alert", "finn job alert"}


def _title_from_url(url: str, source: str) -> str:
    if source == "gmail_finn":
        return "FINN job alert"
    if source == "gmail_linkedin":
        return "LinkedIn job alert"
    return "Gmail job alert"


def _normalize_cleanup_action(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in {"none", "archive", "trash"}:
        LOGGER.warning("Invalid GMAIL_CLEANUP_ACTION=%r. Falling back to archive.", value)
        return "archive"
    return normalized
