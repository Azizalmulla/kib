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
    max_tokens: int = 700
    reasoning_effort: str = "low"
    response_format: str = "tool"

    def _answer_tool(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "submit_answer",
                "description": "Submit the final grounded answer and citations.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "answer": {"type": "string"},
                        "citations": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "doc_id": {"type": "string"},
                                    "document_version": {"type": "string"},
                                    "page_number": {"type": ["integer", "null"]},
                                    "start_offset": {"type": ["integer", "null"]},
                                    "end_offset": {"type": ["integer", "null"]},
                                    "source_uri": {"type": "string"},
                                    "quote": {"type": "string"},
                                },
                                "required": [
                                    "doc_id",
                                    "document_version",
                                    "page_number",
                                    "start_offset",
                                    "end_offset",
                                    "source_uri",
                                    "quote",
                                ],
                            },
                        },
                    },
                    "required": ["answer", "citations"],
                },
            },
        }

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
            "max_tokens": self.max_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self.reasoning_effort:
            payload["reasoning_effort"] = self.reasoning_effort
        if self.response_format == "tool":
            payload["tools"] = [self._answer_tool()]
            payload["tool_choice"] = {
                "type": "function",
                "function": {"name": "submit_answer"},
            }
        elif self.response_format:
            payload["response_format"] = {"type": self.response_format}

        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            data = resp.json()

        message = data["choices"][0]["message"]
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            function = tool_calls[0].get("function") or {}
            arguments = function.get("arguments")
            if arguments:
                return arguments

        return message.get("content") or ""


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
            max_tokens=settings.llm_max_tokens,
            reasoning_effort=settings.llm_reasoning_effort,
            response_format=settings.llm_response_format,
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
