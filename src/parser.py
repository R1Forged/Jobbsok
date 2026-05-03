from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Iterable
from urllib.parse import parse_qs, urljoin, urlparse

from bs4 import BeautifulSoup


FINN_SOURCE = "finn"


@dataclass
class JobListing:
    job_id: str
    title: str
    company: str
    location: str
    deadline: str
    url: str
    snippet: str
    full_description: str = ""
    source: str = FINN_SOURCE

    @property
    def combined_text(self) -> str:
        return " ".join(
            part
            for part in [
                self.title,
                self.company,
                self.location,
                self.deadline,
                self.snippet,
                self.full_description,
            ]
            if part
        )


def canonicalize_url(url: str, base_url: str = "https://www.finn.no") -> str:
    absolute = urljoin(base_url, url)
    parsed = urlparse(absolute)
    return parsed._replace(fragment="", query=parsed.query).geturl()


def derive_job_id(url: str) -> str:
    parsed = urlparse(url)
    match = re.search(r"/job/ad/(\d+)", parsed.path)
    if match:
        return match.group(1)
    qs = parse_qs(parsed.query)
    for key in ("finnkode", "adid", "id"):
        if qs.get(key):
            return qs[key][0]
    normalized = parsed._replace(query="", fragment="").geturl()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]


def clean_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value).strip()


def _looks_like_job_ad_link(href: str | None) -> bool:
    if not href:
        return False
    return "/job/ad/" in href or "/job/fulltime/ad.html" in href or "/job/parttime/ad.html" in href


def parse_search_results(html: str, search_url: str) -> list[JobListing]:
    soup = BeautifulSoup(html, "html.parser")
    listings: list[JobListing] = []
    seen_ids: set[str] = set()

    for link in soup.find_all("a", href=_looks_like_job_ad_link):
        title = clean_text(link.get_text(" "))
        if not title or title.lower() in {"image", "søk her"}:
            continue

        url = canonicalize_url(str(link.get("href")), search_url)
        job_id = derive_job_id(url)
        if job_id in seen_ids:
            continue

        container = _nearest_result_container(link)
        context_text = clean_text(container.get_text(" ")) if container else title
        company = _extract_company_from_result(container, context_text, title)
        location, deadline = _extract_location_deadline(container, context_text)

        listings.append(
            JobListing(
                job_id=job_id,
                title=title,
                company=company,
                location=location,
                deadline=deadline,
                url=url,
                snippet=context_text,
            )
        )
        seen_ids.add(job_id)

    return listings


def _nearest_result_container(link) -> object | None:
    for parent in link.parents:
        text = clean_text(parent.get_text(" "))
        if len(text) > 25 and ("Legg til som favoritt" in text or "stilling" in text.lower()):
            return parent
    return link.parent


def _extract_company_from_result(container, context_text: str, title: str) -> str:
    if container:
        strong = container.find("strong")
        if strong:
            company = clean_text(strong.get_text(" "))
            if company:
                return company

    text = context_text.replace(title, " ", 1)
    text = re.sub(r"Legg til som favoritt\.?", " ", text, flags=re.IGNORECASE)
    text = re.sub(r"(Betalt plassering|Enkel søknad)", " ", text, flags=re.IGNORECASE)
    parts = [clean_text(part) for part in re.split(r"\s{2,}|\|", text) if clean_text(part)]
    for part in reversed(parts):
        if re.search(r"\b\d+\s+stilling", part, flags=re.IGNORECASE):
            return clean_text(re.sub(r"\b\d+\s+stillinger?.*", "", part, flags=re.IGNORECASE))
    lines = [clean_text(line) for line in context_text.splitlines() if clean_text(line)]
    for line in reversed(lines[-5:]):
        if line != title and not re.search(r"(frist|siden|\d{4}|favoritt|søknad)", line, re.IGNORECASE):
            return line
    return ""


def _extract_location_deadline(container, context_text: str) -> tuple[str, str]:
    deadline = ""
    location = ""
    if container:
        time_tag = container.find("time")
        if time_tag:
            deadline = clean_text(time_tag.get_text(" ")) or clean_text(time_tag.get("datetime"))
        pills = container.select(".job-card__pills li")
        if pills:
            location = clean_text(pills[0].get_text(" "))
            return location, deadline

    for pattern in [r"Frist\s+([^|]+)", r"(\d{1,2}\.\s+\w+\.?\s+\d{4})\s+\|\s+([^|]+)"]:
        match = re.search(pattern, context_text, flags=re.IGNORECASE)
        if match and len(match.groups()) == 1:
            deadline = clean_text(match.group(1))
        elif match and len(match.groups()) >= 2:
            deadline = clean_text(match.group(1))
            location = clean_text(match.group(2))
    if not location:
        pipe_match = re.search(r"\|\s*([^|]+?)(?:\s+[A-ZÆØÅ][\wÆØÅæøå&.\- ]+\s+\d+\s+stillinger?)?$", context_text)
        if pipe_match:
            location = clean_text(pipe_match.group(1))
    return location, deadline


def parse_detail_page(html: str, listing: JobListing) -> JobListing:
    soup = BeautifulSoup(html, "html.parser")
    json_ld = _extract_json_ld(soup)

    title = clean_text(json_ld.get("title")) or listing.title or _first_heading_text(soup)
    company = _extract_company_from_json(json_ld) or listing.company or _extract_company_from_detail(soup)
    location = _extract_location_from_detail(soup, json_ld) or listing.location
    deadline = _extract_deadline_from_detail(soup) or listing.deadline
    description = _extract_description(soup, json_ld)

    return JobListing(
        job_id=listing.job_id,
        title=title,
        company=company,
        location=location,
        deadline=deadline,
        url=listing.url,
        snippet=listing.snippet,
        full_description=description,
        source=listing.source,
    )


def _extract_json_ld(soup: BeautifulSoup) -> dict:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or "")
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for item in candidates:
            if isinstance(item, dict) and item.get("@type") in {"JobPosting", "JobPostingType"}:
                return item
    return {}


def _first_heading_text(soup: BeautifulSoup) -> str:
    heading = soup.find(["h1", "h2"])
    return clean_text(heading.get_text(" ")) if heading else ""


def _extract_company_from_json(json_ld: dict) -> str:
    hiring = json_ld.get("hiringOrganization")
    if isinstance(hiring, dict):
        company = clean_text(hiring.get("name"))
        if company:
            return company
    return ""


def _extract_company_from_detail(soup: BeautifulSoup) -> str:
    heading = soup.find(["h1", "h2"])
    heading_text = clean_text(heading.get_text(" ")) if heading else ""
    lines = [clean_text(line) for line in soup.get_text("\n").splitlines() if clean_text(line)]
    if heading_text and heading_text in lines:
        start = lines.index(heading_text)
        for line in lines[start + 1 : start + 6]:
            if line != heading_text and not re.search(r"^(frist|ansettelsesform|legg til|inaktiv)$", line, re.I):
                return line
    return ""


def _extract_location_from_detail(soup: BeautifulSoup, json_ld: dict) -> str:
    location = json_ld.get("jobLocation")
    if isinstance(location, dict):
        address = location.get("address")
        if isinstance(address, dict):
            parts = [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]
            return clean_text(", ".join(str(part) for part in parts if part))
    page_text = soup.get_text("\n")
    match = re.search(r"\bSted:\s*([^\n]+)", page_text, flags=re.IGNORECASE)
    if match:
        return clean_text(match.group(1))
    return ""


def _extract_deadline_from_detail(soup: BeautifulSoup) -> str:
    text = soup.get_text("\n")
    match = re.search(r"\bFrist\s+([^\n]+)", text, flags=re.IGNORECASE)
    return clean_text(match.group(1)) if match else ""


def _extract_description(soup: BeautifulSoup, json_ld: dict) -> str:
    description = clean_text(json_ld.get("description"))
    if description:
        return BeautifulSoup(description, "html.parser").get_text(" ", strip=True)

    main = soup.find("main") or soup.body or soup
    text = clean_text(main.get_text(" "))
    stop_markers = ["JobbMatch", "Om arbeidsgiveren", "Spørsmål om stillingen", "Annonseinformasjon"]
    for marker in stop_markers:
        marker_index = text.find(marker)
        if marker_index > 500:
            text = text[:marker_index]
            break
    return text[:12000]
