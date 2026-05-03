from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .parser import JobListing, parse_detail_page, parse_search_results


LOGGER = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0 Safari/537.36 personal-job-monitor/1.0"
)


class FinnClient:
    def __init__(self, request_delay_seconds: float, timeout_seconds: int) -> None:
        self.request_delay_seconds = request_delay_seconds
        self.timeout_seconds = timeout_seconds
        self._last_request_at = 0.0
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "nb-NO,nb;q=0.9,en;q=0.7",
            }
        )
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=("GET",),
            respect_retry_after_header=True,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    def fetch_search_results(self, search_url: str, max_pages: int) -> list[JobListing]:
        listings: list[JobListing] = []
        seen: set[str] = set()
        for page_number in range(1, max_pages + 1):
            page_url = _with_page(search_url, page_number)
            html = self._get(page_url)
            if not html:
                continue
            page_listings = parse_search_results(html, page_url)
            LOGGER.info("Parsed %s listings from %s", len(page_listings), page_url)
            for listing in page_listings:
                if listing.job_id not in seen:
                    listings.append(listing)
                    seen.add(listing.job_id)
        return listings

    def fetch_detail(self, listing: JobListing) -> JobListing | None:
        html = self._get(listing.url)
        if not html:
            return None
        return parse_detail_page(html, listing)

    def _get(self, url: str) -> str | None:
        self._respect_delay()
        LOGGER.info("Fetching %s", url)
        try:
            response = self.session.get(url, timeout=self.timeout_seconds)
        except requests.RequestException:
            LOGGER.exception("Request failed for %s", url)
            return None

        if response.status_code in {401, 403, 429}:
            LOGGER.warning(
                "FINN returned %s for %s. Not retrying with bypass behavior.",
                response.status_code,
                url,
            )
            return None
        if response.status_code >= 400:
            LOGGER.warning("FINN returned HTTP %s for %s", response.status_code, url)
            return None
        return response.text

    def _respect_delay(self) -> None:
        elapsed = time.monotonic() - self._last_request_at
        remaining = self.request_delay_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
        self._last_request_at = time.monotonic()


def _with_page(url: str, page_number: int) -> str:
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if page_number <= 1:
        query.pop("page", None)
    else:
        query["page"] = str(page_number)
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))
