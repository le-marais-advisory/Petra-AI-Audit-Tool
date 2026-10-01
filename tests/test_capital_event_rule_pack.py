"""Capital-event rule pack: new rule fields and filtering by document / event type.

The pack lives under rules/capital_event/ and is written in Phase 1 from the
FA-calibrated drafts. Deferred rules (CE-REF-*, CE-FMT-VISUAL-INTEGRITY) are not part
of the v1 pack.
"""
from __future__ import annotations

import pytest

from src.schemas.rule import RuleSchema
from src.services.rule_service import RuleService
from tests.fixtures.generate_capital_event_fixtures import (
    DETERMINISTIC_RULE_IDS,
    EVENT_TYPES,
    HYBRID_RULE_IDS,
    ROLES,
)

DOC_TYPE = "capital_event_workbook"
V1_IDS = set(DETERMINISTIC_RULE_IDS) | set(HYBRID_RULE_IDS)
# Deferred: the vision rule, and rules that need the fund terms (FA calibration).
DEFERRED = {"CE-FMT-VISUAL-INTEGRITY", "CE-ALLOC-FEE-TIERS", "CE-ALLOC-COMPONENT-PARTICIPATION"}

# Rules that only make sense for some event types.
EXCLUDED_BY_EVENT = {
    "capital_call": {"CE-DIST-ROC-LIMIT", "CE-DIST-CARRY-SPLIT", "CE-NET-EVENT-STRUCTURE"},
    "distribution": {"CE-NET-EVENT-STRUCTURE", "CE-TIE-MGMT-FEE"},
    "net_event": set(),
}


@pytest.fixture(scope="module")
def pack() -> list[dict]:
    return RuleService().load_rules(document_type=DOC_TYPE)


def test_pack_contains_exactly_the_v1_rules(pack):
    ids = {r["id"] for r in pack}
    assert ids == V1_IDS
    assert not any(i.startswith("CE-REF-") for i in ids)
    assert not ids & DEFERRED


def test_every_rule_declares_the_new_fields(pack):
    for rule in pack:
        parsed = RuleSchema(**rule)
        assert parsed.document_types == [DOC_TYPE], rule["id"]
        assert parsed.required_roles and set(parsed.required_roles) <= set(ROLES), rule["id"]
        assert parsed.evaluator in ("deterministic", "hybrid"), rule["id"]


def test_evaluator_split_matches_the_plan(pack):
    by_id = {r["id"]: r for r in pack}
    assert {i for i, r in by_id.items() if r["evaluator"] == "deterministic"} == set(DETERMINISTIC_RULE_IDS)
    assert {i for i, r in by_id.items() if r["evaluator"] == "hybrid"} == set(HYBRID_RULE_IDS)


def test_only_llm_evaluated_rules_carry_a_query(pack):
    # Deterministic checks are implemented in code; a query they never read would mislead developers.
    for rule in pack:
        if rule["evaluator"] == "deterministic":
            assert "query" not in rule, rule["id"]
        else:
            assert rule.get("query"), rule["id"]


def test_rule_sections_resolve_to_roles(pack):
    # Sheet-scoped rules route by role, not by "applies ONLY to the Allocation sheet" preambles.
    by_id = {r["id"]: r for r in pack}
    assert by_id["CE-ALLOC-VEHICLE-TIE"]["required_roles"] == ["allocation"]
    assert set(by_id["CE-TIE-ITD-ALLOCATION"]["required_roles"]) == {"allocation", "itd"}
    assert set(by_id["CE-ID-INVESTOR-KEYS"]["required_roles"]) >= {"allocation", "merge", "investor_data"}


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_event_type_filter(event_type):
    ids = {r["id"] for r in RuleService().load_rules(document_type=DOC_TYPE, event_type=event_type)}
    assert ids == V1_IDS - EXCLUDED_BY_EVENT[event_type]


def test_default_load_is_unchanged():
    ids = {r["id"] for r in RuleService().load_rules()}
    assert ids and not any(i.startswith("CE-") for i in ids)
    for rule in RuleService().load_rules():
        assert RuleSchema(**rule).document_types == ["financial_statements"]


def test_rule_schema_query_follows_the_evaluator():
    assert RuleSchema(id="CE-ALLOC-REFOOT", name="x", evaluator="deterministic").query is None
    with pytest.raises(ValueError, match="query"):
        RuleSchema(id="CE-ALLOC-REFOOT", name="x", evaluator="deterministic", query="ignored")
    with pytest.raises(ValueError, match="query"):
        RuleSchema(id="NUM-CROSSFOOT", name="x")
    with pytest.raises(ValueError, match="query"):
        RuleSchema(id="CE-TIE-SUMMARY", name="x", evaluator="hybrid", query="  ")


@pytest.mark.parametrize(
    "rule",
    [
        {"id": "CE-ALLOC-REFOOT", "name": "x", "query": "q", "evaluator": "deterministic",
         "document_types": ["capital_event_workbook"]},
        {"id": "CE-TIE-SUMMARY", "name": "x", "evaluator": "hybrid", "document_types": ["capital_event_workbook"]},
        {"id": "NUM-CROSSFOOT", "name": "x"},
    ],
    ids=["deterministic-with-query", "hybrid-without-query", "llm-without-query"],
)
def test_load_rules_rejects_a_query_that_does_not_match_the_evaluator(rule):
    import json

    with pytest.raises(ValueError, match="query"):
        RuleService().load_rules(rules_json_str=json.dumps({"rules": [rule]}), document_type="capital_event_workbook")


def test_rule_schema_defaults_keep_existing_rules_valid():
    parsed = RuleSchema(id="NUM-CROSSFOOT", name="x", query="q")
    assert parsed.document_types == ["financial_statements"]
    assert parsed.event_types is None
    assert parsed.evaluator == "llm"
    assert parsed.required_roles is None
    assert parsed.requires_documents is None


def test_custom_payload_is_still_validated_and_filtered():
    payload = '{"rules": [{"id": "CE-ALLOC-REFOOT", "name": "x", "document_types": ["capital_event_workbook"], "evaluator": "deterministic", "required_roles": ["allocation"]}, {"id": "NUM-CROSSFOOT", "name": "y", "query": "q"}]}'
    ids = [r["id"] for r in RuleService().load_rules(rules_json_str=payload, document_type=DOC_TYPE)]
    assert ids == ["CE-ALLOC-REFOOT"]


def test_deferred_fund_terms_rules_are_parked_with_their_requirement():
    import json
    from pathlib import Path

    deferred = {r["id"]: r for r in json.loads(Path("rules/capital_event/deferred/deferred_rules.json").read_text())["rules"]}
    for rule_id in ("CE-ALLOC-FEE-TIERS", "CE-ALLOC-COMPONENT-PARTICIPATION"):
        assert "fund_terms" in deferred[rule_id]["requires_documents"]
