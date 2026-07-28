from __future__ import annotations

from typing import Any

import httpx
from openai import OpenAI

from src.providers.analysis_result import AnalysisRuleResult, compact_rule_payload
from src.providers.text.base import TextAnalysisProvider


class OpenAITextAnalysisProvider(TextAnalysisProvider):
    def __init__(
        self,
        api_key: str,
        model_id: str,
        temperature: float | None,
        max_completion_tokens: int | None,
    ) -> None:
        # Explicit timeout and retry budget. The SDK defaults are a 600s timeout with
        # 2 retries, and timeouts are themselves retried, so an unresponsive call could
        # occupy a worker for ~30 minutes. 300s matches the Claude text provider: a
        # reasoning-heavy broad-scope rule was measured at 147.7s there, and text calls
        # are the long ones. The SDK's own 429/5xx retries are kept as the rate-limit
        # defence.
        self._client = OpenAI(api_key=api_key, timeout=httpx.Timeout(300.0, connect=5.0), max_retries=2)
        self._model = model_id
        self._temperature = temperature
        self._max_tokens = max_completion_tokens

    def _call_openai_with_retry(self, messages: list[dict[str, Any]]) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "response_format": AnalysisRuleResult,
        }
        if self._max_tokens is not None:
            request_kwargs["max_completion_tokens"] = self._max_tokens
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature

        response = self._client.beta.chat.completions.parse(
            **request_kwargs,
        )
        message = response.choices[0].message
        if message.parsed is not None:
            return message.parsed.model_dump()
        if message.refusal:
            raise ValueError(f"Model refused to answer: {message.refusal}")
        content = message.content or ""
        raise ValueError(f"Model returned no structured content. Raw content: {content!r}")

    def evaluate_rule(self, document_content: str, rule: dict, system_prompt: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append(
            {
                "role": "user",
                "content": (
                    "Evaluate the following text/content rule against the extracted PDF content.\n"
                    f"{compact_rule_payload(rule)}\n"
                    "EXTRACTED DOCUMENT CONTENT:\n"
                    f"{document_content}\n"
                ),
            }
        )
        return self._call_openai_with_retry(messages)
