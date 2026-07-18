from __future__ import annotations

import json
import re
import threading
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database, utcnow
from .notifications import notify
from .providers import ModelProvider, ProviderError, provider_for
from .reporting import render_pdf, validate_citations
from .sources import (
    BrowserFallbackConnector,
    Document,
    LocalCorpusConnector,
    NeedsAttention,
    SearxngConnector,
    SourceError,
    SubstackConnector,
    WebFetcher,
    XConnector,
    deduplicate,
    save_document,
)


class Cancelled(RuntimeError):
    pass


SYSTEM = """You are a rigorous research analyst. Use only the supplied evidence. Never invent sources, URLs, quotes, dates, or facts. Cite evidence using exact IDs like [S1]. Clearly distinguish evidence, inference, uncertainty, and unresolved contradiction."""


class ResearchPipeline:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    def check_cancelled(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if not job or job["cancel_requested"]:
            raise Cancelled("Job cancelled by user")

    def _plan(self, provider: ModelProvider, question: str) -> dict[str, Any]:
        prompt = f"""Create a decision-complete research plan for this question:\n\n{question}\n\nReturn strict JSON with keys: queries (5-8 search query strings), sections (6-10 report section titles), scope (one paragraph), and risks (array). Do not use markdown fences."""
        try:
            plan = provider.structured([{"role": "system", "content": SYSTEM}, {"role": "user", "content": prompt}])
            if not isinstance(plan.get("queries"), list) or not isinstance(plan.get("sections"), list):
                raise ProviderError("Plan JSON omitted queries or sections")
            return plan
        except ProviderError:
            # A valid local model answer may be prose; preserve progress with a conservative fallback.
            return {
                "queries": [question, f"{question} evidence", f"{question} analysis", f"{question} criticism", f"{question} recent research"],
                "sections": ["Context and Definitions", "Current Evidence", "Major Perspectives", "Contradictions and Tradeoffs", "Implications", "Conclusions"],
                "scope": question,
                "risks": ["The planning model did not return structured JSON; fallback queries were used."],
            }

    def _collect(self, job: dict[str, Any], plan: dict[str, Any]) -> tuple[list[Document], dict[str, Any]]:
        options = job["options"]
        documents: list[Document] = []
        trace: dict[str, Any] = {"queries": [], "errors": [], "connectors": {}}
        fetcher = WebFetcher()
        seed_urls = [u.strip() for u in options.get("seed_urls", []) if u.strip()]

        for url in seed_urls:
            self.check_cancelled(job["id"])
            try:
                documents.append(fetcher.fetch(url))
            except SourceError as exc:
                trace["errors"].append(str(exc))

        use_web = options.get("use_web", True)
        if use_web:
            search = SearxngConnector(self.settings)
            for query in plan["queries"][:8]:
                self.check_cancelled(job["id"])
                try:
                    results = search.search(str(query), limit=5)
                    trace["queries"].append({"query": query, "results": len(results)})
                except SourceError as exc:
                    trace["errors"].append(str(exc))
                    continue
                for result in results:
                    if len(documents) >= self.settings.max_sources:
                        break
                    try:
                        documents.append(fetcher.fetch(result["url"]))
                    except SourceError as exc:
                        trace["errors"].append(str(exc))

        feed_urls = [f.strip() for f in options.get("substack_feeds", []) if f.strip()]
        feed_urls.extend(f.strip() for f in self.settings.substack_feeds.split(",") if f.strip())
        substack = SubstackConnector(fetcher)
        for feed_url in dict.fromkeys(feed_urls):
            self.check_cancelled(job["id"])
            try:
                items = substack.collect(feed_url, limit=20)
                documents.extend(items)
                trace["connectors"].setdefault("substack", {})[feed_url] = len(items)
            except SourceError as exc:
                trace["errors"].append(str(exc))

        private_docs = LocalCorpusConnector(self.settings.private_corpus_dir).collect()
        documents.extend(private_docs)
        trace["connectors"]["private_corpus"] = len(private_docs)

        if options.get("include_x", False):
            x_connector = XConnector(self.settings)
            try:
                x_docs, checkpoint = x_connector.collect(self.settings.captures_dir / job["id"] / "x-checkpoint.json")
                documents.extend(x_docs)
                trace["connectors"]["x"] = checkpoint
            except NeedsAttention:
                raise
            except SourceError as exc:
                trace["errors"].append(str(exc))

        browser_urls = options.get("browser_fallback_urls", [])
        if browser_urls and self.settings.browser_cdp_url:
            browser_docs = BrowserFallbackConnector(self.settings.browser_cdp_url).collect(browser_urls)
            documents.extend(browser_docs)
            trace["connectors"]["browser"] = len(browser_docs)

        documents = deduplicate(documents)
        if not documents:
            detail = "; ".join(trace["errors"][-3:]) or "No source connectors returned usable content"
            raise NeedsAttention(f"Research needs sources. Start SearXNG, add seed URLs, feeds, or private corpus files. {detail}")
        return documents, trace

    @staticmethod
    def _rank(question: str, documents: list[Document], limit: int) -> list[Document]:
        terms = set(re.findall(r"[a-z0-9]{3,}", question.lower()))
        def score(document: Document) -> tuple[int, int]:
            words = Counter(re.findall(r"[a-z0-9]{3,}", (document.title + " " + document.content[:12000]).lower()))
            return sum(min(words[t], 8) for t in terms), len(document.content)
        return sorted(documents, key=score, reverse=True)[:limit]

    @staticmethod
    def _evidence_context(documents: list[Document], max_chars: int = 60_000) -> tuple[str, dict[str, Document]]:
        mapping: dict[str, Document] = {}
        blocks: list[str] = []
        chars_per_source = max(1200, max_chars // max(1, len(documents)))
        for index, document in enumerate(documents, 1):
            source_id = f"S{index}"
            mapping[source_id] = document
            blocks.append(f"[{source_id}] {document.title}\nURL: {document.url}\nRetrieved: {document.retrieved_at}\n{document.content[:chars_per_source]}")
        return "\n\n---\n\n".join(blocks), mapping

    def _draft_report(self, provider: ModelProvider, question: str, plan: dict[str, Any], evidence: str, source_map: dict[str, Document], trace: dict[str, Any]) -> str:
        executive = provider.chat([
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": f"Question: {question}\n\nEvidence:\n{evidence}\n\nWrite a 700-1000 word executive summary. Every factual paragraph must cite one or more supplied IDs. Include the answer, strongest evidence, key uncertainty, and practical implications."},
        ])
        sections: list[str] = []
        for section in plan["sections"][:8]:
            section_text = provider.chat([
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Research question: {question}\nSection: {section}\n\nEvidence:\n{evidence}\n\nWrite 700-1000 analytical words for this section. Use exact [S#] citations in every factual paragraph. Compare sources, expose contradictions, and avoid unsupported claims. Do not repeat an executive summary."},
            ])
            sections.append(f"## {section}\n\n{section_text.strip()}")

        bibliography = "\n".join(
            f"[{sid}] {doc.title}. {doc.url} (retrieved {doc.retrieved_at[:10]})."
            for sid, doc in source_map.items()
        )
        appendix_parts = []
        for sid, doc in source_map.items():
            excerpt = doc.content[:7000].strip()
            appendix_parts.append(f"### [{sid}] {doc.title}\n\nKind: {doc.kind}. Retrieved: {doc.retrieved_at}.\n\n{excerpt}")
        errors = "\n".join(f"- {error}" for error in trace.get("errors", [])[:20]) or "- No material collection errors were recorded."
        return f"""# Executive Summary

{executive.strip()}

# Research Question and Scope

**Question:** {question}

{plan.get('scope', question)}

# Methodology and Source Selection

The harness decomposed the question into targeted searches, captured source text locally, removed duplicate documents, ranked the surviving evidence against the research question, and supplied only that captured evidence to the synthesis model. Citation identifiers refer to the bibliography and evidence appendix. Retrieval failures are recorded rather than treated as successfully read sources.

{chr(10).join(sections)}

# Contradictions, Uncertainty, and Limitations

The report is bounded by the retrieved corpus, access permissions, API limits, publication availability, and model context. Inferences should be distinguished from direct source claims. Sources can be mutually inconsistent or outdated; those tensions are part of the result rather than silently resolved.

Collection notes:

{errors}

# Bibliography

{bibliography}

# Appendix: Research Trace and Selected Evidence

Queries executed:

{chr(10).join('- ' + str(item.get('query')) for item in trace.get('queries', []))}

{chr(10).join(appendix_parts)}
"""

    def run(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        job_dir = self.settings.captures_dir / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        try:
            provider = provider_for(job["provider"], self.settings)
            self.check_cancelled(job_id)
            self.db.transition(job_id, "planning", 8, "Decomposing the research question")
            plan = self._plan(provider, job["question"])
            self.db.update_job(job_id, checkpoint={"plan": plan})

            self.check_cancelled(job_id)
            self.db.transition(job_id, "collecting", 20, "Collecting web and private sources")
            documents, trace = self._collect(job, plan)
            ranked = self._rank(job["question"], documents, self.settings.max_sources)
            for document in ranked:
                path = save_document(document, job_dir)
                record = document.public_dict()
                record.update(content_path=str(path), content_hash=document.content_hash)
                self.db.insert_source(job_id, record)
            self.db.update_job(job_id, checkpoint={"plan": plan, "trace": trace, "source_count": len(ranked)})

            self.check_cancelled(job_id)
            self.db.transition(job_id, "extracting", 42, f"Ranking evidence from {len(ranked)} captured sources")
            evidence, source_map = self._evidence_context(ranked, self.settings.max_evidence_chars)

            self.check_cancelled(job_id)
            self.db.transition(job_id, "synthesizing", 55, "Writing and critiquing the cited report")
            markdown = self._draft_report(provider, job["question"], plan, evidence, source_map, trace)
            citation_errors = validate_citations(markdown, set(source_map))
            if citation_errors:
                self.db.add_event(job_id, "warning", "; ".join(citation_errors))
            critique = provider.chat([
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Independently audit this report for unsupported claims, missing counterevidence, unresolved contradictions, and invalid citations. Return a concise audit with exact [S#] references. Valid citations: {', '.join(source_map)}. Automated precheck: {citation_errors or 'passed'}.\n\n{markdown[:30000]}"},
            ])
            markdown += f"\n\n# Independent Evidence and Citation Audit\n\n{critique}\n"

            canonical = {
                "job_id": job_id,
                "question": job["question"],
                "provider": job["provider"],
                "plan": plan,
                "trace": trace,
                "sources": {sid: doc.public_dict() for sid, doc in source_map.items()},
                "citation_validation": validate_citations(markdown, set(source_map)),
                "generated_at": utcnow(),
            }
            canonical_path = job_dir / "report.json"
            markdown_path = job_dir / "report.md"
            canonical_path.write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding="utf-8")
            markdown_path.write_text(markdown, encoding="utf-8")

            self.check_cancelled(job_id)
            self.db.transition(job_id, "rendering", 88, "Rendering and validating the PDF")
            report_path = self.settings.reports_dir / f"{job_id}.pdf"
            pages = render_pdf(markdown, report_path, job["question"])
            errors = validate_citations(markdown, set(source_map))
            if errors or pages < self.settings.min_report_pages:
                reason = "; ".join(errors + ([f"PDF has {pages} pages; minimum is {self.settings.min_report_pages}"] if pages < self.settings.min_report_pages else []))
                self.db.update_job(
                    job_id, status="needs_attention", progress=95, status_message="Report quality gate needs attention",
                    error=reason, canonical_json_path=str(canonical_path), markdown_path=str(markdown_path), report_path=str(report_path), completed_at=utcnow(),
                )
                self.db.add_event(job_id, "warning", reason)
                return

            self.db.update_job(
                job_id, status="completed", progress=100, status_message=f"Completed: {pages}-page cited report",
                error=None, canonical_json_path=str(canonical_path), markdown_path=str(markdown_path), report_path=str(report_path), completed_at=utcnow(),
            )
            self.db.add_event(job_id, "info", f"Report completed and validated ({pages} pages)")
            for error in notify(self.settings, job_id, job["question"]):
                self.db.add_event(job_id, "warning", error)
        except Cancelled as exc:
            self.db.update_job(job_id, status="failed", status_message="Cancelled", error=str(exc), completed_at=utcnow())
            self.db.add_event(job_id, "warning", str(exc))
        except NeedsAttention as exc:
            self.db.update_job(job_id, status="needs_attention", status_message="Human attention required", error=str(exc), completed_at=utcnow())
            self.db.add_event(job_id, "warning", str(exc))
        except (ProviderError, SourceError) as exc:
            self.db.update_job(job_id, status="needs_attention", status_message="Configuration or source attention required", error=str(exc), completed_at=utcnow())
            self.db.add_event(job_id, "error", str(exc))
        except Exception as exc:
            self.db.update_job(job_id, status="failed", status_message="Unexpected failure", error=f"{type(exc).__name__}: {exc}", completed_at=utcnow())
            self.db.add_event(job_id, "error", f"Unexpected failure: {type(exc).__name__}: {exc}")


class Worker:
    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db
        self.pipeline = ResearchPipeline(settings, db)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._loop, name="research-worker", daemon=True)

    def start(self) -> None:
        if not self.thread.is_alive():
            self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=10)

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            job = self.db.claim_next_job()
            if job:
                self.pipeline.run(job)
            else:
                self.stop_event.wait(self.settings.worker_poll_seconds)
