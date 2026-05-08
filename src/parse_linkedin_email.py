from __future__ import annotations

import hashlib
import re
from email.message import Message
from html import unescape
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from .parser import JobListing, clean_text


LINKEDIN_EMAIL_SOURCE = "gmail_linkedin"


def parse_linkedin_email(message: Message, subject: str = "") -> list[JobListing]:
    html, plain = _message_bodies(message)
    if html:
        listings = _parse_html(html, subject)
        if listings:
            return listings
    return _parse_plaintext(plain, subject)


def canonicalize_linkedin_url(url: str) -> str:
    decoded = unescape(unquote(url.strip()))
    parsed = urlparse(decoded)
    query = parse_qs(parsed.query)
    for key in ("url", "u", "redirect", "redirectUrl"):
        if query.get(key):
            return canonicalize_linkedin_url(query[key][0])

    host = parsed.netloc.lower()
    if "linkedin.com" not in host:
        return decoded.split("#", 1)[0]

    match = re.search(r"/jobs/view/(\d+)", parsed.path)
    if match:
        return f"https://www.linkedin.com/jobs/view/{match.group(1)}/"

    current_job_id = query.get("currentJobId") or query.get("jobId")
    if current_job_id:
        return f"https://www.linkedin.com/jobs/view/{current_job_id[0]}/"

    return parsed._replace(query="", fragment="").geturl()


def derive_linkedin_job_id(url: str) -> str:
    canonical = canonicalize_linkedin_url(url)
    parsed = urlparse(canonical)
    match = re.search(r"/jobs/view/(\d+)", parsed.path)
    if match:
        return match.group(1)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _message_bodies(message: Message) -> tuple[str, str]:
    html_parts: list[str] = []
    plain_parts: list[str] = []
    for part in message.walk():
        content_type = part.get_content_type()
        disposition = str(part.get("Content-Disposition", "")).lower()
        if "attachment" in disposition:
            continue
        if content_type not in {"text/html", "text/plain"}:
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


def _parse_html(html: str, subject: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[JobListing] = []
    seen: set[str] = set()

    for link in soup.find_all("a", href=True):
        raw_url = str(link.get("href") or "")
        if not _looks_like_linkedin_job_url(raw_url):
            continue
        title = clean_text(link.get_text(" "))
        if not title or _is_non_job_link_text(title):
            continue

        url = canonicalize_linkedin_url(raw_url)
        job_id = derive_linkedin_job_id(url)
        if job_id in seen:
            continue

        snippet = _nearby_text(link, subject)
        company, location = _extract_company_location(title, snippet)
        listings.append(
            JobListing(
                job_id=job_id,
                title=title[:250],
                company=company,
                location=location,
                deadline="",
                url=url,
                snippet=snippet,
                full_description=snippet,
                source=LINKEDIN_EMAIL_SOURCE,
                canonical_url=url,
            )
        )
        seen.add(job_id)

    return listings


def _parse_plaintext(text: str, subject: str) -> list[JobListing]:
    listings: list[JobListing] = []
    seen: set[str] = set()
    lines = [clean_text(line) for line in text.splitlines() if clean_text(line)]

    for index, line in enumerate(lines):
        if not _looks_like_linkedin_job_url(line):
            continue
        url = canonicalize_linkedin_url(line)
        job_id = derive_linkedin_job_id(url)
        if job_id in seen:
            continue
        context = lines[max(0, index - 4) : min(len(lines), index + 3)]
        title = _first_title_candidate(context) or "LinkedIn job alert"
        snippet = clean_text(f"{subject} | {' | '.join(context)}")
        company, location = _extract_company_location(title, snippet)
        listings.append(
            JobListing(
                job_id=job_id,
                title=title[:250],
                company=company,
                location=location,
                deadline="",
                url=url,
                snippet=snippet,
                full_description=snippet,
                source=LINKEDIN_EMAIL_SOURCE,
                canonical_url=url,
            )
        )
        seen.add(job_id)

    return listings


def _looks_like_linkedin_job_url(url: str) -> bool:
    lowered = unquote(url).lower()
    return "linkedin.com" in lowered and ("/jobs/view" in lowered or "currentjobid=" in lowered)


def _is_non_job_link_text(text: str) -> bool:
    lowered = text.lower()
    return lowered in {"view job", "apply", "see job", "show all", "view all"} or len(text) < 3


def _nearby_text(link, subject: str) -> str:
    for parent in link.parents:
        text = clean_text(parent.get_text(" "))
        if len(text) >= 40:
            return clean_text(f"{subject} | {text}")[:4000]
    return clean_text(f"{subject} | {link.get_text(' ')}")[:4000]


def _extract_company_location(title: str, snippet: str) -> tuple[str, str]:
    text = snippet.replace(title, " ", 1)
    parts = [part.strip(" -|,") for part in re.split(r"\s{2,}|\s+[·•]\s+|\s+\|\s+", text) if part.strip(" -|,")]
    cleaned = [
        part
        for part in parts
        if not re.search(r"linkedin|job alert|view job|apply|new jobs?", part, re.I)
        and len(part) <= 120
    ]
    company = cleaned[0] if cleaned else ""
    location = ""
    for part in cleaned[1:5]:
        if re.search(r"oslo|akershus|viken|norway|norge|remote|hybrid|bergen|trondheim|stavanger", part, re.I):
            location = part
            break
    return clean_text(company), clean_text(location)


def _first_title_candidate(context: list[str]) -> str:
    for line in reversed(context):
        if not _looks_like_linkedin_job_url(line) and not _is_non_job_link_text(line):
            if not re.search(r"linkedin|job alert|unsubscribe", line, re.I):
                return line
    return ""
