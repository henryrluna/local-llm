from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from typing import Any, Callable

import httpx

from .config import Settings


class ProviderError(RuntimeError):
    pass


class ModelProvider(ABC):
    name: str

    def set_progress_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        """Receive best-effort live generation statistics when supported."""
        return None

    @abstractmethod
    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        raise NotImplementedError

    def structured(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        raw = self.chat(messages, temperature=0.1).strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
            if raw.startswith("json"):
                raw = raw[4:].lstrip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Model did not return valid JSON: {exc}") from exc

    @abstractmethod
    def embeddings(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError


class OllamaProvider(ModelProvider):
    name = "ollama"

    def __init__(self, settings: Settings):
        self.base_url = settings.ollama_url.rstrip("/")
        self.model = settings.ollama_model
        self.embed_model = settings.ollama_embed_model
        self.timeout_seconds = settings.ollama_timeout_seconds
        self.num_ctx = settings.ollama_num_ctx
        self.chat_max_tokens = settings.ollama_chat_max_tokens
        self.structured_max_tokens = settings.ollama_structured_max_tokens
        self.keep_alive = settings.ollama_keep_alive
        self.progress_update_seconds = settings.progress_update_seconds
        self._progress_callback: Callable[[dict[str, Any]], None] | None = None
        self.use_raw_final_channel = self.model.lower().startswith("openai-20b-neoplus-uncensored")

    def set_progress_callback(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        self._progress_callback = callback

    def _emit_progress(self, values: dict[str, Any]) -> None:
        if self._progress_callback is not None:
            self._progress_callback(values)

    @staticmethod
    def _raw_final_prompt(messages: list[dict[str, str]]) -> str:
        parts = []
        for message in messages:
            role = message.get("role", "user")
            if role not in {"system", "user", "assistant"}:
                role = "user"
            parts.append(f"<|{role}|>{message.get('content', '')}<|end|>")
        parts.append("<|assistant|><|channel|>final<|message|>")
        return "\n".join(parts)

    def _chat_request(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: str | None = None,
    ) -> str:
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": self.num_ctx,
            "num_predict": max_tokens,
        }
        if self.use_raw_final_channel:
            options["stop"] = ["<|end|>", "<|user|>", "<|assistant|>", "<|system|>"]
            payload: dict[str, Any] = {
                "model": self.model,
                "prompt": self._raw_final_prompt(messages),
                "raw": True,
                "stream": self._progress_callback is not None,
                "keep_alive": self.keep_alive,
                "options": options,
            }
            endpoint = "/api/generate"
        else:
            payload = {
                "model": self.model,
                "messages": messages,
                "stream": self._progress_callback is not None,
                "think": False,
                "keep_alive": self.keep_alive,
                "options": options,
            }
            endpoint = "/api/chat"
        if response_format and not self.use_raw_final_channel:
            payload["format"] = response_format
        with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds, connect=10)) as client:
            if self._progress_callback is not None:
                started = time.monotonic()
                last_update = started
                generated_pieces = 0
                content: list[str] = []
                with client.stream("POST", f"{self.base_url}{endpoint}", json=payload) as response:
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not line:
                            continue
                        item = json.loads(line)
                        piece = item.get("response", "") if self.use_raw_final_channel else item.get("message", {}).get("content", "")
                        if piece:
                            content.append(piece)
                            generated_pieces += 1
                        now = time.monotonic()
                        if now - last_update >= self.progress_update_seconds and not item.get("done"):
                            elapsed = max(now - started, 0.001)
                            self._emit_progress({
                                "generated_tokens": generated_pieces,
                                "tokens_per_second": generated_pieces / elapsed,
                                "elapsed_seconds": round(elapsed),
                                "estimated": True,
                                "done": False,
                                "max_tokens": max_tokens,
                            })
                            last_update = now
                        if item.get("done"):
                            elapsed = max(now - started, 0.001)
                            eval_count = int(item.get("eval_count") or generated_pieces)
                            eval_duration = float(item.get("eval_duration") or 0) / 1_000_000_000
                            self._emit_progress({
                                "prompt_tokens": int(item.get("prompt_eval_count") or 0),
                                "generated_tokens": eval_count,
                                "tokens_per_second": eval_count / eval_duration if eval_duration else eval_count / elapsed,
                                "elapsed_seconds": round(elapsed),
                                "estimated": False,
                                "done": True,
                                "max_tokens": max_tokens,
                            })
                return "".join(content)
            response = client.post(f"{self.base_url}{endpoint}", json=payload)
            response.raise_for_status()
            data = response.json()
            return data["response"] if self.use_raw_final_channel else data["message"]["content"]

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        try:
            return self._chat_request(
                messages,
                temperature=temperature,
                max_tokens=self.chat_max_tokens,
            )
        except (httpx.HTTPError, KeyError) as exc:
            raise ProviderError(
                f"Ollama request failed at {self.base_url} after a {self.timeout_seconds:g}s limit. "
                f"Confirm Ollama is running and model '{self.model}' is installed: {exc}"
            ) from exc

    def structured(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            raw = self._chat_request(
                messages,
                temperature=0.1,
                max_tokens=self.structured_max_tokens,
                response_format="json",
            ).strip()
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
                if raw.startswith("json"):
                    raw = raw[4:].lstrip()
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Model did not return valid JSON: {exc}") from exc
        except (httpx.HTTPError, KeyError) as exc:
            raise ProviderError(
                f"Ollama structured request failed at {self.base_url} after a {self.timeout_seconds:g}s limit: {exc}"
            ) from exc

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        try:
            with httpx.Client(timeout=httpx.Timeout(300, connect=5)) as client:
                response = client.post(
                    f"{self.base_url}/api/embed", json={"model": self.embed_model, "input": texts}
                )
                response.raise_for_status()
                return response.json()["embeddings"]
        except (httpx.HTTPError, KeyError) as exc:
            raise ProviderError(f"Ollama embedding request failed: {exc}") from exc


class OpenAICompatibleProvider(ModelProvider):
    name = "cloud"

    def __init__(self, settings: Settings):
        if not settings.cloud_api_key or not settings.cloud_model:
            raise ProviderError("Cloud provider requires HARNESS_CLOUD_API_KEY and HARNESS_CLOUD_MODEL")
        self.base_url = settings.cloud_base_url.rstrip("/")
        self.model = settings.cloud_model
        self.embed_model = settings.cloud_embed_model
        self.api_key = settings.cloud_api_key

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        try:
            with httpx.Client(timeout=httpx.Timeout(600, connect=10)) as client:
                response = client.post(
                    f"{self.base_url}/chat/completions",
                    headers=self.headers,
                    json={"model": self.model, "messages": messages, "temperature": temperature},
                )
                response.raise_for_status()
                return response.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError) as exc:
            raise ProviderError(f"Cloud model request failed: {exc}") from exc

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        try:
            with httpx.Client(timeout=httpx.Timeout(300, connect=10)) as client:
                response = client.post(
                    f"{self.base_url}/embeddings",
                    headers=self.headers,
                    json={"model": self.embed_model, "input": texts},
                )
                response.raise_for_status()
                return [item["embedding"] for item in response.json()["data"]]
        except (httpx.HTTPError, KeyError) as exc:
            raise ProviderError(f"Cloud embedding request failed: {exc}") from exc


class HybridProvider(ModelProvider):
    name = "hybrid"

    def __init__(self, local: OllamaProvider, cloud: OpenAICompatibleProvider):
        self.local = local
        self.cloud = cloud

    def chat(self, messages: list[dict[str, str]], *, temperature: float = 0.2) -> str:
        return self.cloud.chat(messages, temperature=temperature)

    def embeddings(self, texts: list[str]) -> list[list[float]]:
        return self.local.embeddings(texts)


def provider_for(name: str, settings: Settings) -> ModelProvider:
    local = OllamaProvider(settings)
    if name == "ollama":
        return local
    cloud = OpenAICompatibleProvider(settings)
    if name == "cloud":
        return cloud
    if name == "hybrid":
        return HybridProvider(local, cloud)
    raise ProviderError(f"Unknown provider: {name}")
