from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="HARNESS_", env_file=".env", extra="ignore")

    host: str = "0.0.0.0"
    port: int = 8787
    public_base_url: str = "http://localhost:8787"
    data_dir: Path = Path("./data")
    min_report_pages: int = 10
    max_sources: int = 24
    max_evidence_chars: int = 60_000
    worker_poll_seconds: float = 1.0

    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "hf.co/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF:Q4_K_M"
    ollama_embed_model: str = "nomic-embed-text"
    cloud_base_url: str = "https://api.openai.com/v1"
    cloud_model: str = ""
    cloud_embed_model: str = "text-embedding-3-small"
    cloud_api_key: str = ""

    searxng_url: str = "http://localhost:8081"
    substack_feeds: str = ""
    x_bearer_token: str = ""
    x_user_id: str = ""
    x_max_accounts: int = 100
    x_posts_per_account: int = 5
    private_corpus_dir: Path = Path("./private-corpus")
    browser_cdp_url: str = ""

    ntfy_url: str = ""
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    notify_email_from: str = ""
    notify_email_to: str = ""

    @property
    def db_path(self) -> Path:
        return self.data_dir / "harness.sqlite3"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def captures_dir(self) -> Path:
        return self.data_dir / "captures"

    def prepare(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)
        self.captures_dir.mkdir(parents=True, exist_ok=True)
        self.private_corpus_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
