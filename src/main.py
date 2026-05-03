from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import configure_logging, load_settings
from src.db import JobStore
from src.fetch_finn import FinnClient
from src.filters import hard_filter
from src.parser import JobListing
from src.scoring import JobScorer, ScoringUnavailable
from src.telegram import TelegramNotifier, build_message


LOGGER = logging.getLogger(__name__)


def run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    settings.validate_for_run()

    db_path = settings.db_path
    if settings.dry_run:
        suffix = settings.db_path.suffix or ".sqlite"
        db_path = settings.db_path.with_name(f"{settings.db_path.stem}.dry-run{suffix}")
        LOGGER.info("DRY_RUN=true. Using separate database at %s", db_path)

    store = JobStore(db_path)
    finn = FinnClient(settings.request_delay_seconds, settings.http_timeout_seconds)
    scorer = JobScorer(settings.openai_api_key, settings.openai_model)
    notifier = TelegramNotifier(
        settings.telegram_bot_token,
        settings.telegram_chat_id,
        dry_run=settings.dry_run,
    )

    LOGGER.info(
        "Starting job agent. searches=%s max_new=%s max_details=%s min_score=%s dry_run=%s",
        len(settings.finn_search_urls),
        settings.max_new_jobs_per_run,
        settings.max_detail_fetches_per_run,
        settings.min_score,
        settings.dry_run,
    )

    new_listings = _collect_new_listings(settings, finn, store)
    LOGGER.info("Collected %s new listings for detail processing", len(new_listings))

    processed = 0
    alerted = 0
    hard_filtered = 0
    for listing in new_listings[: settings.max_detail_fetches_per_run]:
        try:
            detailed = finn.fetch_detail(listing)
            if detailed is None:
                continue

            filter_result = hard_filter(detailed)
            if not filter_result.include:
                hard_filtered += 1
                LOGGER.info("Skipping %s: %s", detailed.url, filter_result.reason)
                continue

            try:
                score = scorer.score(detailed)
            except ScoringUnavailable as exc:
                LOGGER.error("%s", exc)
                break
            store.save_score(detailed.source, detailed.job_id, score.score, score.recommendation)
            processed += 1

            if score.score < settings.min_score:
                LOGGER.info("Score below threshold for %s: %s", detailed.url, score.score)
                continue

            message = build_message(detailed, score)
            if notifier.send(message):
                store.mark_alerted(detailed.source, detailed.job_id)
                alerted += 1
        except Exception:
            LOGGER.exception("Failed processing listing %s", listing.url)
            continue

    LOGGER.info("Run complete. hard_filtered=%s scored=%s alerted=%s", hard_filtered, processed, alerted)
    return 0


def _collect_new_listings(settings, finn: FinnClient, store: JobStore) -> list[JobListing]:
    if settings.max_new_jobs_per_run <= 0:
        LOGGER.info("MAX_NEW_JOBS_PER_RUN is 0; skipping collection")
        return []

    collected: list[JobListing] = []
    seen_this_run: set[tuple[str, str]] = set()

    for search_url in settings.finn_search_urls:
        try:
            listings = finn.fetch_search_results(search_url, settings.finn_max_pages_per_search)
        except Exception:
            LOGGER.exception("Failed fetching search URL %s", search_url)
            continue

        for listing in listings:
            key = (listing.source, listing.job_id)
            if key in seen_this_run:
                continue
            seen_this_run.add(key)
            inserted = store.upsert_seen(listing)
            if inserted:
                collected.append(listing)
                if len(collected) >= settings.max_new_jobs_per_run:
                    return collected

    return collected


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (RuntimeError, ValueError) as exc:
        configure_logging("ERROR")
        LOGGER.error("%s", exc)
        sys.exit(2)
