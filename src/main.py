from __future__ import annotations

import logging
import platform
import sys
from dataclasses import dataclass
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.config import configure_logging, load_settings
from src.db import JobStore
from src.fetch_finn import FinnClient
from src.fetch_gmail import GmailClient, GmailEmail, GmailIngestionNotConfigured
from src.filters import hard_filter
from src.parser import JobListing
from src.scoring import JobScorer, ScoringUnavailable
from src.telegram import TelegramNotifier, build_message


LOGGER = logging.getLogger(__name__)


@dataclass
class CollectionStats:
    new_listings: list[JobListing]
    finn_jobs_fetched: int = 0
    gmail_emails_found: int = 0
    gmail_emails_processed: int = 0
    gmail_jobs_parsed: int = 0
    gmail_emails_skipped_error: int = 0
    gmail_emails: list[GmailEmail] | None = None


def run() -> int:
    settings = load_settings()
    configure_logging(settings.log_level)
    settings.validate_for_run()
    LOGGER.info("Current working directory: %s", Path.cwd())
    LOGGER.info("Python version: %s", sys.version.replace("\n", " "))
    LOGGER.info("Platform: %s", platform.platform())
    LOGGER.info("Loaded config keys: %s", settings.safe_config_snapshot())
    LOGGER.info(
        "Enabled sources: FINN=%s Gmail=%s",
        bool(settings.finn_search_urls),
        settings.enable_gmail,
    )

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
        "Starting job agent. mode=%s searches=%s pages_per_search=%s max_new=%s max_details=%s min_score=%s dry_run=%s",
        "initial_backfill" if settings.initial_backfill else "normal",
        len(settings.finn_search_urls),
        settings.finn_pages_this_run,
        settings.max_new_jobs_this_run,
        settings.max_detail_fetches_this_run,
        settings.min_score,
        settings.dry_run,
    )

    collection = _collect_new_listings(settings, finn, store)
    new_listings = collection.new_listings
    LOGGER.info(
        "Collection complete. finn_jobs_fetched=%s gmail_emails_found=%s "
        "gmail_emails_processed=%s gmail_jobs_parsed=%s gmail_emails_skipped_error=%s new_after_dedup=%s",
        collection.finn_jobs_fetched,
        collection.gmail_emails_found,
        collection.gmail_emails_processed,
        collection.gmail_jobs_parsed,
        collection.gmail_emails_skipped_error,
        len(new_listings),
    )

    processed = 0
    alerted = 0
    hard_filtered = 0
    passed_hard_filter = 0
    fatal_email_ids: set[str] = set()
    scoring_unavailable = False
    for listing in new_listings[: settings.max_detail_fetches_this_run]:
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
                store.save_score(detailed.source, detailed.job_id, 0, "DROPP")
                LOGGER.info("Skipping %s: %s", detailed.url, filter_result.reason)
                continue
            passed_hard_filter += 1

            try:
                score = scorer.score(detailed)
            except ScoringUnavailable as exc:
                LOGGER.error("%s", exc)
                if detailed.source_message_id:
                    fatal_email_ids.add(detailed.source_message_id)
                scoring_unavailable = True
                break
            store.save_score(
                detailed.source,
                detailed.job_id,
                score.score,
                score.recommendation,
                score.raw_ai_json,
            )
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
            if listing.source_message_id:
                fatal_email_ids.add(listing.source_message_id)
            continue

    cleanup_counts = _cleanup_processed_gmail_emails(
        settings=settings,
        store=store,
        gmail_emails=collection.gmail_emails or [],
        fatal_email_ids=fatal_email_ids,
        scoring_unavailable=scoring_unavailable,
    )
    LOGGER.info(
        "Run complete. hard_filtered=%s passed_hard_filter=%s scored=%s alerted=%s "
        "gmail_emails_archived=%s gmail_emails_trashed=%s gmail_emails_left=%s gmail_emails_skipped_error=%s",
        hard_filtered,
        passed_hard_filter,
        processed,
        alerted,
        cleanup_counts["archived"],
        cleanup_counts["trashed"],
        cleanup_counts["left"],
        cleanup_counts["skipped_error"],
    )
    return 0


def _collect_new_listings(settings, finn: FinnClient, store: JobStore) -> CollectionStats:
    stats = CollectionStats(new_listings=[])
    max_new_jobs = settings.max_new_jobs_this_run
    if max_new_jobs <= 0:
        LOGGER.info("Max new jobs for this run is 0; skipping collection")
        return stats

    seen_this_run: set[tuple[str, str]] = set()

    if settings.enable_gmail:
        try:
            gmail_result = GmailClient(
                credentials_path=settings.gmail_credentials_path,
                token_path=settings.gmail_token_path,
                query=settings.gmail_query,
                max_emails_per_run=settings.gmail_max_emails_per_run,
            ).fetch_job_alerts()
            stats.gmail_emails_found = gmail_result.emails_found
            stats.gmail_emails_processed = gmail_result.emails_processed
            stats.gmail_jobs_parsed = len(gmail_result.jobs)
            stats.gmail_emails_skipped_error = gmail_result.emails_skipped_error
            stats.gmail_emails = []
            for email_record in gmail_result.emails:
                if email_record.error_message:
                    store.record_processed_email(
                        email_record.message_id,
                        email_record.source,
                        email_record.subject,
                        email_record.from_email,
                        settings.gmail_cleanup_action,
                        "error",
                        email_record.error_message,
                    )
                    continue
                stats.gmail_emails.append(email_record)
                _add_new_listings(
                    email_record.jobs,
                    store,
                    seen_this_run,
                    stats,
                    max_new_jobs,
                    include_existing_unprocessed=settings.initial_backfill,
                )
        except GmailIngestionNotConfigured as exc:
            if settings.require_gmail:
                raise RuntimeError(f"{exc} Gmail source is required for this run.") from exc
            LOGGER.warning("%s Gmail source skipped; FINN source will continue.", exc)
        except Exception as exc:
            if settings.require_gmail:
                raise RuntimeError("Unexpected Gmail ingestion failure; Gmail source is required for this run.") from exc
            LOGGER.exception("Unexpected Gmail ingestion failure")
    else:
        LOGGER.info("Gmail ingestion disabled")

    for search_url in settings.finn_search_urls:
        if len(stats.new_listings) >= max_new_jobs:
            LOGGER.info("Reached collection cap before fetching remaining FINN searches")
            break
        try:
            listings = finn.fetch_search_results(search_url, settings.finn_pages_this_run)
            stats.finn_jobs_fetched += len(listings)
        except Exception:
            LOGGER.exception("Failed fetching search URL %s", search_url)
            continue

        _add_new_listings(
            listings,
            store,
            seen_this_run,
            stats,
            max_new_jobs,
            include_existing_unprocessed=settings.initial_backfill,
        )

    return stats


def _cleanup_processed_gmail_emails(
    settings,
    store: JobStore,
    gmail_emails: list[GmailEmail],
    fatal_email_ids: set[str],
    scoring_unavailable: bool,
) -> dict[str, int]:
    counts = {"archived": 0, "trashed": 0, "left": 0, "skipped_error": 0}
    if not settings.enable_gmail or not gmail_emails:
        return counts
    gmail = GmailClient(
        credentials_path=settings.gmail_credentials_path,
        token_path=settings.gmail_token_path,
        query=settings.gmail_query,
        max_emails_per_run=settings.gmail_max_emails_per_run,
    )
    action = settings.gmail_cleanup_action
    for email_record in gmail_emails:
        if email_record.error_message:
            counts["skipped_error"] += 1
            continue
        if not email_record.jobs:
            counts["left"] += 1
            store.record_processed_email(
                email_record.message_id,
                email_record.source,
                email_record.subject,
                email_record.from_email,
                "none",
                "skipped",
                "No job links extracted; message left untouched.",
            )
            continue
        if scoring_unavailable or email_record.message_id in fatal_email_ids:
            counts["skipped_error"] += 1
            store.record_processed_email(
                email_record.message_id,
                email_record.source,
                email_record.subject,
                email_record.from_email,
                "none",
                "error",
                "Job processing failed; message left untouched.",
            )
            continue
        job_statuses = [
            (job, store.job_application_status(job, settings.min_score))
            for job in email_record.jobs
        ]
        pending_jobs = [job for job, status in job_statuses if status == "pending"]
        if pending_jobs:
            counts["left"] += 1
            store.record_processed_email(
                email_record.message_id,
                email_record.source,
                email_record.subject,
                email_record.from_email,
                "none",
                "skipped",
                f"{len(pending_jobs)} Gmail job(s) were not fully scored yet; message left untouched.",
            )
            continue
        unalerted_interesting_jobs = [job for job, status in job_statuses if status == "needs_alert"]
        if unalerted_interesting_jobs:
            counts["left"] += 1
            store.record_processed_email(
                email_record.message_id,
                email_record.source,
                email_record.subject,
                email_record.from_email,
                "none",
                "processed",
                f"{len(unalerted_interesting_jobs)} Gmail job(s) met the alert threshold but were not alerted; message left in inbox.",
            )
            continue
        if settings.dry_run:
            counts["left"] += 1
            store.record_processed_email(
                email_record.message_id,
                email_record.source,
                email_record.subject,
                email_record.from_email,
                "none",
                "processed",
                f"DRY_RUN=true; all Gmail jobs were below threshold or alerted; would have applied cleanup action {action}.",
            )
            LOGGER.info(
                "DRY_RUN=true. Gmail message_id=%s left untouched; all jobs below threshold or alerted; would have applied cleanup action=%s",
                email_record.message_id,
                action,
            )
            continue
        try:
            if gmail.cleanup_message(email_record.message_id, action):
                if action == "trash":
                    counts["trashed"] += 1
                elif action == "archive":
                    counts["archived"] += 1
                else:
                    counts["left"] += 1
                store.record_processed_email(
                    email_record.message_id,
                    email_record.source,
                    email_record.subject,
                    email_record.from_email,
                    action,
                    "processed",
                )
        except GmailIngestionNotConfigured as exc:
            LOGGER.warning("%s Gmail cleanup skipped.", exc)
            counts["skipped_error"] += 1
        except Exception as exc:
            LOGGER.exception("Gmail cleanup failed for message_id=%s", email_record.message_id)
            counts["skipped_error"] += 1
            store.record_processed_email(
                email_record.message_id,
                email_record.source,
                email_record.subject,
                email_record.from_email,
                "none",
                "error",
                str(exc),
            )
    return counts


def _add_new_listings(
    listings: list[JobListing],
    store: JobStore,
    seen_this_run: set[tuple[str, str]],
    stats: CollectionStats,
    max_new_jobs: int,
    include_existing_unprocessed: bool = False,
) -> None:
    for listing in listings:
        if len(stats.new_listings) >= max_new_jobs:
            return
        key = (listing.source, listing.job_id)
        if key in seen_this_run:
            continue
        seen_this_run.add(key)
        inserted = store.upsert_seen(listing)
        if inserted or store.needs_processing(listing.source, listing.job_id):
            stats.new_listings.append(listing)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except (RuntimeError, ValueError) as exc:
        configure_logging("ERROR")
        LOGGER.error("%s", exc)
        sys.exit(2)
