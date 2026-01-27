# local-llm

Goal: run local AI services with Docker for a self-hosted LLM stack.

## Current capabilities
- LLM runtime via Ollama (GPU-enabled)
- Web UI via Open WebUI (connects to Ollama)
- Local web search via SearXNG
- Text-to-speech via openedai-speech (OpenAI-compatible)
- Caching/kv store via Valkey (redis-compatible)

## Services and ports
- Ollama: 11434
- Open WebUI: 8080
- SearXNG: 8090
- openedai-speech: 8000

## Quick start
1) Ensure Docker Desktop is running
2) `docker compose up -d`
3) Open the UI at http://localhost:8080

