# local-llm

One Docker Compose stack for local model inference, chat, web search, speech, and asynchronous deep research.

## Start everything

From `C:\Users\Henry\Documents\local-llm`:

```powershell
docker compose up -d --build
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-stack.ps1
```

Docker Desktop must be running. The first startup can take a while because Ollama downloads the configured chat and embedding models. Later starts reuse `./Ollama`.

To stop the stack without deleting data:

```powershell
docker compose down
```

## Services

| Service | Address | Purpose |
| --- | --- | --- |
| Open WebUI | http://localhost:8080 | Chat UI using Ollama |
| Local Deep Research | http://localhost:5000 | Durable research jobs and PDF reports |
| SearXNG | http://localhost:8090 | Local web search |
| Ollama API | http://localhost:11434 | Local generation and embeddings |
| openedai-speech | http://localhost:8000 | OpenAI-compatible text to speech |
| Valkey | Internal only | SearXNG cache |

The Local Deep Research app is the custom asynchronous implementation in `./local-deep-research`; it replaces the former `localdeepresearch/local-deep-research` image. Inside Docker it talks directly to `http://ollama:11434` and `http://searxng:8080`.

## Configuration

The default writing model is the locally installed `openai-20b-neoplus-uncensored:latest` model. Its approximately 11 GB package leaves more of a 16 GB RTX 5060 Ti available for the 32,768-token context and KV cache than the previous 27B model, reducing CPU spill. `nomic-embed-text` remains the lightweight embedding model. To change either model, copy `.env.example` to `.env` or edit your existing local `.env`:

```dotenv
LOCAL_LLM_MODEL=openai-20b-neoplus-uncensored:latest
LOCAL_LLM_EMBED_MODEL=nomic-embed-text
HARNESS_PUBLIC_BASE_URL=http://localhost:5000
```

For cloud models, Substack feeds, X, browser fallback, or notifications, copy `research-harness.env.example` to the ignored `research-harness.env` and fill only the settings you need. Private credentials and captures are not committed.

Put private `.txt`, `.md`, `.html`, or `.json` source files in `./private-corpus`. Durable jobs, captures, canonical reports, and PDFs are stored in `./local-deep-research-data`. Data created by the previous Local Deep Research image remains there for recovery but is not used by the custom harness.

For phone access on the same Wi-Fi, open `http://<desktop-LAN-IP>:5000`. If Windows Firewall prompts, allow Docker only on private networks. Set `HARNESS_PUBLIC_BASE_URL` to that LAN address if notification links should open on the phone.

## Useful commands

```powershell
# See status
docker compose ps

# Follow the research app
docker compose logs -f local-deep-research

# Rebuild only the research app after code changes
docker compose up -d --build local-deep-research

# Run the full verification script
powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\verify-stack.ps1
```

Application internals and development tests are documented in `./local-deep-research/README.md`.
