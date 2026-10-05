"""Prompt ordering and cache-primed dispatch: shared content first, rule text last, the shared prefix
marked for Claude's cache only when another call reads it, and one call per prefix started first.

Offline: providers are stubbed, so no API key is needed.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace as NS
from typing import Any

from src.core.config import AppYaml, PipelineConfig, Settings
from src.core.llm_usage import ContextThreadPoolExecutor, run_cache_primed
from src.pipeline.text_rule_analyzer import TextRuleAnalyzer
from src.providers.text.base import TextAnalysisProvider
from src.providers.text.claude import ClaudeTextAnalysisProvider
from src.providers.text.openai import OpenAITextAnalysisProvider
from src.providers.vision.claude import ClaudeVisionProvider
from src.providers.vision.openai import OpenAIVisionProvider

RESULT_JSON = ('{"rule_id": "R1", "rule_name": "n", "verdict": "pass", "summary": "", "reasoning": "", '
               '"findings": [], "confidence": "high", "citations": []}')
RULE = {"id": "R1", "name": "Rule one", "query": "Check the totals", "acceptance_criteria": "Totals tie"}


# -- dispatcher -----------------------------------------------------------------------------------

def test_primer_runs_before_its_followers_and_every_item_completes():
    started: list[str] = []
    lock = threading.Lock()

    def work(item):
        with lock:
            started.append(item)
        time.sleep(0.02)
        return item

    items = ["a1", "a2", "a3", "b1", "b2", "solo"]
    key = lambda item: None if item == "solo" else item[0]
    with ContextThreadPoolExecutor(max_workers=8) as pool:
        done = [item for item, future in run_cache_primed(pool, items, work, key) if future.result() == item]

    assert sorted(done) == sorted(items)
    # with 8 workers everything could start at once; followers must still wait for their primer
    assert set(started[:3]) == {"a1", "b1", "solo"}
    assert started.index("a2") > started.index("a1") and started.index("b2") > started.index("b1")


def test_no_key_means_everything_starts_at_once():
    barrier = threading.Barrier(3, timeout=2)

    def work(item):
        barrier.wait()  # deadlocks unless all three run concurrently
        return item

    with ContextThreadPoolExecutor(max_workers=3) as pool:
        done = [item for item, _ in run_cache_primed(pool, ["x", "y", "z"], work, lambda item: None)]
    assert sorted(done) == ["x", "y", "z"]


def test_cancellation_stops_followers_and_yields_nothing_more():
    cancelled = threading.Event()
    ran: list[str] = []

    def work(item):
        ran.append(item)
        cancelled.set()
        return item

    with ContextThreadPoolExecutor(max_workers=2) as pool:
        done = list(run_cache_primed(pool, ["a1", "a2", "a3"], work, lambda item: "a", cancelled.is_set))
    assert done == [] and ran == ["a1"]


# -- providers ------------------------------------------------------------------------------------

def _anthropic_response():
    return NS(model="claude-test", stop_reason="end_turn", content=[NS(type="text", text=RESULT_JSON)],
              usage=NS(input_tokens=1, output_tokens=1, cache_read_input_tokens=0, cache_creation_input_tokens=0))


def _capture(store: dict):
    def create(**kwargs):
        store.update(kwargs)
        return _anthropic_response()
    return create


def test_claude_text_puts_content_first_and_marks_it_only_when_asked():
    provider = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-test", temperature=None, max_tokens=100)
    for cache in (True, False):
        sent: dict = {}
        provider._client = NS(messages=NS(create=_capture(sent)))
        provider.evaluate_rule("PAGE TEXT", RULE, "SYSTEM", rule_context="NOTE", cache_content=cache,
                               shared_context="LAYOUT", cache_shared_context=cache)
        shared, layout, tail = sent["messages"][0]["content"]
        assert "PAGE TEXT" in shared["text"] and "RULE NAME" not in shared["text"]
        assert layout["text"].startswith("LAYOUT")
        assert tail["text"].index("NOTE") < tail["text"].index("RULE NAME: Rule one")
        assert ("cache_control" in shared) is cache and ("cache_control" in layout) is cache
        assert "cache_control" not in tail
        assert ("cache_control" in sent["system"][0]) is cache
        assert sent["system"][0]["text"] == "SYSTEM"


def test_claude_text_without_shared_context_sends_two_blocks():
    provider = ClaudeTextAnalysisProvider(api_key="k", model_id="claude-test", temperature=None, max_tokens=100)
    sent: dict = {}
    provider._client = NS(messages=NS(create=_capture(sent)))
    provider.evaluate_rule("PAGE TEXT", RULE, "SYSTEM", cache_content=True)
    assert len(sent["messages"][0]["content"]) == 2


def test_claude_vision_puts_the_image_first_and_marks_it_only_when_asked():
    provider = ClaudeVisionProvider(api_key="k", model_id="claude-test", temperature=None, max_tokens=100,
                                    max_concurrent=2)
    page = {"page": 3, "image_url": "data:image/png;base64,AAAA"}
    for cache in (True, False):
        sent: dict = {}
        provider._client = NS(messages=NS(create=_capture(sent)))
        provider.evaluate_rule(page, RULE, "SYSTEM", cache_content=cache)
        label, image, rule_text = sent["messages"][0]["content"]
        assert label["text"].startswith("RENDERED PDF PAGE") and image["type"] == "image"
        assert "RULE NAME: Rule one" in rule_text["text"]
        assert ("cache_control" in image) is cache


def test_openai_providers_use_the_same_order_without_markers():
    sent: dict = {}

    def chat_create(**kwargs):
        sent.update(kwargs)
        return NS(model="gpt", usage=None, choices=[NS(message=NS(parsed=NS(model_dump=lambda: {}), refusal=None))])

    text = OpenAITextAnalysisProvider(api_key="k", model_id="gpt", temperature=None, max_completion_tokens=None)
    text._client = NS(beta=NS(chat=NS(completions=NS(parse=chat_create))))
    text.evaluate_rule("PAGE TEXT", RULE, "SYSTEM", rule_context="NOTE", cache_content=True,
                       shared_context="LAYOUT")
    user = sent["messages"][-1]["content"]
    assert user.index("PAGE TEXT") < user.index("LAYOUT") < user.index("NOTE") < user.index("RULE NAME")

    vision_sent: dict = {}

    def responses_create(**kwargs):
        vision_sent.update(kwargs)
        return NS(model="gpt", usage=None, output_text=RESULT_JSON)

    vision = OpenAIVisionProvider(api_key="k", model_id="gpt", temperature=0.0, seed=None, max_completion_tokens=100,
                                  image_detail="high", max_concurrent=2)
    vision._client = NS(responses=NS(create=responses_create))
    vision.evaluate_rule({"page": 1, "image_url": "data:image/png;base64,AAAA"}, RULE, "SYSTEM", cache_content=True)
    types = [part["type"] for part in vision_sent["input"][-1]["content"]]
    assert types == ["input_text", "input_image", "input_text"]
    assert "cache_control" not in str(vision_sent)


# -- text analyzer --------------------------------------------------------------------------------

class RecordingProvider(TextAnalysisProvider):
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def evaluate_rule(self, document_content, rule, system_prompt, rule_context="", cache_content=False,
                      shared_context="", cache_shared_context=False):
        with self._lock:
            self.requests.append({"rule": rule["id"], "content": document_content, "context": rule_context,
                                  "cache": cache_content, "layout": shared_context,
                                  "cache_layout": cache_shared_context})
        return {"verdict": "pass", "summary": "", "reasoning": "", "findings": [], "citations": [],
                "confidence": "high"}


PAGES = [
    {"page": 1, "text": "balance sheet page", "tables": [], "page_type": ["balance_sheet"],
     "layout_summary": {"top_lines": [{"text": "Balance Sheet"}]}},
    {"page": 2, "text": "cash flows page", "tables": [], "page_type": ["statement_of_cash_flows"]},
]
GLOBAL = {"id": "PAGE-GLOBAL", "name": "Global", "analysis_type": "text", "scope": "page", "section": "All Statements"}
LAYOUT = {"id": "PAGE-LAYOUT", "name": "Heading alignment", "analysis_type": "text", "scope": "page",
          "section": "All Statements"}
NUMERIC = {"id": "BS-TOTALS", "name": "Balance sheet totals", "analysis_type": "text", "scope": "page",
           "section": "Balance Sheet"}
DOC_A = {"id": "DOC-A", "name": "Whole document A", "analysis_type": "text", "scope": "document"}
DOC_B = {"id": "DOC-B", "name": "Whole document B", "analysis_type": "text", "scope": "document"}


def _analyze(rules: list[dict], prompt_cache: bool = True) -> RecordingProvider:
    provider = RecordingProvider()
    analyzer = TextRuleAnalyzer(app_config=AppYaml(pipeline=PipelineConfig(prompt_cache=prompt_cache)),
                                settings=Settings(_env_file=None), provider=provider)
    analyzer.analyze(pages=PAGES, rules=rules)
    return provider


def test_rules_on_one_page_send_identical_content_and_carry_their_extras_separately():
    provider = _analyze([GLOBAL, LAYOUT, NUMERIC])
    page_one = {r["rule"]: r for r in provider.requests if "balance sheet page" in r["content"]}
    assert set(page_one) == {"PAGE-GLOBAL", "PAGE-LAYOUT", "BS-TOTALS"}
    assert len({r["content"] for r in page_one.values()}) == 1
    assert page_one["PAGE-LAYOUT"]["layout"].startswith("Layout Metadata:")
    assert page_one["PAGE-GLOBAL"]["layout"] == page_one["BS-TOTALS"]["layout"] == ""
    assert page_one["BS-TOTALS"]["context"].startswith("Arithmetic note")
    assert page_one["PAGE-GLOBAL"]["context"] == page_one["PAGE-LAYOUT"]["context"] == ""


def test_layout_is_cached_only_when_two_rules_share_it_and_a_layout_rule_primes_each_page():
    one_layout = _analyze([GLOBAL, LAYOUT])
    assert not any(r["cache_layout"] for r in one_layout.requests)
    # the layout rule is listed second but runs first on each page, so it writes both entries
    first_per_page: dict[str, str] = {}
    for r in one_layout.requests:
        first_per_page.setdefault(r["content"], r["rule"])
    assert set(first_per_page.values()) == {"PAGE-LAYOUT"}

    second_layout = {**LAYOUT, "id": "PAGE-LAYOUT-2"}
    two_layouts = _analyze([GLOBAL, LAYOUT, second_layout])
    flags = {(r["rule"], r["content"]): r["cache_layout"] for r in two_layouts.requests}
    assert all(flag for (rule, _), flag in flags.items() if rule.startswith("PAGE-LAYOUT"))
    assert not any(flag for (rule, _), flag in flags.items() if rule == "PAGE-GLOBAL")


def test_only_prefixes_another_call_reads_are_marked():
    provider = _analyze([GLOBAL, LAYOUT, NUMERIC, DOC_A, DOC_B])
    cache = {(r["rule"], r["content"][:40]): r["cache"] for r in provider.requests}
    # page 1 has three rules and page 2 has two, so all page calls share; both broad rules gather
    # the same pages and so share too
    assert all(cache.values()), cache

    single = _analyze([GLOBAL, NUMERIC, DOC_A])
    by_rule_page = {(r["rule"], "cash flows page" in r["content"] and "balance" not in r["content"]): r["cache"]
                    for r in single.requests}
    assert by_rule_page[("PAGE-GLOBAL", True)] is False  # page 2 has only this rule
    assert by_rule_page[("DOC-A", False)] is False  # the only broad rule


def test_prompt_cache_off_sends_no_markers():
    provider = _analyze([GLOBAL, LAYOUT, NUMERIC, DOC_A, DOC_B], prompt_cache=False)
    assert provider.requests and not any(r["cache"] for r in provider.requests)


def test_broad_layout_rules_keep_layout_metadata_inside_each_page():
    # Gathering every page's layout metadata into one block after the content halved the
    # expected-verdict rate of a live sections check (SCP-SECTIONS-PRESENT, 9/10 -> 5/10 fail).
    doc_layout = {"id": "DOC-LAYOUT", "name": "Section headers present", "analysis_type": "text",
                  "scope": "document"}
    provider = _analyze([doc_layout, DOC_A])
    by_rule = {r["rule"]: r for r in provider.requests}
    content = by_rule["DOC-LAYOUT"]["content"]
    assert content.index("Layout Metadata:") < content.index('<page number="2">')
    assert by_rule["DOC-LAYOUT"]["layout"] == "" and "Layout Metadata" not in by_rule["DOC-A"]["content"]
    # different payloads, so neither is marked for a cache the other cannot read
    assert not by_rule["DOC-LAYOUT"]["cache"] and not by_rule["DOC-A"]["cache"]
