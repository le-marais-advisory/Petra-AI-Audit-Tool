"""Facts pre-pass for hybrid rules (plan section 4).

Hybrid rules are judged by the LLM, but the numbers and cell inventories it judges
come from code. These tests pin the facts that matter for each seeded defect; the LLM
verdicts themselves are covered by tests/evals/test_hybrid_rules_eval.py.
"""
from __future__ import annotations

import importlib
from decimal import Decimal

import pytest

from tests.fixtures.generate_capital_event_fixtures import DEFECTS, EVENT_TYPES, FixtureSpec


@pytest.fixture(scope="module")
def facts_for():
    loader = importlib.import_module("src.pipeline.workbook.loader")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    extract_mod = importlib.import_module("src.pipeline.workbook.extract")
    facts_mod = importlib.import_module("src.pipeline.workbook.facts")

    def run(manifest, rule_id):
        model = loader.load_workbook_model(manifest.path)
        layouts = {n: layout.parse_layout(r) for n, r in manifest.layouts.items() if n in manifest.relevant_sheets}
        data = extract_mod.extract_workbook_data(model, layouts)
        assert data.reference_sheets == manifest.reference_sheets
        return facts_mod.build_facts(rule_id, model, data, options={"event_type": manifest.spec.event_type})

    return run


def _specs_for(rule_id):
    specs = [FixtureSpec(e) for e in EVENT_TYPES]
    specs += [FixtureSpec(d.event_types[0], d.variant, d.name) for d in DEFECTS.values() if rule_id in d.facts]
    return specs


def _expectation(capital_event_fixtures, spec, rule_id):
    manifest = capital_event_fixtures.get(spec.event_type, spec.variant, spec.defect)
    expected = manifest.facts.get(rule_id)
    if expected is None:
        pytest.skip(f"{rule_id} has no facts expectation for {spec.fixture_id}")
    return manifest, expected


@pytest.mark.parametrize("spec", _specs_for("CE-WB-NO-PLACEHOLDERS"), ids=lambda s: s.fixture_id)
def test_placeholder_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-WB-NO-PLACEHOLDERS")
    facts = facts_for(manifest, "CE-WB-NO-PLACEHOLDERS")
    assert len(facts["placeholders"]) == expected["placeholder_count"]
    scanned = manifest.relevant_sheets + manifest.reference_sheets
    for hit in facts["placeholders"]:
        assert hit["sheet"] in scanned and hit["cell"] and hit["text"]
    # FA: a TBD on a pending status/date field that no event formula uses is not a placeholder.
    assert len(facts["tbd_cells"]) == expected["tbd_cells"]
    assert sum(1 for t in facts["tbd_cells"] if t["referenced_by_event_formulas"]) == expected["tbd_referenced"]


@pytest.mark.parametrize("spec", _specs_for("CE-ITD-EVENT-BLOCK"), ids=lambda s: s.fixture_id)
def test_itd_event_block_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-ITD-EVENT-BLOCK")
    facts = facts_for(manifest, "CE-ITD-EVENT-BLOCK")
    block = facts["current_block"]
    assert block["label"] == manifest.truth["itd"]["current_block_label"]
    unclassified = [c["column"] for c in block["columns"] if not c["classifications"]]
    assert len(unclassified) == expected["unclassified_current_columns"]
    assert facts["columns_without_primary_x"] == unclassified
    assert facts["columns_with_multiple_primary_x"] == []
    for column in block["columns"]:
        assert column["component_type"]
    assert facts["label_is_unique"] is True
    assert facts["appended_after_prior_blocks"] is True


@pytest.mark.parametrize("spec", _specs_for("CE-WB-MERGE-TABS"), ids=lambda s: s.fixture_id)
def test_merge_tab_facts_ignore_inactive_investors(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-WB-MERGE-TABS")
    facts = facts_for(manifest, "CE-WB-MERGE-TABS")
    ignored = [n for tab in facts["merge_tabs"] for n in tab["inactive_investors_ignored"]]
    assert len(ignored) == expected["inactive_ignored"]
    # FA: a transferred-out LP's #N/A ID and file name do not count against the rule.
    for tab in facts["merge_tabs"]:
        assert tab["rows_missing_investor_id"] == []
        assert tab["file_names_not_starting_with_own_ids"] == []


@pytest.mark.parametrize("spec", _specs_for("CE-DATE-CONSISTENCY"), ids=lambda s: s.fixture_id)
def test_date_consistency_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-DATE-CONSISTENCY")
    facts = facts_for(manifest, "CE-DATE-CONSISTENCY")
    kinds = {ref["kind"] for ref in facts["references"]}
    assert {"event_label", "notice_date", "due_date"} <= kinds
    for ref in facts["references"]:
        assert ref["sheet"] in manifest.relevant_sheets and ref["cell"]
    assert facts["event_numbers"] == expected["distinct_event_numbers"]
    if spec.event_type != "distribution":
        assert (len(facts["fee_periods"]) > 1) == expected["fee_period_mismatch"]


@pytest.mark.parametrize("spec", _specs_for("CE-ALLOC-STALE-COMPONENTS"), ids=lambda s: s.fixture_id)
def test_stale_component_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-ALLOC-STALE-COMPONENTS")
    facts = facts_for(manifest, "CE-ALLOC-STALE-COMPONENTS")
    stale = [c for c in facts["inactive_components"] if c["nonzero_cells"]]
    assert len(stale) == expected["stale_columns"]
    # FA: a leftover label only matters on a column that is actually used.
    labelled = [c for c in facts["active_components"] if c["header_names_other_event"]]
    assert len(labelled) == expected["active_with_prior_label"]
    active = {c["header"] for c in facts["active_components"]}
    assert active and not active & {c["header"] for c in facts["inactive_components"]}


@pytest.mark.parametrize("spec", _specs_for("CE-DIST-CARRY-SPLIT"), ids=lambda s: s.fixture_id)
def test_carry_split_facts(facts_for, capital_event_fixtures, spec):
    if spec.event_type != "distribution":
        pytest.skip("carry only exists on distributions in the fixture set")
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-DIST-CARRY-SPLIT")
    facts = facts_for(manifest, "CE-DIST-CARRY-SPLIT")
    assert Decimal(str(facts["carried_interest_rate"])) == Decimal("0.2")
    matches = abs(Decimal(str(facts["gp_share"])) - Decimal(str(facts["expected_gp_share"]))) <= Decimal("0.005")
    assert matches == expected["gp_share_matches_rate"]


def test_facts_render_as_prompt_text(facts_for, capital_event_fixtures):
    facts_mod = importlib.import_module("src.pipeline.workbook.facts")
    manifest = capital_event_fixtures.get(defect="placeholder_left")
    text = facts_mod.render_facts("CE-WB-NO-PLACEHOLDERS", facts_for(manifest, "CE-WB-NO-PLACEHOLDERS"))
    assert "{Investor Short Name}" in text
    assert "COMPUTED FACTS" in text


@pytest.mark.parametrize("spec", _specs_for("CE-ALLOC-REFERENCE-INTEGRITY"), ids=lambda s: s.fixture_id)
def test_reference_integrity_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-ALLOC-REFERENCE-INTEGRITY")
    facts = facts_for(manifest, "CE-ALLOC-REFERENCE-INTEGRITY")
    for ref in facts["references"]:
        assert ref["target_sheet"] and ref["target_cell"] and ref["source_cells"]
        assert ref["kind"] in {"lookup", "roll_forward", "figure"}
        # A lookup spans a whole column; a figure or roll-forward link is one cell.
        assert (ref["kind"] == "lookup") == (not ref["target_cell"][-1].isdigit())
    tracker = [r for r in facts["references"] if r["target_sheet"] == "Portfolio Investment Tracker"]
    assert all(r["kind"] == "figure" for r in tracker)
    targets = {ref["target_sheet"] for ref in facts["references"]}
    if spec.event_type == "capital_call":
        assert {"Mgmt Fee Calc", "Portfolio Investment Tracker"} <= targets
    assert len(facts["period_mismatches"]) == expected["period_mismatches"]
    assert len(facts["link_pattern_exceptions"]) == expected.get("link_pattern_exceptions", 0)
