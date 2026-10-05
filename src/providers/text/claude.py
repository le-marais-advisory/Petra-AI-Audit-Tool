from __future__ import annotations

import json
import logging
from typing import Any

import httpx
from anthropic import Anthropic

from src.core.llm_usage import record_anthropic_usage
from src.providers.analysis_result import RULE_RESULT_JSON_SCHEMA, text_rule_prompt
from src.providers.errors import TruncatedResponseError, claude_response_text, served_by_fallback
from src.providers.text.base import TextAnalysisProvider

logger = logging.getLogger("petra.providers.text.claude")

_CACHE = {"type": "ephemeral"}  # 5-minute TTL: rules sharing a prefix start seconds apart
# Server-side refusal fallback: a declined call is re-run on a fallback model inside the same request,
# chosen by refusal category, instead of coming back as a refusal.
REFUSAL_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def salted_system_prompt(system_prompt: str, cache_salt: str) -> str:
    """The system prompt with a per-run tag in front, so runs never share a cached prefix (used by the
    model comparison tool, where a repeat would otherwise read the cache an earlier run wrote)."""
    return f"[run {cache_salt}]\n{system_prompt}" if cache_salt and system_prompt.strip() else system_prompt


class ClaudeTextAnalysisProvider(TextAnalysisProvider):
    def __init__(
        self,
        api_key: str,
        model_id: str,
        temperature: float | None,
        max_tokens: int,
        structured_max_tokens: int | None = None,
        refusal_fallback: bool = False,
        cache_salt: str = "",
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
        # Role assignment and layout mapping. A wide ITD sheet (30+ event blocks) needs ~6k
        # tokens of JSON on top of ~14k of thinking at medium effort, and at high effort
        # it thought past 24k without answering. These calls are streamed, so the 300s
        # timeout bounds the gap between chunks rather than the whole call.
        self._structured_max_tokens = structured_max_tokens or max_tokens
        self._refusal_fallback = refusal_fallback
        self._cache_salt = cache_salt

    @property
    def model(self) -> str:
        return self._model

    def _call_claude_with_retry(
        self,
        messages: list[dict[str, Any]],
        system_prompt: str,
        schema: dict[str, Any] | None = None,
        rule_id: str | None = None,
        cache_system: bool = False,
        max_tokens: int | None = None,
        effort: str | None = None,
        stream: bool = False,
        budget_setting: str = "CLAUDE_TEXT_MAX_TOKENS",
    ) -> tuple[dict[str, Any], str | None]:
        """The parsed JSON answer, and the model that served it when a refusal fallback took over."""
        max_tokens = max_tokens or self._max_tokens
        output_config: dict[str, Any] = {
            "format": {
                "type": "json_schema",
                "schema": schema or RULE_RESULT_JSON_SCHEMA,
            }
        }
        if effort:
            output_config["effort"] = effort
        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": max_tokens,
            "messages": messages,
            "output_config": output_config,
        }
        system_prompt = salted_system_prompt(system_prompt, self._cache_salt)
        if system_prompt.strip():
            # A one-block list either way, so the rendered prefix is the same whether or not it is
            # marked. Below the model's minimum cacheable length the marker is silently ignored.
            system_block: dict[str, Any] = {"type": "text", "text": system_prompt}
            if cache_system:
                system_block["cache_control"] = _CACHE
            request_kwargs["system"] = [system_block]
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature

        messages_api = self._client.messages
        if self._refusal_fallback:
            messages_api = self._client.beta.messages
            request_kwargs["betas"] = [REFUSAL_FALLBACK_BETA]
            request_kwargs["fallbacks"] = "default"
        if stream:
            with messages_api.stream(**request_kwargs) as response_stream:
                response = response_stream.get_final_message()
        else:
            response = messages_api.create(**request_kwargs)
        record_anthropic_usage(response, self._model, "Text rules", rule_id, effort=effort)
        stop_reason = getattr(response, "stop_reason", "unknown")
        # Output tokens cover reasoning as well as the response and are what max_tokens
        # caps, so this is the number to look at when sizing CLAUDE_TEXT_MAX_TOKENS or
        # judging how much rate-limit headroom a run is using.
        usage = getattr(response, "usage", None)
        if usage is not None:
            logger.info(
                "Claude text usage: in=%s out=%s budget=%d effort=%s stop=%s",
                getattr(usage, "input_tokens", "?"),
                getattr(usage, "output_tokens", "?"),
                max_tokens,
                effort or "default",
                stop_reason,
            )
        raw_text = claude_response_text(response, max_tokens, budget_setting)
        try:
            return json.loads(raw_text), served_by_fallback(response)
        except json.JSONDecodeError as exc:
            logger.error(
                "Claude text response JSON parse failed (stop_reason=%s, len=%d, max_tokens=%d): %s — raw: %.500s",
                stop_reason,
                len(raw_text),
                max_tokens,
                exc,
                raw_text,
            )
            if stop_reason == "max_tokens":
                # Deterministic, so retrying is pointless — the budget is the problem.
                # Sonnet 5 thinks by default and max_tokens covers thinking plus the
                # response, so a reasoning-heavy rule can exhaust it before finishing
                # the JSON. Raise CLAUDE_TEXT_MAX_TOKENS.
                raise TruncatedResponseError(
                    f"Response was truncated at max_tokens={max_tokens} before the JSON was "
                    f"complete. Raise {budget_setting} — on models that think by default the "
                    "budget covers reasoning as well as the response.",
                    max_tokens=max_tokens,
                    during_thinking=False,
                ) from exc
            raise

    def evaluate_rule(
        self,
        document_content: str,
        rule: dict,
        system_prompt: str,
        rule_context: str = "",
        cache_content: bool = False,
        shared_context: str = "",
        cache_shared_context: bool = False,
        effort: str | None = None,
    ) -> dict[str, Any]:
        shared, subset, tail = text_rule_prompt(document_content, rule, rule_context, shared_context)
        # Separate blocks, so a rule that reads only the document content still finds the entry a
        # rule with layout metadata wrote at the end of the content block.
        blocks: list[dict[str, Any]] = [{"type": "text", "text": shared}]
        if cache_content:
            blocks[0]["cache_control"] = _CACHE
        if subset:
            blocks.append({"type": "text", "text": subset})
            if cache_shared_context:
                blocks[-1]["cache_control"] = _CACHE
        blocks.append({"type": "text", "text": tail})
        messages: list[dict[str, Any]] = [{"role": "user", "content": blocks}]
        result, served_by = self._call_claude_with_retry(
            messages, system_prompt, rule_id=rule.get("id"), cache_system=cache_content or cache_shared_context,
            effort=effort,
        )
        if served_by:
            result["served_by"] = served_by
        return result

    def complete_structured(
        self,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, Any],
        name: str = "result",
        effort: str | None = None,
    ) -> dict[str, Any]:
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_content}]
        result, _ = self._call_claude_with_retry(
            messages,
            system_prompt,
            schema=json_schema,
            max_tokens=self._structured_max_tokens,
            effort=effort,
            stream=True,
            budget_setting="CLAUDE_STRUCTURED_MAX_TOKENS",
        )
        return result
