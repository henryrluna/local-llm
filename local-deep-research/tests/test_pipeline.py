from pathlib import Path

from research_harness.config import Settings
from research_harness.db import Database
from research_harness.pipeline import ResearchPipeline
from research_harness.providers import OllamaProvider, provider_for
from research_harness.reporting import main_report_word_count, render_pdf
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


def test_executive_summary_is_hard_capped_to_one_page_budget():
    original = "word " * 450
    limited = ResearchPipeline._limit_words(original, 400)
    assert len(limited.split()) == 400


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
            prompt = messages[-1]["content"]
            if "editing memo" in prompt.lower():
                return "Keep each architectural fact in its most relevant section and remove repeated specifications elsewhere."
            if "300-400 word executive summary" in prompt:
                return ("The captured evidence supports durable queues and explicit checkpoints while retaining material uncertainty [1]. " * 35)
            if "independently audit" in prompt.lower():
                return "The substantive claims remain linked to the captured evidence, with implementation uncertainty clearly identified [1]."
            return ("The captured evidence supports durable queues, explicit checkpoints, and citation validation while retaining uncertainty [1]. " * 55)

    monkeypatch.setattr("research_harness.pipeline.provider_for", lambda name, configured: FakeProvider())
    ResearchPipeline(settings, db).run(claimed)
    completed = db.get_job(job["id"])
    assert completed["status"] == "completed", completed["error"]
    assert Path(completed["report_path"]).exists()
    markdown = Path(completed["markdown_path"]).read_text(encoding="utf-8")
    headings = [
        "# Executive Summary",
        "# Research Question and Scope",
        "# Research Approach and Queries Executed",
        "# Full Research Report",
        "# Contradictions, Uncertainty, and Limitations",
        "# Bibliography",
        "# Collection Notes",
        "# Independent Evidence and Citation Audit",
    ]
    assert [markdown.index(heading) for heading in headings] == sorted(markdown.index(heading) for heading in headings)
    assert "Appendix" not in markdown
    assert "[S1]" not in markdown
    assert main_report_word_count(markdown) >= settings.min_main_report_words


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


def test_ollama_structured_generation_is_bounded_and_disables_thinking(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"message": {"content": '{"queries": [], "sections": []}'}}

    class FakeClient:
        def __init__(self, **kwargs):
            captured["timeout"] = kwargs["timeout"]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("research_harness.providers.httpx.Client", FakeClient)
    settings = Settings(
        ollama_url="http://ollama:11434",
        ollama_model="test-chat-model",
        ollama_timeout_seconds=3600,
        ollama_num_ctx=32768,
        ollama_structured_max_tokens=1024,
        ollama_keep_alive="12h",
    )

    result = OllamaProvider(settings).structured([{"role": "user", "content": "Plan this."}])

    assert result == {"queries": [], "sections": []}
    assert captured["url"] == "http://ollama:11434/api/chat"
    assert captured["payload"]["format"] == "json"
    assert captured["payload"]["think"] is False
    assert captured["payload"]["keep_alive"] == "12h"
    assert captured["payload"]["options"]["num_ctx"] == 32768
    assert captured["payload"]["options"]["num_predict"] == 1024


def test_ollama_streaming_reports_live_and_final_generation_metrics(monkeypatch):
    captured = {}

    class FakeStreamResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield '{"message":{"content":"Hello"},"done":false}'
            yield '{"message":{"content":" world"},"done":false}'
            yield '{"message":{"content":""},"done":true,"prompt_eval_count":17,"eval_count":2,"eval_duration":500000000}'

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def stream(self, method, url, json):
            captured["method"] = method
            captured["url"] = url
            captured["payload"] = json
            return FakeStreamResponse()

    monkeypatch.setattr("research_harness.providers.httpx.Client", FakeClient)
    settings = Settings(
        ollama_url="http://ollama:11434",
        ollama_model="test-chat-model",
        progress_update_seconds=0,
    )
    updates = []
    provider = OllamaProvider(settings)
    provider.set_progress_callback(updates.append)

    result = provider.chat([{"role": "user", "content": "Say hello."}])

    assert result == "Hello world"
    assert captured["payload"]["stream"] is True
    assert updates[0]["estimated"] is True
    assert updates[-1]["done"] is True
    assert updates[-1]["prompt_tokens"] == 17
    assert updates[-1]["generated_tokens"] == 2
    assert updates[-1]["tokens_per_second"] == 4


def test_uncensored_oss_model_uses_raw_final_channel_prompt(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"response": "ready"}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def post(self, url, json):
            captured["url"] = url
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr("research_harness.providers.httpx.Client", FakeClient)
    provider = OllamaProvider(Settings(ollama_model="openai-20b-neoplus-uncensored:latest"))

    result = provider.chat([{"role": "user", "content": "Reply ready."}])

    assert result == "ready"
    assert captured["url"].endswith("/api/generate")
    assert captured["payload"]["raw"] is True
    assert captured["payload"]["prompt"].endswith("<|assistant|><|channel|>final<|message|>")
    assert "<|user|>Reply ready.<|end|>" in captured["payload"]["prompt"]


def test_pdf_renderer_does_not_include_quotes_after_urls_in_link_attributes(tmp_path: Path):
    markdown = "# Collection Notes\n\nUnable to retrieve https://example.test/path: Client error '403 Forbidden' for url 'https://example.test/path'."
    output = tmp_path / "report.pdf"

    pages = render_pdf(markdown, output, "URL escaping regression")

    assert pages >= 1
    assert output.exists()
