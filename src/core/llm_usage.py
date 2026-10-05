"""Per-run LLM token telemetry.

A run opens a meter with ``track_usage()``; every provider call made while it is open records the
token counts the API reported. The meter lives in a context variable, so it follows the run into
worker threads as long as they are started through ``ContextThreadPoolExecutor``. Calls made with
no meter open (evals, scripts) are not recorded.

Counts are normalised across APIs:
  input_tokens          every prompt token billed, cached or not
  cache_read_tokens     prompt tokens served from the cache (subset of input_tokens)
  cache_creation_tokens prompt tokens written to the cache (Anthropic only; subset of input_tokens)
  output_tokens         every generated token, reasoning included
  reasoning_tokens      reasoning share of output_tokens (OpenAI only; Claude does not split it out)
"""
from __future__ import annotations

import contextvars
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

_METER: contextvars.ContextVar["UsageMeter | None"] = contextvars.ContextVar("llm_usage_meter", default=None)
_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_usage_stage", default=None)

_COUNT_FIELDS = ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens", "reasoning_tokens")


@dataclass
class LlmCall:
    provider: str
    model: str
    stage: str
    rule_id: str | None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0


class UsageMeter:
    def __init__(self) -> None:
        self._calls: list[LlmCall] = []
        self._lock = threading.Lock()

    def add(self, call: LlmCall) -> None:
        with self._lock:
            self._calls.append(call)

    def summary(self) -> dict[str, Any]:
        with self._lock:
            calls = list(self._calls)
        return {
            "totals": _row("All calls", calls),
            "by_stage": _grouped(calls, lambda c: c.stage),
            "by_model": _grouped(calls, lambda c: f"{c.provider} / {c.model}"),
            "by_rule": _grouped([c for c in calls if c.rule_id], lambda c: c.rule_id or ""),
        }


def _row(label: str, calls: list[LlmCall]) -> dict[str, Any]:
    row: dict[str, Any] = {"label": label, "calls": len(calls)}
    for name in _COUNT_FIELDS:
        row[name] = sum(getattr(c, name) for c in calls)
    return row


def _grouped(calls: list[LlmCall], key) -> list[dict[str, Any]]:
    groups: dict[str, list[LlmCall]] = {}
    for call in calls:
        groups.setdefault(key(call), []).append(call)
    rows = [_row(label, members) for label, members in groups.items()]
    return sorted(rows, key=lambda r: r["input_tokens"] + r["output_tokens"], reverse=True)


@contextmanager
def track_usage(meter: UsageMeter | None = None) -> Iterator[UsageMeter]:
    """Open a meter for the run, or resume ``meter`` for a run split across several blocks. Nested
    calls share the outer meter, so a pipeline that tracks its own run still reports into the job
    that wraps it."""
    current = _METER.get()
    if current is not None:
        yield current
        return
    meter = meter or UsageMeter()
    token = _METER.set(meter)
    try:
        yield meter
    finally:
        _METER.reset(token)


@contextmanager
def usage_stage(name: str) -> Iterator[None]:
    """Label the calls made inside the block. Nested stages are joined: "Prior workbook / Sheet roles"."""
    outer = _STAGE.get()
    token = _STAGE.set(f"{outer} / {name}" if outer else name)
    try:
        yield
    finally:
        _STAGE.reset(token)


class ContextThreadPoolExecutor(ThreadPoolExecutor):
    """ThreadPoolExecutor whose tasks run in a copy of the submitter's context, so the usage meter
    and stage label follow the work into the pool."""

    def submit(self, fn, /, *args, **kwargs):
        return super().submit(contextvars.copy_context().run, fn, *args, **kwargs)


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def _record(provider: str, model: str, default_stage: str, rule_id: str | None, **counts: int) -> None:
    meter = _METER.get()
    if meter is None:
        return
    meter.add(LlmCall(provider=provider, model=model, stage=_STAGE.get() or default_stage, rule_id=rule_id,
                      **counts))


def record_anthropic_usage(response: Any, model: str, default_stage: str, rule_id: str | None = None) -> None:
    """Messages API: ``input_tokens`` excludes cache reads and writes, so the total is the sum of all three."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    uncached = _int(getattr(usage, "input_tokens", 0))
    cache_read = _int(getattr(usage, "cache_read_input_tokens", 0))
    cache_write = _int(getattr(usage, "cache_creation_input_tokens", 0))
    _record("anthropic", getattr(response, "model", None) or model, default_stage, rule_id,
            input_tokens=uncached + cache_read + cache_write,
            cache_read_tokens=cache_read,
            cache_creation_tokens=cache_write,
            output_tokens=_int(getattr(usage, "output_tokens", 0)))


def record_openai_chat_usage(response: Any, model: str, default_stage: str, rule_id: str | None = None) -> None:
    """Chat Completions API: ``prompt_tokens`` already includes the cached share."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)
    _record("openai", getattr(response, "model", None) or model, default_stage, rule_id,
            input_tokens=_int(getattr(usage, "prompt_tokens", 0)),
            cache_read_tokens=_int(getattr(prompt_details, "cached_tokens", 0)),
            output_tokens=_int(getattr(usage, "completion_tokens", 0)),
            reasoning_tokens=_int(getattr(completion_details, "reasoning_tokens", 0)))


def record_openai_responses_usage(response: Any, model: str, default_stage: str, rule_id: str | None = None) -> None:
    """Responses API: ``input_tokens`` already includes the cached share."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    input_details = getattr(usage, "input_tokens_details", None)
    output_details = getattr(usage, "output_tokens_details", None)
    _record("openai", getattr(response, "model", None) or model, default_stage, rule_id,
            input_tokens=_int(getattr(usage, "input_tokens", 0)),
            cache_read_tokens=_int(getattr(input_details, "cached_tokens", 0)),
            output_tokens=_int(getattr(usage, "output_tokens", 0)),
            reasoning_tokens=_int(getattr(output_details, "reasoning_tokens", 0)))
