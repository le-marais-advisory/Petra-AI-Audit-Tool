"""Integration tests for the end-to-end validation pipeline.

Each test node corresponds to one (case, rule) pair defined in cases.yaml.
The session-scoped `pipeline_results` fixture (conftest.py) runs the real
validation pipeline — including live LLM calls — once per case and caches
the result so every rule assertion for the same document shares a single run.

Running:
    pytest tests/integration -m integration                          # full run, all rules
    pytest tests/integration -m integration --severity critical      # critical-severity rules only
    pytest tests/integration -m integration --rule GRAM-SPELL        # one specific rule
    pytest tests/integration -m integration --rule A --rule B        # multiple rules
    pytest tests/integration -m integration --severity critical --rule GRAM-SPELL  # combined
    pytest tests/integration -m integration -v                       # verbose

Adding cases: see the instructions at the top of cases.yaml, or run
    python scripts/update_integration_expectations.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

_CASES_FILE = Path(__file__).parent / "cases.yaml"


def _collect_params() -> list[pytest.param]:
    _REPO_ROOT = Path(__file__).parent.parent.parent
    rule_severity = {
        r["id"]: r.get("severity", "major")
        for r in json.loads((_REPO_ROOT / "rules" / "rules.json").read_text())["rules"]
    }

    data = yaml.safe_load(_CASES_FILE.read_text())
    cases = (data or {}).get("cases") or []
    params: list[pytest.param] = []
    for case in cases:
        for exp in case.get("expected") or []:
            pages_filter = exp.get("pages")
            pages_suffix = (
                f"[p{','.join(str(p) for p in sorted(pages_filter))}]"
                if pages_filter
                else ""
            )
            node_id = f"{case['id']}/{exp['rule_id']}{pages_suffix}"
            severity = rule_severity.get(exp["rule_id"], "major")
            params.append(
                pytest.param(
                    case["id"],
                    exp["rule_id"],
                    exp["verdict"],
                    exp.get("matched_pages"),
                    pages_filter,
                    id=node_id,
                    marks=[getattr(pytest.mark, severity)],
                )
            )
    return params


_PARAMS = _collect_params()


def _reaggregate_verdict(page_results: list[dict], rule_id: str, pages: list[int]) -> str:
    """Re-aggregate a verdict from page-level results restricted to the given pages."""
    pages_set = set(pages)
    filtered = [
        r
        for r in page_results
        if r.get("rule_id") == rule_id
        and int(r.get("page", 0)) in pages_set
        and r.get("execution_status") == "completed"
    ]
    if not filtered:
        return "needs_review"
    if any(r.get("verdict") == "fail" for r in filtered):
        return "fail"
    if all(r.get("verdict") in {"pass", "not_applicable"} for r in filtered):
        return "pass"
    return "needs_review"


def _get_page_breakdown(
    page_results: list[dict], rule_id: str, pages: list[int]
) -> list[tuple[int, str, list[str]]]:
    """Return sorted (page, verdict, findings) tuples for each page in the filter set."""
    pages_set = set(pages)
    found: dict[int, tuple[str, list[str]]] = {}
    for r in page_results:
        if r.get("rule_id") == rule_id and int(r.get("page", 0)) in pages_set:
            page = int(r["page"])
            if r.get("execution_status") == "completed":
                found[page] = (r.get("verdict", "unknown"), r.get("findings", []))
            elif page not in found:
                found[page] = (f"not_completed ({r.get('execution_status', 'unknown')})", [])
    for p in pages_set - found.keys():
        found[p] = ("no_result", [])
    return sorted((p, v, f) for p, (v, f) in found.items())


def _pages_with_results(page_results: list[dict], rule_id: str) -> list[int]:
    """Return all page numbers that have completed results for rule_id (outside any filter)."""
    return sorted({
        int(r.get("page", 0))
        for r in page_results
        if r.get("rule_id") == rule_id and r.get("execution_status") == "completed"
    })


def _diagnose_page_results(page_results: list[dict]) -> str:
    """Summarise what rule IDs and pages are actually stored in the page results list."""
    if not page_results:
        return "empty (len=0)"
    from collections import defaultdict
    by_rule: dict[str, list[int]] = defaultdict(list)
    for r in page_results:
        by_rule[str(r.get("rule_id", "<missing>"))].append(int(r.get("page", 0)))
    parts = [f"{rid}: pages {sorted(pages)}" for rid, pages in sorted(by_rule.items())]
    return f"{len(page_results)} entries — " + "; ".join(parts)


@pytest.mark.integration
@pytest.mark.parametrize(
    "case_id,rule_id,expected_verdict,expected_pages,pages_filter",
    _PARAMS if _PARAMS else [pytest.param("_none", "_none", "_none", None, None, id="no_cases")],
)
def test_rule_verdict(
    pipeline_results: dict,
    case_id: str,
    rule_id: str,
    expected_verdict: str,
    expected_pages: list[int] | None,
    pages_filter: list[int] | None,
) -> None:
    if case_id == "_none":
        pytest.skip("No integration test cases defined in cases.yaml")

    result = pipeline_results.get(case_id)
    if result is None:
        pytest.skip(f"Case '{case_id}' not included in this run (filtered by --rule)")

    error = result.get("__error__")
    if error:
        pytest.fail(error)

    assessments: dict[str, dict] = {
        a["rule_id"]: a
        for a in result.get("analysis", {}).get("rule_assessments", [])
    }

    assert rule_id in assessments, (
        f"Rule '{rule_id}' not found in results for case '{case_id}'. "
        f"Rules present: {sorted(assessments)}"
    )

    actual = assessments[rule_id]

    if pages_filter is not None:
        text_page_results = result.get("analysis", {}).get("text_page_results", [])
        visual_page_results = result.get("analysis", {}).get("visual_page_results", [])
        all_page_results = text_page_results + visual_page_results
        actual_verdict = _reaggregate_verdict(all_page_results, rule_id, pages_filter)
        if actual_verdict != expected_verdict:
            breakdown = _get_page_breakdown(all_page_results, rule_id, pages_filter)
            page_lines_parts = []
            for p, v, findings in breakdown:
                line = f"  page {p}: {v}"
                for finding in findings:
                    line += f"\n    - {finding}"
                page_lines_parts.append(line)
            page_lines = "\n".join(page_lines_parts)
            all_no_result = all(v == "no_result" for _, v, _ in breakdown)
            hint = ""
            if all_no_result:
                other = _pages_with_results(all_page_results, rule_id)
                if other:
                    hint = f"\n  Note: rule has completed results on pages {other} (none overlap this filter)"
                else:
                    text_diag = _diagnose_page_results(
                        result.get("analysis", {}).get("text_page_results", [])
                    )
                    vis_diag = _diagnose_page_results(
                        result.get("analysis", {}).get("visual_page_results", [])
                    )
                    hint = (
                        f"\n  Note: no page-level results for this rule — "
                        f"text_page_results: {text_diag} | visual_page_results: {vis_diag}"
                    )
            pytest.fail(
                f"Case '{case_id}' / Rule '{rule_id}' / Pages {sorted(pages_filter)}\n"
                f"  expected: {expected_verdict}\n"
                f"  got:      {actual_verdict}"
                f"{hint}\n"
                f"\nPage breakdown:\n{page_lines}"
            )
    else:
        if actual["verdict"] != expected_verdict:
            pytest.fail(
                f"Case '{case_id}' / Rule '{rule_id}'\n"
                f"  expected: {expected_verdict}\n"
                f"  got:      {actual['verdict']}\n"
                f"\nSummary: {actual.get('summary', '')}\n"
                f"\nFindings: {actual.get('findings', [])}"
            )

    if expected_pages is not None:
        assert sorted(actual.get("matched_pages") or []) == sorted(expected_pages), (
            f"Case '{case_id}' / Rule '{rule_id}': "
            f"expected matched_pages {sorted(expected_pages)}, "
            f"got {sorted(actual.get('matched_pages') or [])}"
        )
