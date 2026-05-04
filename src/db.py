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
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL,
    score INTEGER,
    recommendation TEXT,
    alerted INTEGER NOT NULL DEFAULT 0,
    UNIQUE(source, job_id)
);

CREATE INDEX IF NOT EXISTS idx_jobs_alerted ON jobs(alerted);
CREATE INDEX IF NOT EXISTS idx_jobs_score ON jobs(score);
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

    def upsert_seen(self, listing: JobListing) -> bool:
        """Insert a listing if new, otherwise refresh last_seen. Returns True when inserted."""
        now = utc_now_iso()
        with self.connect() as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO jobs (
                        job_id, source, title, company, location, url, first_seen, last_seen
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        listing.job_id,
                        listing.source,
                        listing.title,
                        listing.company,
                        listing.location,
                        listing.url,
                        now,
                        now,
                    ),
                )
                return True
            except sqlite3.IntegrityError:
                conn.execute(
                    """
                    UPDATE jobs
                    SET last_seen = ?, title = ?, company = ?, location = ?, url = ?
                    WHERE source = ? AND job_id = ?
                    """,
                    (
                        now,
                        listing.title,
                        listing.company,
                        listing.location,
                        listing.url,
                        listing.source,
                        listing.job_id,
                    ),
                )
                return False

    def save_score(self, source: str, job_id: str, score: int, recommendation: str) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET score = ?, recommendation = ?
                WHERE source = ? AND job_id = ?
                """,
                (score, recommendation, source, job_id),
            )

    def mark_alerted(self, source: str, job_id: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE jobs SET alerted = 1 WHERE source = ? AND job_id = ?",
                (source, job_id),
            )
