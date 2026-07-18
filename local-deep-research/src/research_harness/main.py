from __future__ import annotations

import json
import socket
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .config import settings
from .db import Database
from .pipeline import Worker


settings.prepare()
db = Database(settings.db_path)
db.initialize()
worker = Worker(settings, db)


@asynccontextmanager
async def lifespan(_: FastAPI):
    worker.start()
    yield
    worker.stop()


app = FastAPI(title="Local Deep Research Harness", version="0.1.0", lifespan=lifespan)
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


class JobCreate(BaseModel):
    question: str = Field(min_length=10, max_length=10_000)
    provider: str = "ollama"
    use_web: bool = True
    include_x: bool = False
    seed_urls: list[str] = Field(default_factory=list)
    substack_feeds: list[str] = Field(default_factory=list)
    browser_fallback_urls: list[str] = Field(default_factory=list)

    @field_validator("provider")
    @classmethod
    def provider_is_supported(cls, value: str) -> str:
        if value not in {"ollama", "cloud", "hybrid"}:
            raise ValueError("provider must be ollama, cloud, or hybrid")
        return value

    @field_validator("seed_urls", "substack_feeds", "browser_fallback_urls")
    @classmethod
    def urls_are_http(cls, values: list[str]) -> list[str]:
        for value in values:
            if not value.startswith(("http://", "https://")):
                raise ValueError(f"URL must begin with http:// or https://: {value}")
        return values


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    value = dict(job)
    value["report_available"] = bool(job.get("report_path") and Path(job["report_path"]).exists())
    value.pop("report_path", None)
    value.pop("markdown_path", None)
    value.pop("canonical_json_path", None)
    return value


@app.get("/")
def index():
    return FileResponse(static_dir / "index.html")


@app.get("/api/health")
def health():
    def reachable(url: str) -> bool:
        try:
            return httpx.get(url, timeout=1.5).status_code < 500
        except httpx.HTTPError:
            return False

    hostname = socket.gethostname()
    local_ip = "unknown"
    try:
        local_ip = socket.gethostbyname(hostname)
    except OSError:
        pass
    return {
        "status": "ok",
        "hostname": hostname,
        "local_ip": local_ip,
        "phone_url": f"http://{local_ip}:{settings.port}" if local_ip != "unknown" else None,
        "ollama_reachable": reachable(f"{settings.ollama_url.rstrip('/')}/api/tags"),
        "searxng_reachable": reachable(f"{settings.searxng_url.rstrip('/')}/search?q=test&format=json"),
        "cloud_configured": bool(settings.cloud_api_key and settings.cloud_model),
        "x_configured": bool(settings.x_bearer_token and settings.x_user_id),
        "browser_fallback_configured": bool(settings.browser_cdp_url),
    }


@app.post("/api/jobs", status_code=201)
def create_job(request: JobCreate):
    options = request.model_dump(exclude={"question", "provider"})
    return public_job(db.create_job(request.question.strip(), request.provider, options))


@app.get("/api/jobs")
def list_jobs():
    return [public_job(job) for job in db.list_jobs()]


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    value = public_job(job)
    value["events"] = db.events(job_id)
    return value


@app.post("/api/jobs/{job_id}/stop")
def stop_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] in {"completed", "failed"}:
        raise HTTPException(409, f"Cannot stop a {job['status']} job")
    db.request_cancel(job_id)
    return public_job(db.get_job(job_id))


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job["status"] not in {"failed", "needs_attention"}:
        raise HTTPException(409, "Only failed or needs-attention jobs can be retried")
    db.retry(job_id)
    return public_job(db.get_job(job_id))


@app.get("/api/jobs/{job_id}/report")
def download_report(job_id: str):
    job = db.get_job(job_id)
    if not job or not job.get("report_path"):
        raise HTTPException(404, "Report is not available")
    path = Path(job["report_path"])
    if not path.exists() or path.parent.resolve() != settings.reports_dir.resolve():
        raise HTTPException(404, "Report file is missing")
    return FileResponse(path, media_type="application/pdf", filename=f"research-{job_id[:8]}.pdf")


def run() -> None:
    uvicorn.run("research_harness.main:app", host=settings.host, port=settings.port, reload=False)


if __name__ == "__main__":
    run()

