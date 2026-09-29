"""Deterministic capital-event checks (plan section 4, Phase 4).

Each check runs on data extracted with the golden layouts, so these tests exercise the
check logic in isolation from the LLM layout mapper. For every fixture the full set of
deterministic rules is evaluated and compared with the manifest's expected verdicts:
clean fixtures pass (or are not_applicable for the event type), and each seeded defect
changes exactly the verdicts it declares. Verdicts that depend on a pending FA
calibration answer are skipped with the calibration item number.
"""
from __future__ import annotations

import importlib

import pytest

from tests.fixtures.generate_capital_event_fixtures import (
    DEFECTS,
    DETERMINISTIC_RULE_IDS,
    EVENT_TYPES,
    VARIANTS,
    FixtureSpec,
)

RULES = [{"id": rule_id, "name": rule_id} for rule_id in DETERMINISTIC_RULE_IDS]
# Rules about a sheet or file as a whole (view setting, file name) cite the sheet but no cell.
SHEET_LEVEL_RULES = {"CE-WB-FILE-NAMING", "CE-WB-PAGE-BREAK-VIEW"}

SPECS = [FixtureSpec(e, v) for e in EVENT_TYPES for v in VARIANTS] + [
    FixtureSpec(d.event_types[0], "standard", d.name) for d in DEFECTS.values()
]


@pytest.fixture(scope="module")
def evaluate():
    loader = importlib.import_module("src.pipeline.workbook.loader")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    extract_mod = importlib.import_module("src.pipeline.workbook.extract")
    checks = importlib.import_module("src.pipeline.workbook.checks")
    cache: dict[str, dict] = {}

    def run(manifest):
        key = manifest.spec.fixture_id
        if key not in cache:
            model = loader.load_workbook_model(manifest.path)
            layouts = {n: layout.parse_layout(r) for n, r in manifest.layouts.items() if n in manifest.relevant_sheets}
            data = extract_mod.extract_workbook_data(model, layouts)
            cache[key] = checks.run_deterministic_checks(
                model, data, RULES, options={"event_type": manifest.spec.event_type})
        return cache[key]

    return run


def test_registry_covers_every_deterministic_rule():
    checks = importlib.import_module("src.pipeline.workbook.checks")
    assert set(checks.DETERMINISTIC_CHECKS) == set(DETERMINISTIC_RULE_IDS)


@pytest.mark.parametrize("rule_id", DETERMINISTIC_RULE_IDS)
@pytest.mark.parametrize("spec", SPECS, ids=lambda s: s.fixture_id)
def test_verdict(evaluate, capital_event_fixtures, spec, rule_id):
    manifest = capital_event_fixtures.get(spec.event_type, spec.variant, spec.defect)
    expected = manifest.expected_verdicts[rule_id]
    if expected is None:
        item = DEFECTS[spec.defect].pending_calibration if spec.defect else 4
        pytest.skip(f"awaiting FA calibration item {item}")
    result = evaluate(manifest)[rule_id]
    assert result.verdict == expected, f"{rule_id}: {result.summary} | {result.findings}"


@pytest.mark.parametrize("spec", [s for s in SPECS if s.defect], ids=lambda s: s.fixture_id)
def test_failures_cite_sheet_and_cell(evaluate, capital_event_fixtures, spec):
    manifest = capital_event_fixtures.get(spec.event_type, spec.variant, spec.defect)
    results = evaluate(manifest)
    failing = [r for r in results.values() if r.verdict == "fail"]
    for result in failing:
        assert result.citations, result.rule_id
        for citation in result.citations:
            assert citation.sheet in manifest.sheet_roles, (result.rule_id, citation)
            if result.rule_id not in SHEET_LEVEL_RULES:
                assert citation.cell, (result.rule_id, citation)
            assert citation.page == list(manifest.sheet_roles).index(citation.sheet) + 1


def test_results_use_the_analysis_result_shape(evaluate, capital_event_fixtures):
    results = evaluate(capital_event_fixtures.get())
    for rule_id, result in results.items():
        assert result.rule_id == rule_id
        assert result.rule_name == rule_id
        assert result.confidence == "high"
        assert result.summary


def test_only_requested_rules_run(capital_event_fixtures):
    loader = importlib.import_module("src.pipeline.workbook.loader")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    extract_mod = importlib.import_module("src.pipeline.workbook.extract")
    checks = importlib.import_module("src.pipeline.workbook.checks")
    manifest = capital_event_fixtures.get()
    model = loader.load_workbook_model(manifest.path)
    layouts = {n: layout.parse_layout(r) for n, r in manifest.layouts.items()}
    data = extract_mod.extract_workbook_data(model, layouts)
    results = checks.run_deterministic_checks(model, data, [{"id": "CE-ALLOC-REFOOT", "name": "Refoot"}],
                                              options={"event_type": "capital_call"})
    assert list(results) == ["CE-ALLOC-REFOOT"]


def test_missing_layout_yields_needs_review(capital_event_fixtures):
    # If the ITD layout could not be validated, ITD-dependent rules must not guess.
    loader = importlib.import_module("src.pipeline.workbook.loader")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    extract_mod = importlib.import_module("src.pipeline.workbook.extract")
    checks = importlib.import_module("src.pipeline.workbook.checks")
    manifest = capital_event_fixtures.get()
    model = loader.load_workbook_model(manifest.path)
    layouts = {n: layout.parse_layout(r) for n, r in manifest.layouts.items() if r["role"] != "itd"}
    data = extract_mod.extract_workbook_data(model, layouts)
    results = checks.run_deterministic_checks(
        model, data, [{"id": "CE-ITD-CUMULATIVE", "name": "x"}, {"id": "CE-ALLOC-REFOOT", "name": "y"}],
        options={"event_type": "capital_call"})
    assert results["CE-ITD-CUMULATIVE"].verdict == "needs_review"
    assert results["CE-ALLOC-REFOOT"].verdict == "pass"
