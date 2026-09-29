"""Local-only eval against a real capital-event workbook.

Real client workbooks must never be committed. Point the eval at one with:

    CAPITAL_EVENT_SAMPLE_PATH=temp/capital-event-rules/sample-workbook.xlsx \\
        pytest tests/evals/test_real_sample_eval.py -m eval

Expected outcomes below were established by prototyping against the reference sample
(Sallyport, Capital Call #10). Rules whose outcome depends on an open FA calibration
item are listed in PENDING and only reported.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.services.validation_service import ValidationService

pytestmark = pytest.mark.eval

SAMPLE = os.getenv("CAPITAL_EVENT_SAMPLE_PATH")

EXPECTED_PROCESSED = {
    "Summary", "Allocation", "ITD Capital Activity", "Sallyport Partners Fund", "DX Investor Data",
    "Mgmt Fee Calc", "JPM Bank Holidays",
}
NEVER_PROCESSED = {
    "PETRA Launch Page", "Org Chart", "Capital Contributions", "4th Close Rebalance", "Robert Harris Rebalance",
    "Late Interest", "Late Interest Allocations", "Partnership & Org Expense", "Support and Notes -->",
    "Portfolio Investment Tracker",
}
EXPECTED = {
    "CE-ALLOC-VEHICLE-TIE": "pass",
    "CE-ALLOC-GROSS-TIE": "pass",
    "CE-ALLOC-COMMITMENTS": "pass",
    "CE-RF-FOOTING": "pass",
    "CE-ITD-CUMULATIVE": "pass",
    "CE-TIE-ITD-ALLOCATION": "pass",
    "CE-SUM-CHECKS-ZERO": "pass",
    "CE-DATE-VALIDITY": "pass",
    "CE-DATE-ORDER": "pass",
    # Allocation has no Investor ID column, so identity is name-only -> needs_review by the rule text.
    "CE-ID-INVESTOR-KEYS": "needs_review",
}
PENDING = {
    "CE-ALLOC-PLUG-DISCIPLINE": 1,
    "CE-ALLOC-ROUNDING": 2,
    "CE-ITD-EVENT-BLOCK": 3,
    "CE-ITD-EVENT-SEQUENCE": 4,
    "CE-WB-MERGE-TABS": 6,
    "CE-WB-FILE-NAMING": 7,
    "CE-WB-PAGE-BREAK-VIEW": 8,
    "CE-ALLOC-FEE-TIERS": 9,
    "CE-ITD-PRIOR-FROZEN": 10,
    "CE-FMT-NO-FORMULA-ERRORS": 11,
    "CE-WB-NO-PLACEHOLDERS": 12,
}


@pytest.fixture(scope="module")
def result():
    if not SAMPLE or not Path(SAMPLE).exists():
        pytest.skip("set CAPITAL_EVENT_SAMPLE_PATH to a local capital-event workbook")
    return ValidationService().validate_document(
        file_path=SAMPLE,
        source_filename=Path(SAMPLE).name,
        document_type="capital_event_workbook",
        options={"event_type": "capital_call"},
    )


def test_only_relevant_sheets_are_processed(result):
    processed = {page["label"] for page in result["pages"]}
    assert processed == EXPECTED_PROCESSED
    assert not processed & NEVER_PROCESSED


@pytest.mark.parametrize("rule_id", sorted(EXPECTED))
def test_known_outcomes(result, rule_id):
    assessment = next(a for a in result["analysis"]["rule_assessments"] if a["rule_id"] == rule_id)
    assert assessment["verdict"] == EXPECTED[rule_id], assessment.get("summary")


@pytest.mark.parametrize("rule_id", sorted(PENDING))
def test_pending_outcomes_are_reported(result, rule_id, record_property):
    assessment = next(a for a in result["analysis"]["rule_assessments"] if a["rule_id"] == rule_id)
    record_property(rule_id, assessment["verdict"])
    pytest.skip(f"awaiting FA calibration item {PENDING[rule_id]}; got {assessment['verdict']}")
