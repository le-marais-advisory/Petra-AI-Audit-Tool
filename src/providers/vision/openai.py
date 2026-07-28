from __future__ import annotations

import json
import threading
from typing import Any

import httpx
from openai import OpenAI

from src.providers.analysis_result import RULE_RESULT_JSON_SCHEMA, build_vector_data_text, compact_rule_payload
from src.providers.vision.base import VisionProvider


_GLOBAL_OPENAI_SEMAPHORE = None
_SEMAPHORE_LOCK = threading.Lock()


def get_global_semaphore(max_concurrent: int = 10) -> threading.Semaphore:
    global _GLOBAL_OPENAI_SEMAPHORE
    with _SEMAPHORE_LOCK:
        if _GLOBAL_OPENAI_SEMAPHORE is None:
            _GLOBAL_OPENAI_SEMAPHORE = threading.Semaphore(max_concurrent)
    return _GLOBAL_OPENAI_SEMAPHORE


class OpenAIVisionProvider(VisionProvider):
    def __init__(
        self,
        api_key: str,
        model_id: str,
        temperature: float,
        seed: int | None,
        max_completion_tokens: int,
        image_detail: str,
        max_concurrent: int,
    ) -> None:
        # Explicit timeout and retry budget. The SDK defaults are a 600s timeout with
        # 2 retries, and timeouts are themselves retried, so an unresponsive call could
        # occupy a worker for ~30 minutes. 180s rather than the text providers' 300s:
        # vision is capped at CLAUDE_VISION_MAX_TOKENS (1600), so it cannot legitimately
        # run as long as a reasoning-heavy text call. The SDK's own 429/5xx retries
        # (which honour retry-after) are kept as the rate-limit defence.
        self._client = OpenAI(api_key=api_key, timeout=httpx.Timeout(180.0, connect=5.0), max_retries=2)
        self._model = model_id
        self._temperature = temperature
        self._seed = seed
        self._max_tokens = max_completion_tokens
        self._detail = image_detail
        self._semaphore = get_global_semaphore(max_concurrent)

    def _call_openai_with_retry(self, input_items: list[dict[str, Any]], schema: dict[str, Any], schema_name: str) -> str:
        responses_api = getattr(self._client, "responses", None)
        if responses_api is None:
            raise RuntimeError(
                "The installed openai SDK does not expose Responses API. Upgrade the openai package to a version "
                "that supports client.responses.create()."
            )

        request_kwargs: dict[str, Any] = {
            "model": self._model,
            "input": input_items,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
        }
        if self._max_tokens is not None:
            request_kwargs["max_output_tokens"] = self._max_tokens
        if self._temperature is not None:
            request_kwargs["temperature"] = self._temperature

        with self._semaphore:
            response = responses_api.create(**request_kwargs)
        output_text = getattr(response, "output_text", "") or ""
        if output_text:
            return output_text

        for item in getattr(response, "output", []) or []:
            for content in getattr(item, "content", []) or []:
                text = getattr(content, "text", "") or ""
                if text:
                    return text

        raise ValueError("Model returned no structured text content.")

    def evaluate_rule(self, page_image: dict[str, Any], rule: dict, system_prompt: str) -> dict[str, Any]:
        page_number = int(page_image.get("page", 0) or 0)
        content: list[dict[str, Any]] = [
            {
                "type": "input_text",
                "text": (
                    "Evaluate the following vision rule against the rendered PDF page image.\n"
                    f"{compact_rule_payload(rule)}\n"
                    f"{build_vector_data_text(page_image)}"
                    "RENDERED PDF PAGE:\n"
                    f"PDF page {page_number}\n"
                ),
            }
        ]
        content.append({"type": "input_text", "text": f"PDF page {page_number}"})
        content.append(
            {
                "type": "input_image",
                "image_url": page_image["image_url"],
                "detail": page_image.get("detail", self._detail),
            }
        )

        input_items: list[dict[str, Any]] = []
        if system_prompt.strip():
            input_items.append({"role": "system", "content": [{"type": "input_text", "text": system_prompt}]})
        input_items.append({"role": "user", "content": content})

        try:
            text = self._call_openai_with_retry(input_items, RULE_RESULT_JSON_SCHEMA, "vision_rule_result")
            return json.loads(text)
        except Exception as exc:
            return {
                "rule_id": rule.get("id", ""),
                "rule_name": rule.get("name", ""),
                # Report the failure as such. Without this the analyzer stamps "completed" and an
                # API outage becomes indistinguishable from the model genuinely being unsure.
                "execution_status": "error",
                "verdict": "needs_review",
                "summary": "Vision analysis failed.",
                "reasoning": f"OpenAI API error: {exc}",
                "findings": [],
                "confidence": "low",
                "citations": [],
            }
