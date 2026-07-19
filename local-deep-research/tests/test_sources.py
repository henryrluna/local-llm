from pathlib import Path

import pytest

from research_harness.config import Settings
from research_harness.sources import BingConnector, Document, LocalCorpusConnector, NeedsAttention, SearxngConnector, SourceError, WebSearchConnector, XConnector, deduplicate, requires_browser_attention


def test_searxng_retries_explicit_fallback_engines(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params):
            calls.append(params)
            if len(calls) == 1:
                return FakeResponse({"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]})
            return FakeResponse({"results": [{"title": "Test result", "url": "https://example.test", "content": "Evidence"}]})

    monkeypatch.setattr("research_harness.sources.httpx.Client", FakeClient)
    results = SearxngConnector(Settings(searxng_url="http://searxng:8080")).search("test")
    assert results[0]["url"] == "https://example.test"
    assert calls[1]["engines"] == "yandex,presearch,dogpile,privacywall"


def test_searxng_filters_irrelevant_results_before_returning(monkeypatch):
    class FakeResponse:
        def __init__(self, results):
            self.results = results

        def raise_for_status(self):
            return None

        def json(self):
            return {"results": self.results, "unresponsive_engines": []}

    class FakeClient:
        calls = 0

        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params):
            FakeClient.calls += 1
            if FakeClient.calls == 1:
                return FakeResponse([{"title": "Saint Peter", "url": "https://irrelevant.test", "content": "Biblical biography"}])
            return FakeResponse([{"title": "Peter Thiel political influence", "url": "https://relevant.test", "content": "Funding networks"}])

    monkeypatch.setattr("research_harness.sources.httpx.Client", FakeClient)
    results = SearxngConnector(Settings()).search("Peter Theil political influence")
    assert [result["url"] for result in results] == ["https://relevant.test"]


def test_searxng_zero_results_is_an_error(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": [], "unresponsive_engines": [["bing", "rate limit"]]}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params):
            return FakeResponse()

    monkeypatch.setattr("research_harness.sources.httpx.Client", FakeClient)
    with pytest.raises(SourceError, match="zero results"):
        SearxngConnector(Settings()).search("test")


def test_bing_reads_keyless_rss_results(monkeypatch):
    class FakeResponse:
        content = b"""<?xml version='1.0'?><rss version='2.0'><channel><item><title>Useful result</title><link>https://example.test/evidence</link><description>Useful evidence summary</description></item></channel></rss>"""

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def get(self, url, params):
            assert params["format"] == "rss"
            return FakeResponse()

    monkeypatch.setattr("research_harness.sources.httpx.Client", FakeClient)
    results = BingConnector(Settings()).search("useful evidence")
    assert results == [{
        "title": "Useful result",
        "url": "https://example.test/evidence",
        "snippet": "Useful evidence summary",
        "provider": "bing",
    }]


def test_web_search_falls_back_from_searxng_to_bing():
    connector = WebSearchConnector(Settings())

    class FailingSearch:
        def search(self, query, limit):
            raise SourceError("SearXNG unavailable")

    class WorkingSearch:
        def search(self, query, limit):
            return [{"title": "Fallback", "url": "https://example.test", "snippet": "", "provider": "bing"}]

    connector.connectors = (FailingSearch(), WorkingSearch())
    assert connector.search("test")[0]["provider"] == "bing"


def test_deduplicate_by_url_and_content():
    one = Document.create(kind="web", title="One", url="https://example.com/a", content="alpha " * 100)
    same_url = Document.create(kind="web", title="Other", url="https://example.com/a#part", content="beta " * 100)
    same_content = Document.create(kind="web", title="Mirror", url="https://mirror.test/a", content="alpha " * 100)
    assert deduplicate([one, same_url, same_content]) == [one]


def test_private_corpus_ingestion(tmp_path: Path):
    (tmp_path / "note.md").write_text("A private research note with useful evidence.", encoding="utf-8")
    (tmp_path / "ignored.exe").write_bytes(b"no")
    docs = LocalCorpusConnector(tmp_path).collect()
    assert len(docs) == 1
    assert docs[0].kind == "private"
    assert "useful evidence" in docs[0].content


def test_x_collection_resumes_mid_page(tmp_path: Path):
    settings = Settings(x_bearer_token="token", x_user_id="me", x_max_accounts=2, x_posts_per_account=5)
    connector = XConnector(settings)
    interrupted = {"value": True}

    def fake_get(path, params):
        if path.endswith("/following"):
            return {"data": [{"id": "1", "username": "one"}, {"id": "2", "username": "two"}], "meta": {}}
        if path.endswith("/followers"):
            return {"data": [], "meta": {}}
        account_id = path.split("/")[-2]
        if account_id == "2" and interrupted["value"]:
            raise NeedsAttention("rate limit")
        return {"data": [{"id": f"p-{account_id}", "text": f"post from {account_id}", "created_at": "2026-01-01"}]}

    connector._get = fake_get
    checkpoint = tmp_path / "x-checkpoint.json"
    with pytest.raises(NeedsAttention):
        connector.collect(checkpoint)
    interrupted["value"] = False
    docs, state = connector.collect(checkpoint)
    assert state["complete"] is True
    assert {doc.metadata["account_id"] for doc in docs} == {"1", "2"}
    assert len(docs) == 2


@pytest.mark.parametrize("text", ["Please complete this CAPTCHA", "Verify you are human", "Log in to continue"])
def test_browser_attention_markers_stop_automation(text):
    assert requires_browser_attention(text)
