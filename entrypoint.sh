#!/bin/bash
set -e

/bin/ollama serve &
ollama_pid=$!

until ollama list >/dev/null 2>&1; do
  sleep 1
done

ensure_model() {
  local model="$1"
  if ollama show "$model" >/dev/null 2>&1; then
    echo "Model already installed: $model"
  else
    ollama pull "$model"
  fi
}

ensure_model "${LOCAL_LLM_MODEL:-openai-20b-neoplus-uncensored:latest}"
ensure_model "${LOCAL_LLM_EMBED_MODEL:-nomic-embed-text}"
ollama list

wait "$ollama_pid"
