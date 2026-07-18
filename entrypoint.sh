#!/bin/bash
set -e

/bin/ollama serve &
ollama_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

ollama pull "${LOCAL_LLM_MODEL:-hf.co/bottlecapai/ThinkingCap-Qwen3.6-27B-GGUF:Q4_K_M}"
ollama pull "${LOCAL_LLM_EMBED_MODEL:-nomic-embed-text}"
ollama list

wait "$ollama_pid"
