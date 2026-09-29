"""Hybrid-rule verdict eval: full WorkbookPipeline with live LLM calls."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from src.services.validation_service import ValidationService

pytestmark = pytest.mark.eval

CASES = yaml.safe_load((Path(__file__).parent / "capital_event_cases.yaml").read_text(encoding="utf-8"))["cases"]
PARAMS = [(case, rule_id, expected) for case in CASES for rule_id, expected in case["expected"].items()]

_results: dict[str, dict] = {}


def _run(capital_event_fixtures, case) -> dict:
    if case["id"] not in _results:
        fx = case["fixture"]
        manifest = capital_event_fixtures.get(fx["event_type"], fx.get("variant", "standard"), fx.get("defect"))
        result = ValidationService().validate_document(
            file_path=str(manifest.path),
            source_filename=manifest.path.name,
            document_type="capital_event_workbook",
            options={"event_type": fx["event_type"]},
        )
        _results[case["id"]] = {a["rule_id"]: a for a in result["analysis"]["rule_assessments"]}
    return _results[case["id"]]


@pytest.mark.parametrize("case,rule_id,expected", PARAMS, ids=[f"{c['id']}/{r}" for c, r, _ in PARAMS])
def test_hybrid_verdict(capital_event_fixtures, case, rule_id, expected):
    if isinstance(expected, dict) and "pending" in expected:
        pytest.skip(f"awaiting FA calibration item {expected['pending']}")
    assessment = _run(capital_event_fixtures, case)[rule_id]
    assert assessment["verdict"] == expected, assessment.get("summary")
