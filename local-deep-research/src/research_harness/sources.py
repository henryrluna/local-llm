from __future__ import annotations

import hashlib
import html
import json
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup

from .config import Settings


class SourceError(RuntimeError):
    pass


class NeedsAttention(SourceError):
    pass


@dataclass
class Document:
    id: str
    kind: str
    title: str
    url: str
    content: str
    author: str = ""
    published_at: str = ""
    retrieved_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def create(cls, *, kind: str, title: str, url: str, content: str, **kwargs: Any) -> "Document":
        digest = hashlib.sha256((url + "\n" + content).encode("utf-8")).hexdigest()
        return cls(id=digest[:12], kind=kind, title=title.strip() or url, url=url, content=content.strip(), **kwargs)

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.content.encode("utf-8")).hexdigest()

    def public_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value.pop("content")
        return value


USER_AGENT = "LocalDeepResearchHarness/0.1 (personal research; contact: local-user)"


def requires_browser_attention(text: str) -> bool:
    lowered = text.lower()
    return any(marker in lowered for marker in ("captcha", "verify you are human", "login to continue", "log in to continue"))


def clean_html(raw: str) -> tuple[str, str]:
    soup = BeautifulSoup(raw, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "form", "noscript", "svg"]):
        tag.decompose()
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    main = soup.find("article") or soup.find("main") or soup.body or soup
    text = main.get_text("\n", strip=True)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text


class WebFetcher:
    def __init__(self, timeout: float = 25):
        self.timeout = timeout

    def fetch(self, url: str, kind: str = "web") -> Document:
        try:
            with httpx.Client(
                timeout=self.timeout,
                follow_redirects=True,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml,text/plain"},
            ) as client:
                response = client.get(url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "")
                if "html" in content_type:
                    title, text = clean_html(response.text)
                else:
                    title, text = urlparse(str(response.url)).path.rsplit("/", 1)[-1], response.text
                if requires_browser_attention(text):
                    raise NeedsAttention(f"Browser intervention required for {url}")
                if len(text) < 200:
                    raise SourceError(f"Retrieved content was too short to use: {url}")
                return Document.create(kind=kind, title=title, url=str(response.url), content=text)
        except NeedsAttention:
            raise
        except httpx.HTTPError as exc:
            raise SourceError(f"Unable to retrieve {url}: {exc}") from exc


class SearxngConnector:
    def __init__(self, settings: Settings):
        self.url = settings.searxng_url.rstrip("/")

    def search(self, query: str, limit: int = 6) -> list[dict[str, str]]:
        try:
            query_terms = set(re.findall(r"[a-z0-9]{3,}", query.lower())) - {
                "the", "and", "for", "with", "from", "what", "when", "where", "does", "have", "about",
            }
            minimum_matches = 1 if len(query_terms) <= 2 else 2

            def relevant(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
                return [
                    item for item in items
                    if len(query_terms & set(re.findall(
                        r"[a-z0-9]{3,}",
                        f"{item.get('title', '')} {item.get('content', '')}".lower(),
                    ))) >= minimum_matches
                ]

            with httpx.Client(timeout=30) as client:
                params = {"q": query, "format": "json", "language": "en"}
                response = client.get(f"{self.url}/search", params=params)
                response.raise_for_status()
                payload = response.json()
                results = relevant(payload.get("results", []))
                if not results:
                    # Public search engines routinely suspend a local SearXNG
                    # instance after CAPTCHAs or rate limits. Retry several engines
                    # that can also be invoked explicitly when defaults fail.
                    response = client.get(
                        f"{self.url}/search",
                        params={**params, "engines": "yandex,presearch,dogpile,privacywall"},
                    )
                    response.raise_for_status()
                    payload = response.json()
                    results = relevant(payload.get("results", []))
                if not results:
                    failures = ", ".join(
                        f"{item[0]}: {item[1]}"
                        for item in payload.get("unresponsive_engines", [])
                        if isinstance(item, list) and len(item) >= 2
                    )
                    detail = f" Failed engines: {failures}." if failures else ""
                    raise SourceError(f"SearXNG returned zero results for '{query}'.{detail}")
                results = results[:limit]
                return [
                    {"title": item.get("title", item["url"]), "url": item["url"], "snippet": item.get("content", "")}
                    for item in results if item.get("url")
                ]
        except SourceError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise SourceError(
                f"SearXNG search failed at {self.url}. Start the configured search service or provide seed URLs: {exc}"
            ) from exc


class SubstackConnector:
    def __init__(self, fetcher: WebFetcher):
        self.fetcher = fetcher

    def collect(self, feed_url: str, limit: int = 20) -> list[Document]:
        feed = feedparser.parse(feed_url)
        if getattr(feed, "bozo", False) and not feed.entries:
            raise SourceError(f"Could not parse Substack feed {feed_url}: {feed.bozo_exception}")
        documents: list[Document] = []
        for entry in feed.entries[:limit]:
            url = entry.get("link", "")
            raw = entry.get("content", [{}])[0].get("value") or entry.get("summary", "")
            _, content = clean_html(raw)
            if len(content) < 500 and url:
                try:
                    documents.append(self.fetcher.fetch(url, "substack"))
                    continue
                except SourceError:
                    pass
            if content:
                documents.append(
                    Document.create(
                        kind="substack",
                        title=html.unescape(entry.get("title", url)),
                        url=url or feed_url,
                        content=content,
                        author=entry.get("author", ""),
                        published_at=entry.get("published", ""),
                    )
                )
        return documents


class XConnector:
    base_url = "https://api.x.com/2"

    def __init__(self, settings: Settings):
        self.token = settings.x_bearer_token
        self.user_id = settings.x_user_id
        self.max_accounts = settings.x_max_accounts
        self.posts_per_account = settings.x_posts_per_account

    def enabled(self) -> bool:
        return bool(self.token and self.user_id)

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            with httpx.Client(timeout=45, headers={"Authorization": f"Bearer {self.token}"}) as client:
                response = client.get(f"{self.base_url}{path}", params=params)
                if response.status_code in (401, 403, 429):
                    raise NeedsAttention(f"X API requires attention ({response.status_code}): {response.text[:300]}")
                response.raise_for_status()
                return response.json()
        except NeedsAttention:
            raise
        except httpx.HTTPError as exc:
            raise SourceError(f"X API request failed: {exc}") from exc

    def collect(self, resume_path: Path | None = None) -> tuple[list[Document], dict[str, Any]]:
        if not self.enabled():
            return [], {"enabled": False}
        state: dict[str, Any] = {
            "enabled": True,
            "relationship_index": 0,
            "pagination_token": None,
            "relationship_counts": {"following": 0, "followers": 0},
            "processed_ids": {"following": [], "followers": []},
            "seen_account_ids": [],
            "documents": [],
        }
        if resume_path and resume_path.exists():
            try:
                state.update(json.loads(resume_path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass

        def persist() -> None:
            if resume_path:
                resume_path.parent.mkdir(parents=True, exist_ok=True)
                resume_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        relationships = ("following", "followers")
        seen: set[str] = set(state["seen_account_ids"])
        documents = [Document(**item) for item in state["documents"]]
        for relationship_index in range(int(state["relationship_index"]), len(relationships)):
            relationship = relationships[relationship_index]
            processed = set(state.get("processed_ids", {}).get(relationship, []))
            token = state.get("pagination_token") if relationship_index == int(state["relationship_index"]) else None
            while len(processed) < self.max_accounts:
                remaining = self.max_accounts - len(processed)
                params: dict[str, Any] = {"max_results": min(1000, max(1, remaining)), "user.fields": "name,username,description"}
                if token:
                    params["pagination_token"] = token
                account_page = self._get(f"/users/{self.user_id}/{relationship}", params)
                accounts = account_page.get("data", [])
                for account in accounts:
                    if len(processed) >= self.max_accounts:
                        break
                    if account["id"] in processed:
                        continue
                    if account["id"] in seen:
                        processed.add(account["id"])
                        state["processed_ids"][relationship] = sorted(processed)
                        state["relationship_counts"][relationship] = len(processed)
                        persist()
                        continue
                    try:
                        tweets_payload = self._get(
                            f"/users/{account['id']}/tweets",
                            {"max_results": max(5, min(100, self.posts_per_account)), "tweet.fields": "created_at,conversation_id,public_metrics"},
                        )
                    except NeedsAttention:
                        persist()
                        raise
                    except SourceError:
                        processed.add(account["id"])
                        seen.add(account["id"])
                        state["processed_ids"][relationship] = sorted(processed)
                        state["relationship_counts"][relationship] = len(processed)
                        state["seen_account_ids"] = sorted(seen)
                        persist()
                        continue
                    posts = tweets_payload.get("data", [])[: self.posts_per_account]
                    if posts:
                        content = "\n\n".join(f"{post.get('created_at','')}\n{post['text']}" for post in posts)
                        document = Document.create(
                            kind="x",
                            title=f"Recent posts from @{account.get('username', account['id'])}",
                            url=f"https://x.com/{account.get('username', '')}",
                            content=content,
                            author=account.get("name", ""),
                            metadata={"relationship": relationship, "account_id": account["id"]},
                        )
                        documents.append(document)
                        state["documents"].append(asdict(document))
                    processed.add(account["id"])
                    seen.add(account["id"])
                    state["processed_ids"][relationship] = sorted(processed)
                    state["relationship_counts"][relationship] = len(processed)
                    state["seen_account_ids"] = sorted(seen)
                    persist()
                    time.sleep(0.15)
                token = account_page.get("meta", {}).get("next_token")
                state["pagination_token"] = token
                state["relationship_index"] = relationship_index
                persist()
                if not token or not accounts:
                    break
                time.sleep(1)
            state["relationship_index"] = relationship_index + 1
            state["pagination_token"] = None
            persist()
        checkpoint = {
            "enabled": True,
            "complete": int(state["relationship_index"]) >= len(relationships),
            "relationship_index": state["relationship_index"],
            "pagination_token": state["pagination_token"],
            "relationships": state["relationship_counts"],
            "accounts_collected": len(seen),
            "documents_collected": len(documents),
        }
        return documents, checkpoint


class LocalCorpusConnector:
    allowed_suffixes = {".txt", ".md", ".html", ".htm", ".json"}

    def __init__(self, path: Path):
        self.path = path

    def collect(self, limit: int = 500) -> list[Document]:
        documents: list[Document] = []
        if not self.path.exists():
            return documents
        for path in sorted(self.path.rglob("*")):
            if len(documents) >= limit or not path.is_file() or path.suffix.lower() not in self.allowed_suffixes:
                continue
            try:
                raw = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                raw = path.read_text(encoding="utf-8", errors="replace")
            if path.suffix.lower() in {".html", ".htm"}:
                title, raw = clean_html(raw)
            else:
                title = path.stem
            if raw.strip():
                documents.append(Document.create(kind="private", title=title, url=path.resolve().as_uri(), content=raw))
        return documents


class BrowserFallbackConnector:
    """Optional, explicit browser fallback. It never launches or logs into a browser."""

    def __init__(self, cdp_url: str):
        self.cdp_url = cdp_url

    def collect(self, urls: Iterable[str]) -> list[Document]:
        if not self.cdp_url:
            return []
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise NeedsAttention("Browser fallback configured but Playwright extra is not installed") from exc
        documents: list[Document] = []
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(self.cdp_url)
            context = browser.contexts[0] if browser.contexts else browser.new_context()
            page = context.new_page()
            for url in urls:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                body = page.locator("body").inner_text(timeout=10000)
                if requires_browser_attention(body):
                    raise NeedsAttention(f"Browser fallback stopped for login/CAPTCHA at {url}")
                if len(body) >= 200:
                    documents.append(Document.create(kind="browser", title=page.title(), url=page.url, content=body))
            page.close()
        return documents


def deduplicate(documents: Iterable[Document]) -> list[Document]:
    unique: dict[str, Document] = {}
    seen_urls: set[str] = set()
    for document in documents:
        normalized_url = document.url.split("#", 1)[0].rstrip("/").lower()
        fingerprint = hashlib.sha256(re.sub(r"\s+", " ", document.content.lower()).encode()).hexdigest()
        if normalized_url in seen_urls or fingerprint in unique:
            continue
        seen_urls.add(normalized_url)
        unique[fingerprint] = document
    return list(unique.values())


def save_document(document: Document, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{document.id}.txt"
    header = json.dumps(document.public_dict(), ensure_ascii=False, indent=2)
    path.write_text(f"{header}\n\n--- CONTENT ---\n{document.content}", encoding="utf-8")
    return path
