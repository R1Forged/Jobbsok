from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from .parser import JobListing


SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    company TEXT,
    location TEXT,
    url TEXT NOT NULL,
    canonical_url TEXT,
    source_message_id TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    score INTEGER,
    recommendation TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    raw_ai_json TEXT,
    UNIQUE(source, job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_alerted ON jobs(alerted);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);

CREATE TABLE IF NOT EXISTS processed_emails (
    message_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    subject TEXT,
    from_email TEXT,
    processed_at TEXT NOT NULL,
    cleanup_action TEXT NOT NULL,
    status TEXT NOT NULL,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_processed_emails_status ON processed_emails(status);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class JobStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(SCHEMA)
            self._migrate_jobs_table(conn)

    def _migrate_jobs_table(self, conn: sqlite3.Connection) -> None:
        existing = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(jobs)").fetchall()
        }
        migrations = {
            "canonical_url": "ALTER TABLE jobs ADD COLUMN canonical_url TEXT",
            "source_message_id": "ALTER TABLE jobs ADD COLUMN source_message_id TEXT",
            "raw_ai_json": "ALTER TABLE jobs ADD COLUMN raw_ai_json TEXT",
        }
        for column, statement in migrations.items():
            if column not in existing:
                conn.execute(statement)
        conn.execute("UPDATE jobs SET canonical_url = url WHERE canonical_url IS NULL OR canonical_url = ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_jobs_canonical_url ON jobs(canonical_url)")

    def has_job(self, source: str, job_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM jobs WHERE source = ? AND job_id = ? LIMIT 1",
                (source, job_id),
            ).fetchone()
            return row is not None

    def needs_processing(self, source: str, job_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT score, recommendation
                FROM jobs
                WHERE source = ? AND job_id = ?
                LIMIT 1
                """,
                (source, job_id),
            ).fetchone()
            return row is not None and row["score"] is None and row["recommendation"] is None

    def job_finished_processing(self, listing: JobListing) -> bool:
        canonical_url = listing.canonical_url or listing.url
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT score, recommendation
                FROM jobs
                WHERE (source = ? AND job_id = ?) OR canonical_url = ?
                ORDER BY CASE WHEN source = ? AND job_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (
                    listing.source,
                    listing.job_id,
                    canonical_url,
                    listing.source,
                    listing.job_id,
                ),
            ).fetchone()
            return row is not None and (row["score"] is not None or row["recommendation"] is not None)

    def job_application_status(self, listing: JobListing, min_score: int) -> str:
        canonical_url = listing.canonical_url or listing.url
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT score, recommendation, alerted
                FROM jobs
                WHERE (source = ? AND job_id = ?) OR canonical_url = ?
                ORDER BY CASE WHEN source = ? AND job_id = ? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (
                    listing.source,
                    listing.job_id,
                    canonical_url,
                    listing.source,
                    listing.job_id,
                ),
            ).fetchone()
            if row is None or row["score"] is None:
                return "pending"
            if row["alerted"] or row["score"] >= min_score:
                return "interesting"
            return "uninteresting"

    def upsert_seen(self, listing: JobListing) -> bool:
        """Insert a listing if new, otherwise refresh last_seen. Returns True when inserted."""
        now = utc_now_iso()
        canonical_url = listing.canonical_url or listing.url
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT source, job_id
                FROM jobs
                WHERE canonical_url = ?
                LIMIT 1
                """,
                (canonical_url,),
            ).fetchone()
            if row and (row["source"], row["job_id"]) != (listing.source, listing.job_id):
                conn.execute(
                    """
                    UPDATE jobs
                    SET last_seen = ?, title = ?, company = ?, location = ?, url = ?,
                        source_message_id = COALESCE(NULLIF(?, ''), source_message_id)
                    WHERE source = ? AND job_id = ?
                    """,
                    (
                        now,
                        listing.title,
                        listing.company,
                        listing.location,
                        listing.url,
                        listing.source_message_id,
                        row["source"],
                        row["job_id"],
                    ),
                )
                return False
            try:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, source, title, company, location, url, canonical_url,
                        source_message_id, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        listing.job_id,
                        listing.source,
                        listing.title,
                        listing.company,
                        listing.location,
                        listing.url,
                        canonical_url,
                        listing.source_message_id,
                        now,
                        now,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                conn.execute(
                    """
                    UPDATE jobs
                    SET last_seen = ?, title = ?, company = ?, location = ?, url = ?,
                        canonical_url = ?, source_message_id = COALESCE(NULLIF(?, ''), source_message_id)
                    WHERE source = ? AND job_id = ?
                    """,
                    (
                        now,
                        listing.title,
                        listing.company,
                        listing.location,
                        listing.url,
                        canonical_url,
                        listing.source_message_id,
                        listing.source,
                        listing.job_id,
                    ),
                )
                return False

    def save_score(
        self,
        source: str,
        job_id: str,
        score: int,
        recommendation: str,
        raw_ai_json: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET score = ?, recommendation = ?, raw_ai_json = COALESCE(NULLIF(?, ''), raw_ai_json)
                WHERE source = ? AND job_id = ?
                """,
                (score, recommendation, raw_ai_json, source, job_id),
            )

    def mark_alerted(self, source: str, job_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET alerted = 1 WHERE source = ? AND job_id = ?",
                (source, job_id),
            )

    def email_already_processed(self, message_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM processed_emails
                WHERE message_id = ? AND status = 'processed'
                LIMIT 1
                """,
                (message_id,),
            ).fetchone()
            return row is not None

    def record_processed_email(
        self,
        message_id: str,
        source: str,
        subject: str,
        from_email: str,
        cleanup_action: str,
        status: str,
        error_message: str = "",
    ) -> None:
        now = utc_now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO processed_emails (
                    message_id, source, subject, from_email, processed_at,
                    cleanup_action, status, error_message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    source = excluded.source,
                    subject = excluded.subject,
                    from_email = excluded.from_email,
                    processed_at = excluded.processed_at,
                    cleanup_action = excluded.cleanup_action,
                    status = excluded.status,
                    error_message = excluded.error_message
                """,
                (
                    message_id,
                    source,
                    subject,
                    from_email,
                    now,
                    cleanup_action,
                    status,
                    error_message,
                ),
            )
