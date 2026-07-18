#!/bin/bash
set -e

/bin/ollama serve &
ollama_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

ollama pull "${LOCAL_LLM_MODEL:-qwen3:8b}"
ollama pull "${LOCAL_LLM_EMBED_MODEL:-nomic-embed-text}"
ollama list

wait "$ollama_pid"
