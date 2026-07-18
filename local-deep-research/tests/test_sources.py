from pathlib import Path

import pytest

from research_harness.config import Settings
from research_harness.sources import Document, LocalCorpusConnector, NeedsAttention, XConnector, deduplicate, requires_browser_attention


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
