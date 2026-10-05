from __future__ import annotations

from typing import Any


class TruncatedResponseError(ValueError):
    """The model hit its output-token cap before the answer was complete.

    Retrying the same request is pointless; the caller can retry with more budget or
    less reasoning (a lower effort). ``during_thinking`` is True when the whole budget
    went on reasoning and no answer text was written at all.
    """

    def __init__(self, message: str, *, max_tokens: int, during_thinking: bool) -> None:
        super().__init__(message)
        self.max_tokens = max_tokens
        self.during_thinking = during_thinking


def _answer_blocks(response: Any) -> list[Any]:
    """Content blocks of the final answer. After a refusal fallback the content can start with the
    declining model's partial output, so only the blocks after the last ``fallback`` block count."""
    blocks = list(getattr(response, "content", None) or [])
    last = max((i for i, b in enumerate(blocks) if getattr(b, "type", None) == "fallback"), default=-1)
    return blocks[last + 1:]


def served_by_fallback(response: Any) -> str | None:
    """The model that answered when a server-side refusal fallback took over, else None."""
    blocks = getattr(response, "content", None) or []
    if not any(getattr(b, "type", None) == "fallback" for b in blocks):
        return None
    model = getattr(response, "model", None)
    return model if isinstance(model, str) and model else "a fallback model"


def claude_response_text(response: Any, max_tokens: int, budget_setting: str) -> str:
    """The joined text blocks of a Claude response, or an error that names why there are none."""
    parts = [
        block.text.strip()
        for block in _answer_blocks(response)
        if getattr(block, "type", None) == "text" and getattr(block, "text", "").strip()
    ]
    if parts:
        return "\n".join(parts)
    stop_reason = getattr(response, "stop_reason", None)
    if stop_reason == "max_tokens":
        usage = getattr(response, "usage", None)
        thinking = getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", None)
        spent = f" ({thinking} of them on thinking)" if thinking is not None else ""
        raise TruncatedResponseError(
            f"Claude used its whole max_tokens={max_tokens} budget{spent} before writing any answer. "
            f"Raise {budget_setting} or lower the effort.",
            max_tokens=max_tokens,
            during_thinking=True,
        )
    if stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None)
        explanation = getattr(details, "explanation", None)
        raise ValueError(
            "Claude declined to answer (stop_reason=refusal"
            + (f", category={category}" if category else "")
            + ")"
            + (f": {explanation}" if explanation else ".")
        )
    raise ValueError(f"Claude returned no text content (stop_reason={stop_reason}).")
