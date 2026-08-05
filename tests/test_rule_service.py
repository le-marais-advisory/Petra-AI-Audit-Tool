from __future__ import annotations

import json

import pytest

from src.services.rule_service import RuleService, _validate_rule_ids


def test_default_rules_load_and_are_valid():
    rules = RuleService().load_rules()
    assert rules, "expected the bundled rule files to load"
    ids = [r["id"] for r in rules]
    assert len(ids) == len(set(ids)), "bundled rule ids must be unique"


def test_validate_accepts_canonical_ids():
    # Includes the '=' / '+' separators used by ARITH-ASSETS=LIABS+CAP.
    _validate_rule_ids([{"id": "NUM-CROSSFOOT"}, {"id": "ARITH-ASSETS=LIABS+CAP"}])


@pytest.mark.parametrize(
    "bad_id",
    ["FMT-headings", "", "NUM CROSSFOOT", None, "num-crossfoot"],
)
def test_validate_rejects_malformed_ids(bad_id):
    with pytest.raises(ValueError):
        _validate_rule_ids([{"id": bad_id}])


def test_validate_rejects_duplicate_ids():
    with pytest.raises(ValueError, match="duplicate"):
        _validate_rule_ids([{"id": "NUM-REFOOT"}, {"id": "NUM-REFOOT"}])


def test_load_rules_raises_on_malformed_custom_payload():
    payload = json.dumps({"rules": [{"id": "FMT-headings", "name": "x", "query": "q"}]})
    with pytest.raises(ValueError):
        RuleService().load_rules(rules_json_str=payload)
