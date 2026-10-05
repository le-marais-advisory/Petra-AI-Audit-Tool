"""LLM token telemetry: per-API normalisation, run scoping, stage labels and thread propagation.

Provider clients are replaced by stubs returning SDK-shaped usage objects, so no API key is needed.
"""
from __future__ import annotations

from types import SimpleNamespace as NS

from src.core.llm_usage import (
    ContextThreadPoolExecutor,
    record_anthropic_usage,
    record_openai_chat_usage,
    record_openai_responses_usage,
    track_usage,
    usage_stage,
)
from src.providers.text.claude import ClaudeTextAnalysisProvider
from src.providers.text.openai import OpenAITextAnalysisProvider
from src.providers.vision.claude import ClaudeVisionProvider
from src.providers.vision.openai import OpenAIVisionProvider

RESULT_JSON = '{"rule_id": "R1", "rule_name": "n", "verdict": "pass", "summary": "", "reasoning": "", "findings": [], "confidence": "high", "citations": []}'


def _anthropic_response(model="claude-test"):
    return NS(model=model, stop_reason="end_turn", content=[NS(type="text", text=RESULT_JSON)],
              usage=NS(input_tokens=100, cache_read_input_tokens=800, cache_creation_input_tokens=50, output_tokens=40))


class _AnthropicStream:
    """Stands in for the context manager ``client.messages.stream`` returns."""

    def __init__(self, message):
        self._message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._message


def _openai_chat_response():
    return NS(model="gpt-test",
              usage=NS(prompt_tokens=1000, completion_tokens=70,
                       prompt_tokens_details=NS(cached_tokens=600), completion_tokens_details=NS(reasoning_tokens=30)),
              choices=[NS(message=NS(parsed=None, refusal=None, content=RESULT_JSON))])


def _openai_responses_response():
    return NS(model="gpt-test", output_text=RESULT_JSON,
              usage=NS(input_tokens=2000, output_tokens=90,
                       input_tokens_details=NS(cached_tokens=1500), output_tokens_details=NS(reasoning_tokens=20)))


def test_anthropic_input_total_includes_cache_reads_and_writes():
    with track_usage() as meter:
        record_anthropic_usage(_anthropic_response(), "fallback", "Text rules", "R1")
    totals = meter.summary()["totals"]
    assert totals == {"label": "All calls", "calls": 1, "input_tokens": 950, "cache_read_tokens": 800,
                      "cache_creation_tokens": 50, "output_tokens": 40, "reasoning_tokens": 0}


def test_openai_counts_are_taken_as_reported():
    with track_usage() as meter:
        record_openai_chat_usage(_openai_chat_response(), "fallback", "Text rules")
        record_openai_responses_usage(_openai_responses_response(), "fallback", "Vision rules")
    summary = meter.summary()
    assert summary["totals"]["input_tokens"] == 3000
    assert summary["totals"]["cache_read_tokens"] == 2100
    assert summary["totals"]["output_tokens"] == 160
    assert summary["totals"]["reasoning_tokens"] == 50
    assert {r["label"] for r in summary["by_stage"]} == {"Text rules", "Vision rules"}
    assert summary["by_rule"] == []  # neither call named a rule


def test_missing_usage_and_no_open_meter_are_ignored():
    record_anthropic_usage(_anthropic_response(), "m", "Text rules")  # no meter: no error, nothing kept
    with track_usage() as meter:
        record_anthropic_usage(NS(model="m"), "m", "Text rules")
    assert meter.summary()["totals"]["calls"] == 0


def test_nested_tracking_shares_the_outer_meter_and_stages_join():
    with track_usage() as outer:
        with usage_stage("Prior workbook"), usage_stage("Sheet roles"):
            with track_usage() as inner:
                record_anthropic_usage(_anthropic_response(), "m", "Text rules")
    assert inner is outer
    assert [r["label"] for r in outer.summary()["by_stage"]] == ["Prior workbook / Sheet roles"]


def test_meter_and_stage_follow_work_into_the_pool():
    with track_usage() as meter, usage_stage("Layout mapping"):
        with ContextThreadPoolExecutor(max_workers=4) as pool:
            for future in [pool.submit(record_anthropic_usage, _anthropic_response(), "m", "x") for _ in range(8)]:
                future.result()
    summary = meter.summary()
    assert summary["totals"]["calls"] == 8
    assert summary["by_stage"][0]["label"] == "Layout mapping"


def test_resumed_meter_accumulates_across_blocks():
    with track_usage() as meter:
        record_anthropic_usage(_anthropic_response(), "m", "Text rules")
    with track_usage(meter):
        record_anthropic_usage(_anthropic_response(), "m", "Vision rules")
    assert meter.summary()["totals"]["calls"] == 2


def test_every_provider_records_its_calls():
    claude_text = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-test", temperature=None, max_tokens=100)
    claude_text._client = NS(messages=NS(create=lambda **kw: _anthropic_response(),
                                         stream=lambda **kw: _AnthropicStream(_anthropic_response())))
    openai_text = OpenAITextAnalysisProvider(api_key="k", model_id="gpt-test", temperature=None,
                                             max_completion_tokens=None)
    openai_text._client = NS(chat=NS(completions=NS(create=lambda **kw: _openai_chat_response())))
    claude_vision = ClaudeVisionProvider(api_key="k", model_id="claude-test", temperature=None, max_tokens=100,
                                         max_concurrent=2)
    claude_vision._client = NS(messages=NS(create=lambda **kw: _anthropic_response()))
    openai_vision = OpenAIVisionProvider(api_key="k", model_id="gpt-test", temperature=0.0, seed=None,
                                         max_completion_tokens=100, image_detail="high", max_concurrent=2)
    openai_vision._client = NS(responses=NS(create=lambda **kw: _openai_responses_response()))

    rule = {"id": "R1", "name": "Rule one"}
    page = {"page": 1, "image_url": "data:image/png;base64,AAAA"}
    with track_usage() as meter:
        claude_text.evaluate_rule("content", rule, "system")
        claude_text.complete_structured("system", "user", {"type": "object"}, name="sheet_roles")
        openai_text.complete_structured("system", "user", {"type": "object", "properties": {}})
        claude_vision.evaluate_rule(page, rule, "system")
        openai_vision.evaluate_rule(page, rule, "system")
    summary = meter.summary()
    assert summary["totals"]["calls"] == 5
    assert {r["label"]: r["calls"] for r in summary["by_stage"]} == {"Text rules": 3, "Vision rules": 2}
    assert {r["label"]: r["calls"] for r in summary["by_model"]} == {"anthropic / claude-test": 3,
                                                                     "openai / gpt-test": 2}
    assert {r["label"]: r["calls"] for r in summary["by_rule"]} == {"R1": 3}
