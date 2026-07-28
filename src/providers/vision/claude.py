from __future__ import annotations

import json
import threading
from typing import Any

from anthropic import Anthropic
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from src.providers.analysis_result import RULE_RESULT_JSON_SCHEMA, build_vector_data_text, compact_rule_payload
from src.providers.vision.base import VisionProvider


_GLOBAL_ANTHROPIC_SEMAPHORE = None
_SEMAPHORE_LOCK = threading.Lock()
_SUPPORTED_IMAGE_MEDIA_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


def get_global_semaphore(max_concurrent: int = 10) -> threading.Semaphore:
    global _GLOBAL_ANTHROPIC_SEMAPHORE
    with _SEMAPHORE_LOCK:
        if _GLOBAL_ANTHROPIC_SEMAPHORE is None:
            _GLOBAL_ANTHROPIC_SEMAPHORE = threading.Semaphore(max_concurrent)
    return _GLOBAL_ANTHROPIC_SEMAPHORE


def _extract_text_content(content_blocks: list[Any]) -> str:
    parts = [
        block.text.strip()
        for block in content_blocks
        if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
    ]
    if parts:
        return "\n".join(parts)
    raise ValueError("Claude returned no structured text content.")


def _build_image_source(image_url: str) -> dict[str, str]:
    if image_url.startswith("data:"):
        header, encoded = image_url.split(",", 1)
        media_type = header[5:].split(";", 1)[0]
        if media_type not in _SUPPORTED_IMAGE_MEDIA_TYPES:
            raise ValueError(f"Unsupported image media type for Claude vision analysis: {media_type}")
        return {
            "type": "base64",
            "media_type": media_type,
            "data": encoded,
        }
    if image_url.startswith(("http://", "https://")):
        return {
            "type": "url",
            "url": image_url,
        }
    raise ValueError("Claude vision analysis requires a data URL or an HTTP(S) image URL.")


class ClaudeVisionProvider(VisionProvider):
    def __init__(
        self,
        api_key: str,
        model_id: str,
        temperature: float | None,
        max_tokens: int,
        max_concurrent: int,
    ) -> None:
        self._client = Anthropic(api_key=api_key)
        self._model = model_id
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._semaphore = get_global_semaphore(max_concurrent)

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

        with self._semaphore:
            response = self._client.messages.create(**request_kwargs)
        return json.loads(_extract_text_content(response.content))

    def evaluate_rule(self, page_image: dict[str, Any], rule: dict, system_prompt: str) -> dict[str, Any]:
        page_number = int(page_image.get("page", 0) or 0)
        messages: list[dict[str, Any]] = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Evaluate the following vision rule against the rendered PDF page image.\n"
                            f"{compact_rule_payload(rule)}\n"
                            f"{build_vector_data_text(page_image)}"
                            "RENDERED PDF PAGE:\n"
                            f"PDF page {page_number}\n"
                        ),
                    },
                    {
                        "type": "image",
                        "source": _build_image_source(page_image["image_url"]),
                    },
                ],
            }
        ]

        try:
            return self._call_claude_with_retry(messages, system_prompt)
        except Exception as exc:
            return {
                "rule_id": rule.get("id", ""),
                "rule_name": rule.get("name", ""),
                # Report the failure as such. Without this the analyzer stamps "completed" and an
                # API outage becomes indistinguishable from the model genuinely being unsure.
                "execution_status": "error",
                "verdict": "needs_review",
                "summary": "Vision analysis failed.",
                "reasoning": f"Claude API error: {exc}",
                "findings": [],
                "confidence": "low",
                "citations": [],
            }
