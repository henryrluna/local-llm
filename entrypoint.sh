#!/bin/bash

# Start Ollama in the background
/bin/ollama serve &
pid=$!

until ollama list > /dev/null 2>&1; do
  sleep 1
done

# List your required models here
  ollama pull qwen3:8b
  ollama pull gemma3:12b
  ollama pull llama3.2:8b
  ollama pull llama3.2:1b

  ollama list

# Wait for Ollama process to finish
wait $pid
