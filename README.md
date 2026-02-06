# local-llm

Goal: run local AI services with Docker for a self-hosted LLM stack.

## Current capabilities
- LLM runtime via Ollama (GPU-enabled)
- Web UI via Open WebUI (connects to Ollama)
- Deep research app via Local Deep Research
- Local web search via SearXNG
- Text-to-speech via openedai-speech (OpenAI-compatible)
- Caching/kv store via Valkey (redis-compatible)

## Services and ports
- Ollama: 11434
- Open WebUI: 8080
- Local Deep Research: 5000
- SearXNG: 8090
- openedai-speech: 8000

## Quick start
1) Ensure Docker Desktop is running
2) `docker compose up -d`
3) Verify everything: `.\scripts\verify-stack.ps1`
4) Open the UIs:
   - Open WebUI: http://localhost:8080
   - Local Deep Research: http://localhost:5000

## Local Deep Research defaults
- LLM provider: `ollama`
- Model: `qwen3:8b`
- Ollama URL inside Docker: `http://ollama:11434`
- SearXNG URL inside Docker: `http://searxng:8080`
- Persistent data: `./local-deep-research-data`
