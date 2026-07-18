from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


ACTIVE_STATES = ("planning", "collecting", "extracting", "synthesizing", "rendering")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA foreign_keys=ON")
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    question TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress INTEGER NOT NULL DEFAULT 0,
                    status_message TEXT NOT NULL DEFAULT '',
                    options_json TEXT NOT NULL DEFAULT '{}',
                    checkpoint_json TEXT NOT NULL DEFAULT '{}',
                    canonical_json_path TEXT,
                    markdown_path TEXT,
                    report_path TEXT,
                    error TEXT,
                    cancel_requested INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS sources (
                    id TEXT NOT NULL,
                    job_id TEXT NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL,
                    author TEXT,
                    published_at TEXT,
                    retrieved_at TEXT NOT NULL,
                    content_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    PRIMARY KEY(job_id, id)
                );
                CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_at);
                CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id, id);
                """
            )
            placeholders = ",".join("?" for _ in ACTIVE_STATES)
            db.execute(
                f"UPDATE jobs SET status='queued', status_message='Resuming after service restart', updated_at=? WHERE status IN ({placeholders})",
                (utcnow(), *ACTIVE_STATES),
            )

    def create_job(self, question: str, provider: str, options: dict[str, Any]) -> dict[str, Any]:
        job_id = str(uuid.uuid4())
        now = utcnow()
        with self.connect() as db:
            db.execute(
                "INSERT INTO jobs(id,question,provider,status,status_message,options_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                (job_id, question, provider, "queued", "Waiting for worker", json.dumps(options), now, now),
            )
            db.execute(
                "INSERT INTO events(job_id,level,message,created_at) VALUES(?,?,?,?)",
                (job_id, "info", "Research job queued", now),
            )
        return self.get_job(job_id)

    def row_to_dict(self, row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        result = dict(row)
        for field in ("options_json", "checkpoint_json"):
            result[field[:-5]] = json.loads(result.pop(field) or "{}")
        result["cancel_requested"] = bool(result["cancel_requested"])
        return result

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            return self.row_to_dict(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())

    def list_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        return [self.row_to_dict(row) for row in rows]

    def claim_next_job(self) -> dict[str, Any] | None:
        with self._lock, self.connect() as db:
            row = db.execute(
                "SELECT id FROM jobs WHERE status='queued' AND cancel_requested=0 ORDER BY created_at LIMIT 1"
            ).fetchone()
            if not row:
                return None
            now = utcnow()
            changed = db.execute(
                "UPDATE jobs SET status='planning', progress=5, status_message='Planning research', started_at=COALESCE(started_at,?), updated_at=? WHERE id=? AND status='queued'",
                (now, now, row["id"]),
            ).rowcount
            if not changed:
                return None
        self.add_event(row["id"], "info", "Worker claimed job")
        return self.get_job(row["id"])

    def update_job(self, job_id: str, **values: Any) -> None:
        if not values:
            return
        if "checkpoint" in values:
            values["checkpoint_json"] = json.dumps(values.pop("checkpoint"))
        values["updated_at"] = utcnow()
        fields = ",".join(f"{key}=?" for key in values)
        with self.connect() as db:
            db.execute(f"UPDATE jobs SET {fields} WHERE id=?", (*values.values(), job_id))

    def transition(self, job_id: str, status: str, progress: int, message: str) -> None:
        self.update_job(job_id, status=status, progress=progress, status_message=message)
        self.add_event(job_id, "info", message)

    def add_event(self, job_id: str, level: str, message: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO events(job_id,level,message,created_at) VALUES(?,?,?,?)",
                (job_id, level, message, utcnow()),
            )

    def events(self, job_id: str) -> list[dict[str, Any]]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM events WHERE job_id=? ORDER BY id", (job_id,)).fetchall()
        return [dict(row) for row in rows]

    def request_cancel(self, job_id: str) -> None:
        job = self.get_job(job_id)
        if job and job["status"] == "queued":
            self.update_job(job_id, cancel_requested=1, status="failed", status_message="Cancelled", error="Job cancelled by user", completed_at=utcnow())
        else:
            self.update_job(job_id, cancel_requested=1, status_message="Cancellation requested")
        self.add_event(job_id, "warning", "Cancellation requested")

    def retry(self, job_id: str) -> None:
        self.update_job(
            job_id,
            status="queued",
            progress=0,
            status_message="Queued for retry",
            error=None,
            cancel_requested=0,
            completed_at=None,
        )
        self.add_event(job_id, "info", "Job queued for retry")

    def insert_source(self, job_id: str, source: dict[str, Any]) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT OR REPLACE INTO sources
                (id,job_id,kind,title,url,author,published_at,retrieved_at,content_path,content_hash,metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    source["id"], job_id, source["kind"], source["title"], source["url"],
                    source.get("author"), source.get("published_at"), source["retrieved_at"],
                    source["content_path"], source["content_hash"], json.dumps(source.get("metadata", {})),
                ),
            )

