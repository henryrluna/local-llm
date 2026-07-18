from pathlib import Path

from research_harness.db import Database


def test_job_lifecycle_is_durable(tmp_path: Path):
    path = tmp_path / "jobs.sqlite3"
    db = Database(path)
    db.initialize()
    job = db.create_job("What evidence answers this sufficiently detailed question?", "ollama", {"use_web": False})
    claimed = db.claim_next_job()
    assert claimed["id"] == job["id"]
    assert claimed["status"] == "planning"

    # A restart recovers active work rather than losing it.
    restarted = Database(path)
    restarted.initialize()
    assert restarted.get_job(job["id"])["status"] == "queued"
    assert restarted.claim_next_job()["id"] == job["id"]


def test_cancel_queued_job_is_terminal(tmp_path: Path):
    db = Database(tmp_path / "jobs.sqlite3")
    db.initialize()
    job = db.create_job("A sufficiently long research question for cancellation", "ollama", {})
    db.request_cancel(job["id"])
    cancelled = db.get_job(job["id"])
    assert cancelled["status"] == "failed"
    assert cancelled["cancel_requested"] is True

