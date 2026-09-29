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
        return facts_mod.build_facts(rule_id, model, data, options={"event_type": manifest.spec.event_type})

    return run


def _specs_for(rule_id):
    specs = [FixtureSpec(e) for e in EVENT_TYPES]
    specs += [FixtureSpec(d.event_types[0], "standard", d.name) for d in DEFECTS.values() if rule_id in d.facts]
    return specs


def _expectation(capital_event_fixtures, spec, rule_id):
    manifest = capital_event_fixtures.get(spec.event_type, spec.variant, spec.defect)
    expected = manifest.facts.get(rule_id)
    if expected is None:
        pytest.skip(f"{rule_id} has no facts expectation for {spec.fixture_id}")
    if spec.defect and DEFECTS[spec.defect].pending_calibration:
        pytest.skip(f"awaiting FA calibration item {DEFECTS[spec.defect].pending_calibration}")
    return manifest, expected


@pytest.mark.parametrize("spec", _specs_for("CE-WB-NO-PLACEHOLDERS"), ids=lambda s: s.fixture_id)
def test_placeholder_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-WB-NO-PLACEHOLDERS")
    facts = facts_for(manifest, "CE-WB-NO-PLACEHOLDERS")
    assert len(facts["placeholders"]) == expected["placeholder_count"]
    for hit in facts["placeholders"]:
        assert hit["sheet"] in manifest.relevant_sheets and hit["cell"] and hit["text"]


@pytest.mark.parametrize("spec", _specs_for("CE-ITD-EVENT-BLOCK"), ids=lambda s: s.fixture_id)
def test_itd_event_block_facts(facts_for, capital_event_fixtures, spec):
    manifest, expected = _expectation(capital_event_fixtures, spec, "CE-ITD-EVENT-BLOCK")
    facts = facts_for(manifest, "CE-ITD-EVENT-BLOCK")
    block = facts["current_block"]
    assert block["label"] == manifest.truth["itd"]["current_block_label"]
    unclassified = [c for c in block["columns"] if not c["classifications"]]
    assert len(unclassified) == expected["unclassified_current_columns"]
    for column in block["columns"]:
        assert column["component_type"]
    assert facts["label_is_unique"] is True
    assert facts["appended_after_prior_blocks"] is True


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
