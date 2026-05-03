from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import configure_logging, load_settings
from src.db import JobStore
from src.fetch_email import EmailClient, EmailIngestionNotConfigured
from src.fetch_finn import FinnClient
from src.filters import hard_filter
from src.parser import JobListing
from src.scoring import JobScorer, ScoringUnavailable
from src.telegram import TelegramNotifier, build_message


LOGGER = logging.getLogger(__name__)


@dataclass
class CollectionStats:
    new_listings: list[JobListing]
    finn_jobs_fetched: int = 0
    linkedin_emails_scanned: int = 0
    linkedin_jobs_parsed: int = 0


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

    collection = _collect_new_listings(settings, finn, store)
    new_listings = collection.new_listings
    LOGGER.info(
        "Collection complete. finn_jobs_fetched=%s linkedin_emails_scanned=%s "
        "linkedin_jobs_parsed=%s new_after_dedup=%s",
        collection.finn_jobs_fetched,
        collection.linkedin_emails_scanned,
        collection.linkedin_jobs_parsed,
        len(new_listings),
    )

    processed = 0
    alerted = 0
    hard_filtered = 0
    passed_hard_filter = 0
    for listing in new_listings[: settings.max_detail_fetches_per_run]:
        try:
            if listing.source == "finn":
                detailed = finn.fetch_detail(listing)
                if detailed is None:
                    continue
            else:
                detailed = listing

            filter_result = hard_filter(detailed)
            if not filter_result.include:
                hard_filtered += 1
                LOGGER.info("Skipping %s: %s", detailed.url, filter_result.reason)
                continue
            passed_hard_filter += 1

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

    LOGGER.info(
        "Run complete. hard_filtered=%s passed_hard_filter=%s scored=%s alerted=%s",
        hard_filtered,
        passed_hard_filter,
        processed,
        alerted,
    )
    return 0


def _collect_new_listings(settings, finn: FinnClient, store: JobStore) -> CollectionStats:
    stats = CollectionStats(new_listings=[])
    if settings.max_new_jobs_per_run <= 0:
        LOGGER.info("MAX_NEW_JOBS_PER_RUN is 0; skipping collection")
        return stats

    seen_this_run: set[tuple[str, str]] = set()

    if settings.enable_email_ingestion:
        if not settings.email_configured:
            LOGGER.warning("Email ingestion enabled but email settings are incomplete; skipping email source")
        else:
            try:
                email_result = EmailClient(
                    host=settings.email_host,
                    port=settings.email_port,
                    username=settings.email_username,
                    password=settings.email_password,
                    folder=settings.email_folder,
                    from_filter=settings.email_from_filter,
                    subject_filter=settings.email_subject_filter,
                    lookback_days=settings.email_lookback_days,
                    max_emails_per_run=settings.max_emails_per_run,
                ).fetch_linkedin_jobs()
                stats.linkedin_emails_scanned = email_result.emails_scanned
                stats.linkedin_jobs_parsed = len(email_result.jobs)
                _add_new_listings(email_result.jobs, store, seen_this_run, stats, settings.max_new_jobs_per_run)
            except EmailIngestionNotConfigured as exc:
                LOGGER.warning("%s", exc)
            except Exception:
                LOGGER.exception("Unexpected email ingestion failure")
    else:
        LOGGER.info("Email ingestion disabled")

    for search_url in settings.finn_search_urls:
        try:
            listings = finn.fetch_search_results(search_url, settings.finn_max_pages_per_search)
            stats.finn_jobs_fetched += len(listings)
        except Exception:
            LOGGER.exception("Failed fetching search URL %s", search_url)
            continue

        _add_new_listings(listings, store, seen_this_run, stats, settings.max_new_jobs_per_run)

    return stats


def _add_new_listings(
    listings: list[JobListing],
    store: JobStore,
    seen_this_run: set[tuple[str, str]],
    stats: CollectionStats,
    max_new_jobs: int,
) -> None:
    for listing in listings:
        if len(stats.new_listings) >= max_new_jobs:
            return
        key = (listing.source, listing.job_id)
        if key in seen_this_run:
            continue
        seen_this_run.add(key)
        inserted = store.upsert_seen(listing)
        if inserted:
            stats.new_listings.append(listing)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (RuntimeError, ValueError) as exc:
        configure_logging("ERROR")
        LOGGER.error("%s", exc)
        sys.exit(2)
