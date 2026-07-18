from pathlib import Path

from research_harness.config import Settings
from research_harness.db import Database
from research_harness.pipeline import ResearchPipeline
from research_harness.providers import OllamaProvider, provider_for
from research_harness.sources import Document


def test_lexical_ranking_prefers_relevant_document(tmp_path: Path):
    settings = Settings(data_dir=tmp_path / "data", private_corpus_dir=tmp_path / "private")
    settings.prepare()
    db = Database(settings.db_path)
    db.initialize()
    pipeline = ResearchPipeline(settings, db)
    relevant = Document.create(kind="web", title="Solar storage", url="https://a.test", content="solar battery storage " * 50)
    irrelevant = Document.create(kind="web", title="Cooking", url="https://b.test", content="pasta sauce " * 100)
    ranked = pipeline._rank("solar battery storage", [irrelevant, relevant], 2)
    assert ranked[0] is relevant


def test_end_to_end_pipeline_produces_valid_long_pdf(tmp_path: Path, monkeypatch):
    settings = Settings(
        data_dir=tmp_path / "data",
        private_corpus_dir=tmp_path / "private",
        min_report_pages=10,
        max_sources=4,
    )
    settings.prepare()
    (settings.private_corpus_dir / "evidence.md").write_text(
        ("Primary evidence describes durable queues, checkpoints, citations, and asynchronous workers. " * 350),
        encoding="utf-8",
    )
    db = Database(settings.db_path)
    db.initialize()
    job = db.create_job(
        "How should a durable asynchronous research system preserve evidence and recover from interruption?",
        "ollama",
        {"use_web": False, "include_x": False, "seed_urls": [], "substack_feeds": []},
    )
    claimed = db.claim_next_job()

    class FakeProvider:
        name = "fake"

        def structured(self, messages):
            return {
                "queries": ["durable research systems"],
                "sections": [f"Finding {index}" for index in range(1, 7)],
                "scope": "A bounded architecture review.",
                "risks": [],
            }

        def chat(self, messages, temperature=0.2):
            return ("The captured evidence supports durable queues, explicit checkpoints, and citation validation while retaining uncertainty [S1]. " * 85)

    monkeypatch.setattr("research_harness.pipeline.provider_for", lambda name, configured: FakeProvider())
    ResearchPipeline(settings, db).run(claimed)
    completed = db.get_job(job["id"])
    assert completed["status"] == "completed", completed["error"]
    assert Path(completed["report_path"]).exists()


def test_ollama_unavailable_becomes_recoverable_attention_state(tmp_path: Path):
    settings = Settings(
        data_dir=tmp_path / "data",
        private_corpus_dir=tmp_path / "private",
        ollama_url="http://127.0.0.1:9",
    )
    settings.prepare()
    (settings.private_corpus_dir / "source.md").write_text("Local evidence " * 200, encoding="utf-8")
    db = Database(settings.db_path)
    db.initialize()
    job = db.create_job(
        "What should happen when the configured local model service is unavailable?",
        "ollama",
        {"use_web": False, "include_x": False, "seed_urls": [], "substack_feeds": []},
    )
    ResearchPipeline(settings, db).run(db.claim_next_job())
    result = db.get_job(job["id"])
    assert result["status"] == "needs_attention"
    assert "Ollama request failed" in result["error"]


def test_local_provider_does_not_require_cloud_credentials(tmp_path: Path):
    settings = Settings(cloud_api_key="", cloud_model="", ollama_url="http://localhost:11434")
    assert isinstance(provider_for("ollama", settings), OllamaProvider)
