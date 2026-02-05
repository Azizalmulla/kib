from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx

from .core.config import settings


class LLMProvider(Protocol):
    def generate(self, system_prompt: str, user_prompt: str) -> str:
        ...


@dataclass
class OpenAICompatibleProvider:
    base_url: str
    model: str
    api_key: str = ""
    timeout_seconds: int = 30

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        url = self.base_url.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        url = f"{url}/chat/completions"

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        return data["choices"][0]["message"]["content"]


@dataclass
class OllamaProvider:
    base_url: str
    model: str
    timeout_seconds: int = 30

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        url = f"{self.base_url.rstrip('/')}/api/chat"
        payload = {
            "model": self.model,
            "stream": False,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()

        return data["message"]["content"]


@dataclass
class MockProvider:
    response_text: str

    def generate(self, system_prompt: str, user_prompt: str) -> str:
        return self.response_text


def get_provider() -> LLMProvider:
    provider = settings.llm_provider.lower()
    if provider == "openai_compatible":
        return OpenAICompatibleProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            api_key=settings.llm_api_key,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "ollama":
        return OllamaProvider(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "mock":
        return MockProvider("{}")

    raise ValueError(f"Unsupported LLM provider: {settings.llm_provider}")
