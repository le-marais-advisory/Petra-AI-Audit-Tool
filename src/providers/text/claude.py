from __future__ import annotations

import json
import logging
from typing import Any

from anthropic import Anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.providers.analysis_result import RULE_RESULT_JSON_SCHEMA, compact_rule_payload
from src.providers.text.base import TextAnalysisProvider

logger = logging.getLogger("petra.providers.text.claude")


def _extract_text_content(content_blocks: list[Any]) -> str:
    parts = [
        block.text.strip()
        for block in content_blocks
        if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
    ]
    if parts:
        return "\n".join(parts)
    raise ValueError("Claude returned no structured text content.")


class ClaudeTextAnalysisProvider(TextAnalysisProvider):
    def __init__(
        self,
        api_key: str,
        model_id: str,
        temperature: float | None,
        max_tokens: int,
    ) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model_id
        self._temperature = temperature
        self._max_tokens = max_tokens

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((Exception,)),
        reraise=True,
    )
    def _call_claude_with_retry(self, messages: list[dict[str, Any]], system_prompt: str) -> dict[str, Any]:
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": messages,
            "output_config": {
                "format": {
                    "type": "json_schema",
                    "schema": RULE_RESULT_JSON_SCHEMA,
                }
            },
        }
        if system_prompt.strip():
            request_kwargs["system"] = system_prompt
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature

        response = self._client.messages.create(**request_kwargs)
        raw_text = _extract_text_content(response.content)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            stop_reason = getattr(response, "stop_reason", "unknown")
            logger.error(
                "Claude text response JSON parse failed (stop_reason=%s, len=%d, max_tokens=%d): %s — raw: %.500s",
                stop_reason,
                len(raw_text),
                self._max_tokens,
                exc,
                raw_text,
            )
            raise

    def evaluate_rule(self, document_content: str, rule: dict, system_prompt: str) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": (
                    "Evaluate the following text/content rule against the extracted PDF content.\n"
                    f"{compact_rule_payload(rule)}\n"
                    "EXTRACTED DOCUMENT CONTENT:\n"
                    f"{document_content}\n"
                ),
            }
        ]
        return self._call_claude_with_retry(messages, system_prompt)
