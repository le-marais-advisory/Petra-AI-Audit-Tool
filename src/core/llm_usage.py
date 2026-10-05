"""Per-run LLM token telemetry, and the cache-primed dispatch that lets rule calls reuse cached prompts.

A run opens a meter with ``track_usage()``; every provider call made while it is open records the
token counts the API reported. The meter lives in a context variable, so it follows the run into
worker threads as long as they are started through ``ContextThreadPoolExecutor``. Calls made with
no meter open (evals, scripts) are not recorded.

Counts per call:
  input_tokens          every prompt token billed, cached or not
  cache_read_tokens     prompt tokens served from the cache (subset of input_tokens)
  cache_creation_tokens prompt tokens written to the cache (subset of input_tokens)
  output_tokens         every generated token, thinking included
  reasoning_tokens      thinking share of output_tokens
  cost_usd              priced from config/models.yaml; None when a call's model is not listed there
"""
from __future__ import annotations

import contextvars
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Hashable, Iterable, Iterator, TypeVar

T = TypeVar("T")
R = TypeVar("R")

_METER: contextvars.ContextVar["UsageMeter | None"] = contextvars.ContextVar("llm_usage_meter", default=None)
_STAGE: contextvars.ContextVar[str | None] = contextvars.ContextVar("llm_usage_stage", default=None)

_COUNT_FIELDS = ("input_tokens", "cache_read_tokens", "cache_creation_tokens", "output_tokens", "reasoning_tokens")


@dataclass
class LlmCall:
    provider: str
    model: str
    stage: str
    rule_id: str | None
    effort: str | None = None
    input_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cost_usd: float | None = None


class UsageMeter:
    def __init__(self) -> None:
        self._calls: list[LlmCall] = []
        self._lock = threading.Lock()

    def add(self, call: LlmCall) -> None:
        with self._lock:
            self._calls.append(call)

    def calls(self) -> list[LlmCall]:
        with self._lock:
            return list(self._calls)

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
    costs = [c.cost_usd for c in calls]
    row["cost_usd"] = round(sum(costs), 6) if costs and None not in costs else (0.0 if not costs else None)
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


def run_cache_primed(
    executor: ThreadPoolExecutor,
    items: Iterable[T],
    fn: Callable[[T], R],
    cache_key: Callable[[T], Hashable | None],
    is_cancelled: Callable[[], bool] | None = None,
) -> Iterator[tuple[T, Future[R]]]:
    """Run ``fn`` over ``items`` and yield ``(item, future)`` as each finishes.

    Items that share a prompt prefix (the same non-None ``cache_key``) would all miss the cache if
    they started together, because an entry is only readable once the request that writes it is
    under way. So the first item of each group (the primer) is submitted straight away and the rest
    only once it has finished. Items keyed ``None`` are submitted straight away, in input order.
    When ``is_cancelled`` turns true, pending work is cancelled, followers are never submitted and
    nothing more is yielded.
    """
    followers: dict[Hashable, list[T]] = {}
    pending: dict[Future[R], tuple[T, Hashable | None]] = {}
    for item in items:
        key = cache_key(item)
        if key is not None and key in followers:
            followers[key].append(item)
            continue
        if key is not None:
            followers[key] = []
        pending[executor.submit(fn, item)] = (item, key)

    while pending:
        done, _ = wait(pending, return_when=FIRST_COMPLETED)
        if is_cancelled and is_cancelled():
            for future in pending:
                future.cancel()
            return
        for future in done:
            item, key = pending.pop(future)
            if key is not None:
                for follower in followers.pop(key, []):
                    pending[executor.submit(fn, follower)] = (follower, None)
            yield item, future


def _int(value: Any) -> int:
    return value if isinstance(value, int) else 0


def record_anthropic_usage(response: Any, model: str, default_stage: str, rule_id: str | None = None,
                           effort: str | None = None) -> None:
    """Messages API: ``input_tokens`` excludes cache reads and writes, so the total is the sum of all three.
    The model is the one that served the call, which differs from ``model`` after a refusal fallback."""
    meter = _METER.get()
    usage = getattr(response, "usage", None)
    if meter is None or usage is None:
        return
    from src.providers.models import get_model

    served_by = getattr(response, "model", None)
    served_by = served_by if isinstance(served_by, str) and served_by else model
    uncached = _int(getattr(usage, "input_tokens", 0))
    cache_read = _int(getattr(usage, "cache_read_input_tokens", 0))
    cache_write = _int(getattr(usage, "cache_creation_input_tokens", 0))
    output = _int(getattr(usage, "output_tokens", 0))
    spec = get_model(served_by)
    meter.add(LlmCall(
        provider="anthropic", model=served_by, stage=_STAGE.get() or default_stage, rule_id=rule_id, effort=effort,
        input_tokens=uncached + cache_read + cache_write,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_write,
        output_tokens=output,
        reasoning_tokens=_int(getattr(getattr(usage, "output_tokens_details", None), "thinking_tokens", 0)),
        cost_usd=spec.cost(uncached, cache_read, cache_write, output) if spec else None,
    ))

