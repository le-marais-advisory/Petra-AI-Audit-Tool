"""Claude response handling: why an answer is missing, effort and budget on structured calls (no API key)."""
from __future__ import annotations

from types import SimpleNamespace as NS

import pytest

from src.providers.errors import TruncatedResponseError, claude_response_text
from src.providers.text.claude import ClaudeTextAnalysisProvider


def _response(stop_reason="end_turn", text=None, thinking_tokens=None, stop_details=None):
    content = [NS(type="thinking", thinking="")]
    if text is not None:
        content.append(NS(type="text", text=text))
    usage = NS(input_tokens=10, output_tokens=100, cache_read_input_tokens=0, cache_creation_input_tokens=0,
               output_tokens_details=NS(thinking_tokens=thinking_tokens))
    return NS(model="claude-test", stop_reason=stop_reason, content=content, usage=usage, stop_details=stop_details)


def test_text_blocks_are_joined():
    assert claude_response_text(_response(text=' {"a": 1} '), 100, "X") == '{"a": 1}'


def test_budget_spent_on_thinking_is_a_truncation_naming_the_setting():
    with pytest.raises(TruncatedResponseError) as exc:
        claude_response_text(_response("max_tokens", thinking_tokens=24000), 24000, "CLAUDE_STRUCTURED_MAX_TOKENS")
    assert exc.value.during_thinking and exc.value.max_tokens == 24000
    assert "24000 of them on thinking" in str(exc.value)
    assert "CLAUDE_STRUCTURED_MAX_TOKENS" in str(exc.value)


def test_refusal_names_the_category():
    details = NS(category="cyber", explanation="Declined.")
    with pytest.raises(ValueError, match="refusal, category=cyber"):
        claude_response_text(_response("refusal", stop_details=details), 100, "X")


def test_other_empty_answers_report_the_stop_reason():
    with pytest.raises(ValueError, match="stop_reason=end_turn"):
        claude_response_text(_response(), 100, "X")


def _provider(**kwargs):
    provider = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-test", temperature=None, max_tokens=24000,
                                          **kwargs)
    calls: dict[str, list[dict]] = {"create": [], "stream": []}

    class Stream:
        def __init__(self, message):
            self.message = message

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get_final_message(self):
            return self.message

    def create(**kw):
        calls["create"].append(kw)
        return _response(text='{"ok": true}')

    def stream(**kw):
        calls["stream"].append(kw)
        return Stream(_response(text='{"ok": true}'))

    provider._client = NS(messages=NS(create=create, stream=stream))
    return provider, calls


def test_structured_calls_stream_with_their_own_budget_and_effort():
    provider, calls = _provider(structured_max_tokens=64000)
    assert provider.complete_structured("s", "u", {"type": "object"}, effort="medium") == {"ok": True}
    assert calls["create"] == []
    (request,) = calls["stream"]
    assert request["max_tokens"] == 64000
    assert request["output_config"]["effort"] == "medium"


def test_structured_budget_defaults_to_the_text_budget_and_effort_to_the_model():
    provider, calls = _provider()
    provider.complete_structured("s", "u", {"type": "object"})
    (request,) = calls["stream"]
    assert request["max_tokens"] == 24000
    assert "effort" not in request["output_config"]


def test_rule_calls_are_unchanged():
    provider, calls = _provider(structured_max_tokens=64000)
    provider.evaluate_rule("content", {"id": "R1", "name": "n"}, "system")
    (request,) = calls["create"]
    assert request["max_tokens"] == 24000 and "effort" not in request["output_config"]
