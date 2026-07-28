from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from anthropic import Anthropic

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
        # Explicit timeout and retry budget. The SDK defaults are a 600s timeout with
        # 2 retries, and timeouts are themselves retried, so an unresponsive call could
        # occupy a worker for ~30 minutes. 300s is sized off the slowest legitimate call
        # measured on the fixtures — 147.7s for SOI-PERCENTAGE-TIE, which emitted 15.2k
        # output tokens — leaving roughly 2x headroom. The SDK's own 429/5xx retries
        # (which honour retry-after) are kept as the rate-limit defence.
        self._client = Anthropic(api_key=api_key, timeout=httpx.Timeout(300.0, connect=5.0), max_retries=2)
        self._model = model_id
        self._temperature = temperature
        self._max_tokens = max_tokens

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
        stop_reason = getattr(response, "stop_reason", "unknown")
        # Output tokens cover reasoning as well as the response and are what max_tokens
        # caps, so this is the number to look at when sizing CLAUDE_TEXT_MAX_TOKENS or
        # judging how much rate-limit headroom a run is using.
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "Claude text usage: in=%s out=%s budget=%d stop=%s",
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
                self._max_tokens,
                stop_reason,
            )
        raw_text = _extract_text_content(response.content)
        try:
            return json.loads(raw_text)
        except json.JSONDecodeError as exc:
            logger.error(
                "Claude text response JSON parse failed (stop_reason=%s, len=%d, max_tokens=%d): %s — raw: %.500s",
                stop_reason,
                len(raw_text),
                self._max_tokens,
                exc,
                raw_text,
            )
            if stop_reason == "max_tokens":
                # Deterministic, so retrying is pointless — the budget is the problem.
                # Sonnet 5 thinks by default and max_tokens covers thinking plus the
                # response, so a reasoning-heavy rule can exhaust it before finishing
                # the JSON. Raise CLAUDE_TEXT_MAX_TOKENS.
                raise ValueError(
                    f"Response was truncated at max_tokens={self._max_tokens} before the JSON was "
                    "complete. Raise CLAUDE_TEXT_MAX_TOKENS — on models that think by default the "
                    "budget covers reasoning as well as the response."
                ) from exc
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
