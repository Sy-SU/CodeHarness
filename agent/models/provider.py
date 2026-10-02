"""OpenAI-compatible adapters with provider-independent, safe results."""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Mapping, Optional, Protocol, Tuple

import httpx

from .types import (
    ChatMessage,
    LLMError,
    LLMErrorKind,
    LLMResponse,
    LLMToolCall,
    ModelProfile,
    TokenUsage,
)
from .transport import TransportObservation


class ModelProviderError(RuntimeError):
    """Compatibility exception for unexpected adapter programming failures."""


class ModelProvider(Protocol):
    name: str

    def complete(
        self,
        messages: List[ChatMessage],
        *,
        model: str,
        profile: ModelProfile,
        parameters: Mapping[str, Any],
    ) -> LLMResponse:
        ...


class OpenAICompatibleProvider:
    """Synchronous chat-completions adapter. It never retries automatically."""

    _RESERVED_PARAMETERS = {"model", "messages", "stream"}

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        *,
        timeout_seconds: float,
        client: Optional[httpx.Client] = None,
    ):
        if not name.strip() or not base_url.strip() or not api_key.strip():
            raise ValueError("Provider name, base URL, and API key are required")
        if timeout_seconds <= 0:
            raise ValueError("Provider timeout must be positive")
        self.name = name
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self.timeout_seconds = float(timeout_seconds)
        self.client = client or httpx.Client(timeout=self.timeout_seconds)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(name={self.name!r}, "
            f"base_url={self.base_url!r}, timeout_seconds={self.timeout_seconds!r})"
        )

    @property
    def transport_metadata(self):
        return {"schema_version": "provider_transport_v1", "timeouts": self.client.timeout.as_dict(),
            "configured_timeout_seconds": self.timeout_seconds, "overall_request_deadline_seconds": None,
            "model_response_streaming": False, "automatic_retries": 0}

    @staticmethod
    def _latency_ms(started: float) -> int:
        return max(0, round((time.monotonic() - started) * 1000))

    @staticmethod
    def _request_id(response: httpx.Response, body: Optional[Mapping[str, Any]] = None) -> Optional[str]:
        value = response.headers.get("x-request-id") or response.headers.get("request-id")
        if value:
            return value
        body_id = body.get("id") if body is not None else None
        return body_id if isinstance(body_id, str) and body_id else None

    def _failure(
        self,
        *,
        model: str,
        profile: ModelProfile,
        kind: LLMErrorKind,
        message: str,
        started: float,
        status_code: Optional[int] = None,
        retryable: bool = False,
        request_id: Optional[str] = None,
        transport_diagnostics=None,
        actual_response_model=None,
        usage_metadata=None,
    ) -> LLMResponse:
        return LLMResponse.failure(
            provider=self.name,
            model=model,
            profile=profile,
            error=LLMError(
                kind=kind,
                message=message,
                status_code=status_code,
                retryable=retryable,
            ),
            request_id=request_id,
            latency_ms=self._latency_ms(started),
            transport_diagnostics=transport_diagnostics,
            actual_response_model=actual_response_model,
            usage_metadata=usage_metadata,
        )

    @staticmethod
    def _usage(body: Mapping[str, Any]) -> Optional[TokenUsage]:
        if "usage" not in body or body["usage"] is None:
            return None
        value = body["usage"]
        if not isinstance(value, Mapping):
            raise ValueError("usage must be an object")
        if "prompt_tokens" not in value or "completion_tokens" not in value:
            raise ValueError("usage is missing prompt_tokens or completion_tokens")
        return TokenUsage(
            input_tokens=value["prompt_tokens"],
            output_tokens=value["completion_tokens"],
        )

    @staticmethod
    def _tool_calls(message: Mapping[str, Any]) -> Tuple[LLMToolCall, ...]:
        raw_calls = message.get("tool_calls")
        if raw_calls is None:
            return ()
        if not isinstance(raw_calls, list):
            raise ValueError("tool_calls must be a list")
        calls = []
        for item in raw_calls:
            if not isinstance(item, Mapping):
                raise ValueError("tool call must be an object")
            if item.get("type", "function") != "function":
                raise ValueError("only function tool calls are supported")
            function = item.get("function")
            if not isinstance(function, Mapping):
                raise ValueError("tool call function must be an object")
            call_id = item.get("id")
            name = function.get("name")
            arguments = function.get("arguments")
            if not isinstance(call_id, str) or not call_id:
                raise ValueError("tool call id must be a non-empty string")
            if not isinstance(name, str) or not name:
                raise ValueError("tool call name must be a non-empty string")
            if isinstance(arguments, str):
                arguments = json.loads(arguments)
            if not isinstance(arguments, dict):
                raise ValueError("tool call arguments must be a JSON object")
            calls.append(LLMToolCall(call_id=call_id, name=name, arguments=arguments))
        return tuple(calls)

    def complete(
        self,
        messages: List[ChatMessage],
        *,
        model: str,
        profile: ModelProfile,
        parameters: Mapping[str, Any],
    ) -> LLMResponse:
        if not model.strip():
            raise ValueError("Model ID is required")
        reserved = self._RESERVED_PARAMETERS.intersection(parameters)
        if reserved:
            raise ValueError(f"Reserved model parameters cannot be overridden: {sorted(reserved)}")
        payload: Dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            **dict(parameters),
        }
        started = time.monotonic()
        request = self.client.build_request("POST", f"{self.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self._api_key}"}, json=payload)
        observation = TransportObservation(timeout=request.extensions.get("timeout", {}),
            request_bytes=len(request.content), started=started)
        request.extensions["trace"] = observation.trace
        try:
            response = self.client.send(request, stream=True)
            wire_response = response
            try:
                observation.headers(response.status_code)
                if response.is_stream_consumed:
                    observation.body(len(response.content))
                else:
                    chunks = []
                    # Preserve encoding headers and decode exactly once when
                    # materializing the response; count raw transport bytes.
                    for chunk in response.iter_raw():
                        observation.body(len(chunk))
                        chunks.append(chunk)
                    response = httpx.Response(response.status_code, headers=response.headers,
                        content=b"".join(chunks), request=request, extensions=response.extensions)
                observation.data["body_complete"] = True
            finally:
                wire_response.close()
        except httpx.HTTPError as exc:
            return self._failure(
                model=model,
                profile=profile,
                kind=LLMErrorKind.TRANSPORT,
                message=f"{self.name} model transport failed",
                started=started,
                retryable=True,
                transport_diagnostics=observation.finish(category=observation.error_category(exc), error=exc),
            )
        except KeyboardInterrupt as exc:
            self.last_transport_diagnostics = observation.finish(category="local_cancellation", error=exc)
            raise

        if not 200 <= response.status_code < 300:
            status = response.status_code
            return self._failure(
                model=model,
                profile=profile,
                kind=LLMErrorKind.HTTP,
                message=f"{self.name} model request returned HTTP {status}",
                started=started,
                status_code=status,
                retryable=status in {408, 409, 425, 429} or status >= 500,
                request_id=self._request_id(response),
                transport_diagnostics=observation.finish(category="rate_limit" if status == 429 else
                    "provider_5xx" if status >= 500 else "provider_http_error"),
            )

        try:
            body = response.json()
            if not isinstance(body, Mapping):
                raise ValueError("response JSON must be an object")
            choices = body.get("choices")
            if not isinstance(choices, list) or not choices:
                raise ValueError("response choices must be a non-empty list")
            choice = choices[0]
            if not isinstance(choice, Mapping):
                raise ValueError("response choice must be an object")
            message = choice.get("message")
            if not isinstance(message, Mapping):
                raise ValueError("response message must be an object")
            content = message.get("content")
            if content is None:
                content = ""
            if not isinstance(content, str):
                raise ValueError("response content must be a string or null")
            tool_calls = self._tool_calls(message)
            usage = self._usage(body)
            finish_reason = choice.get("finish_reason")
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ValueError("finish_reason must be a string or null")
            return LLMResponse(
                content=content,
                usage=usage,
                provider=self.name,
                model=model,
                profile=profile,
                request_id=self._request_id(response, body),
                finish_reason=finish_reason,
                tool_calls=tool_calls,
                latency_ms=self._latency_ms(started),
                actual_response_model=body.get("model") if isinstance(body.get("model"), str) else None,
                usage_metadata=_numeric_usage(body.get("usage")),
                transport_diagnostics=observation.finish(),
            )
        except (json.JSONDecodeError, TypeError, ValueError, KeyError, IndexError):
            return self._failure(
                model=model,
                profile=profile,
                kind=LLMErrorKind.PROTOCOL,
                message=f"{self.name} model response did not match the configured protocol",
                started=started,
                request_id=self._request_id(response),
                transport_diagnostics=observation.finish(category="protocol_failure"),
            )


def _numeric_usage(value):
    """Keep token counters only; supplier prose cannot enter diagnostics."""
    if not isinstance(value, dict):
        return {}
    return {key: _numeric_usage(item) if isinstance(item, dict) else item
        for key, item in value.items() if isinstance(item, (dict, int, float)) and not isinstance(item, bool)}
