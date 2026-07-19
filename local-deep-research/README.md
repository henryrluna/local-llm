# Custom Local Deep Research service

This directory contains the application source built by the root `docker-compose.yaml`. Normal startup happens from the parent `local-llm` directory:

```powershell
docker compose up -d --build
```

The service provides a mobile-friendly queue at port 5000, durable SQLite jobs, live progress, stop/retry/resume controls, multi-stage web and private-corpus research, Ollama or OpenAI-compatible providers, citation validation, and 10+ page PDF reports. Reports use a one-page executive summary, scope and executed queries, at least 3,000 words of core analysis, numeric citations, bibliography, collection notes, and a final independent evidence audit. A cross-section editorial pass removes repeated facts before rendering; raw evidence excerpts remain in local captures rather than bloating the PDF.

## Runtime boundaries

- Ollama is supplied by the root Compose service at `http://ollama:11434`.
- SearXNG is supplied by the root Compose service at `http://searxng:8080`.
- Runtime data is mounted from `../local-deep-research-data` to `/app/data`.
- Private sources are mounted read-only from `../private-corpus`.
- Optional credentials are loaded from the ignored `../research-harness.env`.

The app never marks a source as read unless content was retrieved and stored. Ambiguous collection, citation, or rendering results transition to `needs_attention` rather than `completed`.

## Development tests

Docker is the supported runtime. For local development with Python 3.11+:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q --basetemp .\work\pytest
```

The canonical report stays in structured JSON and Markdown; the UI exposes the validated PDF artifact.
