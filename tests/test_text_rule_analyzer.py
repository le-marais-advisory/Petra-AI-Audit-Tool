"""Unit tests for TextRuleAnalyzer's concurrency and classification logic.

These run without network access by injecting a stub provider, so they cover the
threading refactor that folded broad-scope rules into the same pool as page-scope
rules. The stub records concurrency and call order, which is what lets us assert the
latency behaviour (workers actually used, broad rules submitted first) rather than
only the correctness of the results.
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from src.core.config import AppYaml, PipelineConfig, Settings
from src.pipeline.result_builder import build_document_result
from src.pipeline.text_rule_analyzer import MAX_TEXT_WORKERS, TextRuleAnalyzer
from src.providers.text.base import TextAnalysisProvider


class StubTextProvider(TextAnalysisProvider):
    """Deterministic provider that also records concurrency and submission order."""

    def __init__(self, delay: float = 0.0, fail_rule_ids: frozenset[str] = frozenset()) -> None:
        self.delay = delay
        self.fail_rule_ids = fail_rule_ids
        self.calls: list[str] = []  # rule ids, in start order
        self.content_lengths: dict[str, int] = {}
        self.max_in_flight = 0
        self._in_flight = 0
        self._lock = threading.Lock()

    def evaluate_rule(self, document_content: str, rule: dict, system_prompt: str) -> dict[str, Any]:
        rule_id = rule.get("id", "")
        with self._lock:
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            self.calls.append(rule_id)
            self.content_lengths[rule_id] = len(document_content)
        try:
            if self.delay:
                time.sleep(self.delay)
            if rule_id in self.fail_rule_ids:
                raise RuntimeError("stub provider failure")
            return {
                "rule_name": rule.get("name", rule_id),
                "verdict": "pass",
                "summary": f"stub {rule_id}",
                "reasoning": "stub reasoning",
                "findings": [],
                "citations": [{"page": 1, "evidence": "stub evidence"}],
                "confidence": "high",
            }
        finally:
            with self._lock:
                self._in_flight -= 1


def _pages() -> list[dict]:
    # Every page is classified on purpose. rule_applies_to_page is fail-open, so an
    # unclassified page matches every rule and no rule could ever be not_applicable.
    return [
        {"page": 1, "text": "balance sheet page", "tables": [], "char_count": 18, "page_type": ["balance_sheet"]},
        {"page": 2, "text": "cash flows page", "tables": [], "char_count": 15, "page_type": ["statement_of_cash_flows"]},
        {"page": 3, "text": "operations page", "tables": [], "char_count": 15, "page_type": ["statement_of_operations"]},
    ]


# One rule per classification branch.
PAGE_GLOBAL = {"id": "PAGE-GLOBAL", "name": "Global", "analysis_type": "text", "scope": "page", "section": "All Statements"}
PAGE_SCOPED = {"id": "PAGE-BS", "name": "Balance sheet only", "analysis_type": "text", "scope": "page", "section": "Balance Sheet"}
PAGE_NO_MATCH = {"id": "PAGE-NONE", "name": "Never matches", "analysis_type": "text", "scope": "page", "section": "Schedule of Investments"}
DOC_RULE = {"id": "DOC-RULE", "name": "Whole document", "analysis_type": "text", "scope": "document"}
MULTI_OK = {"id": "MULTI-OK", "name": "Two sections", "analysis_type": "text", "scope": "multi_page", "sections": ["Balance Sheet", "Statement of Cash Flows"]}
MULTI_MISSING = {"id": "MULTI-MISSING", "name": "Missing section", "analysis_type": "text", "scope": "multi_page", "sections": ["Balance Sheet", "Schedule of Investments"]}
MULTI_NO_SECTIONS = {"id": "MULTI-EMPTY", "name": "No sections", "analysis_type": "text", "scope": "multi_page", "sections": []}

ALL_RULES = [PAGE_GLOBAL, PAGE_SCOPED, PAGE_NO_MATCH, DOC_RULE, MULTI_OK, MULTI_MISSING, MULTI_NO_SECTIONS]
NOT_APPLICABLE_IDS = {"PAGE-NONE", "MULTI-MISSING", "MULTI-EMPTY"}


def _analyzer(provider: StubTextProvider, concurrent_requests: int = 12) -> TextRuleAnalyzer:
    app_config = AppYaml(pipeline=PipelineConfig(concurrent_requests=concurrent_requests))
    settings = Settings(_env_file=None)
    return TextRuleAnalyzer(app_config=app_config, settings=settings, provider=provider)


def _run(provider: StubTextProvider, rules: list[dict] | None = None, **kwargs) -> dict:
    analyzer = _analyzer(provider, concurrent_requests=kwargs.pop("concurrent_requests", 12))
    return analyzer.analyze(pages=_pages(), rules=rules or ALL_RULES, **kwargs)


def test_every_rule_gets_a_result() -> None:
    """Completeness. Fails with KeyError if a broad result is routed through
    per_rule_page_results, which is keyed only from page rules."""
    provider = StubTextProvider()
    out = _run(provider)

    assert set(out["rule_results"]) == {rule["id"] for rule in ALL_RULES}
    # PAGE-GLOBAL runs on all 3 pages, PAGE-BS on page 1, DOC-RULE and MULTI-OK
    # produce one synthetic page result each.
    assert len(out["page_results"]) == 3 + 1 + 1 + 1


def test_broad_result_keeps_scope_and_matched_pages() -> None:
    """Broad results must bypass _aggregate_rule_results, which never sets scope and
    would recompute matched_pages from citations."""
    provider = StubTextProvider()
    out = _run(provider)

    doc = out["rule_results"]["DOC-RULE"]
    assert doc["scope"] == "document"
    assert doc["matched_pages"] == [1, 2, 3]
    assert doc["execution_status"] == "completed"

    multi = out["rule_results"]["MULTI-OK"]
    assert multi["scope"] == "multi_page"
    assert multi["matched_pages"] == [1, 2]

    # The synthetic page result is attributed to the first gathered page; the
    # integration suite re-aggregates from page_results filtered by (rule_id, page).
    synthetic = [p for p in out["page_results"] if p["rule_id"] == "DOC-RULE"]
    assert len(synthetic) == 1
    assert synthetic[0]["page"] == 1
    assert synthetic[0]["scope"] == "document"


def test_multi_page_sends_only_its_sections() -> None:
    provider = StubTextProvider()
    _run(provider)
    # MULTI-OK covers pages 1-2; DOC-RULE covers all three, so it must be longer.
    assert provider.content_lengths["MULTI-OK"] < provider.content_lengths["DOC-RULE"]


def test_not_applicable_rules_never_reach_the_provider() -> None:
    """Classification happens up front, single-threaded, before any submit."""
    provider = StubTextProvider()
    out = _run(provider)

    for rule_id in NOT_APPLICABLE_IDS:
        assert out["rule_results"][rule_id]["execution_status"] == "not_applicable"
        assert rule_id not in provider.calls

    assert out["rule_results"]["MULTI-EMPTY"]["summary"] == "Rule has no sections defined."
    assert out["rule_results"]["PAGE-NONE"]["summary"] == "No pages matched this rule's section."
    assert "Schedule of Investments" in out["rule_results"]["MULTI-MISSING"]["summary"]


def test_unrecognised_scope_is_not_applicable_not_dropped() -> None:
    """Previously such a rule fell through both filters and vanished from results."""
    odd = {"id": "ODD", "name": "Odd scope", "analysis_type": "text", "scope": "section"}
    provider = StubTextProvider()
    out = _run(provider, rules=[odd])

    assert out["rule_results"]["ODD"]["execution_status"] == "not_applicable"
    # Forced back to a schema-valid value: RuleAssessmentSchema.scope is a Literal.
    assert out["rule_results"]["ODD"]["scope"] == "page"
    assert provider.calls == []


def test_concurrency_reaches_configured_workers() -> None:
    """The only assertion that proves the latency change rather than its correctness."""
    many = [
        {"id": f"R{i}", "name": f"Rule {i}", "analysis_type": "text", "scope": "page", "section": "All Statements"}
        for i in range(10)
    ]
    provider = StubTextProvider(delay=0.05)
    _run(provider, rules=many, concurrent_requests=12)

    # 10 rules x 3 pages = 30 calls; well above the old ceiling of 2.
    assert provider.max_in_flight > 2
    assert provider.max_in_flight <= 12


def test_max_workers_is_floored_and_capped() -> None:
    provider = StubTextProvider()
    assert _analyzer(provider, concurrent_requests=0)._max_workers() == 1
    assert _analyzer(provider, concurrent_requests=-5)._max_workers() == 1
    assert _analyzer(provider, concurrent_requests=500)._max_workers() == MAX_TEXT_WORKERS
    assert _analyzer(provider, concurrent_requests=12)._max_workers() == 12


def test_env_override_wins_over_app_yaml() -> None:
    app_config = AppYaml(pipeline=PipelineConfig(concurrent_requests=12))
    settings = Settings(_env_file=None, PIPELINE_CONCURRENT_REQUESTS=4)
    analyzer = TextRuleAnalyzer(app_config=app_config, settings=settings, provider=StubTextProvider())
    assert analyzer._max_workers() == 4


def test_broad_rules_are_submitted_first() -> None:
    """Longest-processing-time-first: broad calls carry a whole document, so leaving
    them until last would put their latency in the tail."""
    provider = StubTextProvider()
    _run(provider, concurrent_requests=1)

    broad_ran = provider.calls[:2]
    assert set(broad_ran) == {"DOC-RULE", "MULTI-OK"}


def test_cancellation_commits_nothing() -> None:
    provider = StubTextProvider()
    out = _run(provider, is_cancelled=lambda: True)

    assert provider.calls == []
    assert out["page_results"] == []
    # Page rules with applicable pages fall to the cleanup pass.
    assert out["rule_results"]["PAGE-GLOBAL"]["execution_status"] == "error"
    # Broad rules are deliberately absent so build_rule_assessments' "skipped"
    # default applies rather than an aggregate that would strip their scope.
    assert "DOC-RULE" not in out["rule_results"]
    assert "MULTI-OK" not in out["rule_results"]
    # Classification still ran, so not-applicable results are present.
    assert out["rule_results"]["PAGE-NONE"]["execution_status"] == "not_applicable"


def test_provider_error_becomes_an_error_result_on_both_paths() -> None:
    provider = StubTextProvider(fail_rule_ids=frozenset({"DOC-RULE", "PAGE-GLOBAL"}))
    out = _run(provider)

    assert out["rule_results"]["DOC-RULE"]["execution_status"] == "error"
    doc_pages = [p for p in out["page_results"] if p["rule_id"] == "DOC-RULE"]
    assert len(doc_pages) == 1
    assert doc_pages[0]["page"] == 1
    assert doc_pages[0]["execution_status"] == "error"

    global_pages = [p for p in out["page_results"] if p["rule_id"] == "PAGE-GLOBAL"]
    assert len(global_pages) == 3
    assert all(p["execution_status"] == "error" for p in global_pages)

    # An unaffected rule still succeeds.
    assert out["rule_results"]["MULTI-OK"]["execution_status"] == "completed"


def test_callbacks_fire_once_per_page_result() -> None:
    provider = StubTextProvider()
    seen: list[dict] = []
    lock = threading.Lock()

    def on_page_result(page_result: dict, rule_results: dict, page_results: list) -> None:
        with lock:
            seen.append(page_result)
            # Snapshots are taken under the analyzer lock, so each one must be at least
            # as complete as the last -- the progress counter depends on this.
            assert len(page_results) >= len(seen)

    out = _run(provider, on_page_result=on_page_result)
    assert len(seen) == len(out["page_results"])


def test_output_survives_schema_validation() -> None:
    """Guards the scope Literal: a bad scope value would fail here, not in analyze()."""
    odd = {"id": "ODD", "name": "Odd scope", "analysis_type": "text", "scope": "section"}
    provider = StubTextProvider()
    out = _run(provider, rules=[*ALL_RULES, odd])

    result = build_document_result(
        document_id="test",
        pages=_pages(),
        source_filename="test.pdf",
        selected_rules=[*ALL_RULES, odd],
        rule_assessments=list(out["rule_results"].values()),
        text_page_results=out["page_results"],
        visual_page_results=[],
        elapsed_seconds=1.0,
    )
    assert result["analysis"]["rule_assessments"]


def test_no_text_rules_short_circuits() -> None:
    provider = StubTextProvider()
    out = _run(provider, rules=[{"id": "V1", "name": "Vision", "analysis_type": "vision", "scope": "page"}])
    assert out == {"rule_results": {}, "page_results": []}
    assert provider.calls == []


@pytest.mark.parametrize("concurrent_requests", [1, 4, 12])
def test_results_are_identical_regardless_of_concurrency(concurrent_requests: int) -> None:
    """The fold must not make output depend on worker count or completion order."""
    provider = StubTextProvider()
    out = _run(provider, concurrent_requests=concurrent_requests)

    verdicts = {rule_id: r["verdict"] for rule_id, r in out["rule_results"].items()}
    assert verdicts == {
        "PAGE-GLOBAL": "pass",
        "PAGE-BS": "pass",
        "PAGE-NONE": "not_applicable",
        "DOC-RULE": "pass",
        "MULTI-OK": "pass",
        "MULTI-MISSING": "not_applicable",
        "MULTI-EMPTY": "not_applicable",
    }
    assert len(out["page_results"]) == 6
