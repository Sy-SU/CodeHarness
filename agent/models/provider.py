"""Provider adapters with a normalized internal response."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol

import httpx

from .types import ChatMessage, LLMResponse, ModelProfile, TokenUsage


class ModelProviderError(RuntimeError):
    pass


class ModelProvider(Protocol):
    name: str

    def complete(
        self, messages: List[ChatMessage], *, model: str, profile: ModelProfile
    ) -> LLMResponse:
        ...


class OpenAICompatibleProvider:
    """Minimal OpenAI-compatible chat-completions adapter, including Bailian."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 120,
        client: Optional[httpx.Client] = None,
    ):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.client = client or httpx.Client(timeout=timeout_seconds)

    def complete(
        self, messages: List[ChatMessage], *, model: str, profile: ModelProfile
    ) -> LLMResponse:
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "temperature": 0.2,
        }
        try:
            response = self.client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise ModelProviderError(f"{self.name} model request failed: {exc}") from exc
        usage = body.get("usage") or {}
        return LLMResponse(
            content=str(content),
            usage=TokenUsage(
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
            ),
            provider=self.name,
            model=model,
            profile=profile,
            request_id=response.headers.get("x-request-id") or body.get("id"),
        )
