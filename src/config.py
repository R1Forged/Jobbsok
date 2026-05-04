from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _truthy(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _int_env(name: str, default: int, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = int(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be an integer, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}, got {value}")
    return value


def _float_env(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} must be a number, got {raw!r}") from exc
    if minimum is not None and value < minimum:
        raise ValueError(f"{name} must be >= {minimum}, got {value}")
    return value


def _csv_env(name: str) -> list[str]:
    raw = os.getenv(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    openai_api_key: str
    telegram_bot_token: str
    telegram_chat_id: str
    finn_search_urls: list[str]
    min_score: int = 75
    request_delay_seconds: float = 3.0
    max_detail_fetches_per_run: int = 20
    max_new_jobs_per_run: int = 20
    dry_run: bool = False
    db_path: Path = Path("data/jobs.sqlite")
    openai_model: str = "gpt-4.1-mini"
    finn_max_pages_per_search: int = 3
    initial_backfill: bool = False
    backfill_max_pages: int = 5
    backfill_max_detail_fetches: int = 100
    http_timeout_seconds: int = 20
    log_level: str = "INFO"
    enable_email_ingestion: bool = False
    email_host: str = ""
    email_port: int = 993
    email_username: str = ""
    email_password: str = ""
    email_folder: str = "INBOX"
    email_from_filter: str = "jobs-noreply@linkedin.com"
    email_subject_filter: str = "job"
    email_lookback_days: int = 7
    max_emails_per_run: int = 20

    def validate_for_run(self) -> None:
        missing: list[str] = []
        if not self.openai_api_key:
            missing.append("OPENAI_API_KEY")
        if not self.finn_search_urls:
            missing.append("FINN_SEARCH_URLS")
        if not self.dry_run:
            if not self.telegram_bot_token:
                missing.append("TELEGRAM_BOT_TOKEN")
            if not self.telegram_chat_id:
                missing.append("TELEGRAM_CHAT_ID")
        if missing:
            raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
        if not self.dry_run and not re.fullmatch(r"-?\d+", self.telegram_chat_id):
            raise RuntimeError(
                "TELEGRAM_CHAT_ID must be the numeric chat id from Telegram getUpdates, "
                "not a t.me link, bot username, or @handle. Run: python scripts/telegram_setup.py"
            )

    @property
    def email_configured(self) -> bool:
        return bool(self.email_host and self.email_username and self.email_password)

    @property
    def finn_pages_this_run(self) -> int:
        return self.backfill_max_pages if self.initial_backfill else self.finn_max_pages_per_search

    @property
    def max_new_jobs_this_run(self) -> int:
        return self.backfill_max_detail_fetches if self.initial_backfill else self.max_new_jobs_per_run

    @property
    def max_detail_fetches_this_run(self) -> int:
        return self.backfill_max_detail_fetches if self.initial_backfill else self.max_detail_fetches_per_run


def load_settings(env_file: str | Path | None = ".env") -> Settings:
    if env_file:
        load_dotenv(env_file)

    return Settings(
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", "").strip(),
        finn_search_urls=_csv_env("FINN_SEARCH_URLS"),
        min_score=_int_env("MIN_SCORE", 75, minimum=0, maximum=100),
        request_delay_seconds=_float_env("REQUEST_DELAY_SECONDS", 3.0, minimum=0),
        max_detail_fetches_per_run=_int_env("MAX_DETAIL_FETCHES_PER_RUN", 20, minimum=0),
        max_new_jobs_per_run=_int_env("MAX_NEW_JOBS_PER_RUN", 20, minimum=0),
        dry_run=_truthy(os.getenv("DRY_RUN"), default=False),
        db_path=Path(os.getenv("DB_PATH", "data/jobs.sqlite")),
        openai_model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip() or "gpt-4.1-mini",
        finn_max_pages_per_search=_int_env("FINN_MAX_PAGES_PER_SEARCH", 3, minimum=1, maximum=3),
        initial_backfill=_truthy(os.getenv("INITIAL_BACKFILL"), default=False),
        backfill_max_pages=_int_env("BACKFILL_MAX_PAGES", 5, minimum=1, maximum=10),
        backfill_max_detail_fetches=_int_env("BACKFILL_MAX_DETAIL_FETCHES", 100, minimum=0),
        http_timeout_seconds=_int_env("HTTP_TIMEOUT_SECONDS", 20, minimum=5),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
        enable_email_ingestion=_truthy(os.getenv("ENABLE_EMAIL_INGESTION"), default=False),
        email_host=os.getenv("EMAIL_HOST", "").strip(),
        email_port=_int_env("EMAIL_PORT", 993, minimum=1),
        email_username=os.getenv("EMAIL_USERNAME", "").strip(),
        email_password=os.getenv("EMAIL_PASSWORD", "").strip(),
        email_folder=os.getenv("EMAIL_FOLDER", "INBOX").strip() or "INBOX",
        email_from_filter=os.getenv("EMAIL_FROM_FILTER", "jobs-noreply@linkedin.com").strip(),
        email_subject_filter=os.getenv("EMAIL_SUBJECT_FILTER", "job").strip(),
        email_lookback_days=_int_env("EMAIL_LOOKBACK_DAYS", 7, minimum=1),
        max_emails_per_run=_int_env("MAX_EMAILS_PER_RUN", 20, minimum=0),
    )


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
