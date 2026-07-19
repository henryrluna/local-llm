from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import httpx

from .config import Settings


class ProviderError(RuntimeError):
    pass


class ModelProvider(ABC):
    name: str

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

    def _chat_request(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        max_tokens: int,
        response_format: str | None = None,
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": self.keep_alive,
            "options": {
                "temperature": temperature,
                "num_ctx": self.num_ctx,
                "num_predict": max_tokens,
            },
        }
        if response_format:
            payload["format"] = response_format
        with httpx.Client(timeout=httpx.Timeout(self.timeout_seconds, connect=10)) as client:
            response = client.post(f"{self.base_url}/api/chat", json=payload)
            response.raise_for_status()
            return response.json()["message"]["content"]

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
