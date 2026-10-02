"""Local-only eval against a real capital-event workbook.

Real client workbooks must never be committed. Point the eval at one with:

    CAPITAL_EVENT_SAMPLE_PATH=temp/capital-event-rules/sample-workbook.xlsx \\
        pytest tests/evals/test_real_sample_eval.py -m eval

Expected outcomes reflect the local reference sample (a capital call) under the
FA-calibrated rules. Its prior event's workbook is not available, so the cross-event
rules need review.
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
# Not needed by a capital call, but the Allocation's investment driver pulls the current deal from it.
EXPECTED_REFERENCED = {"Portfolio Investment Tracker"}
NEVER_PROCESSED = {
    "PETRA Launch Page", "Org Chart", "Capital Contributions", "4th Close Rebalance", "Robert Harris Rebalance",
    "Late Interest", "Late Interest Allocations", "Partnership & Org Expense", "Support and Notes -->",
}
EXPECTED = {
    # Ties, refoot, roll-forward, ITD and dates hold.
    "CE-ALLOC-VEHICLE-TIE": "pass",
    "CE-ALLOC-GROSS-TIE": "pass",
    "CE-ALLOC-COMMITMENTS": "pass",
    "CE-RF-FOOTING": "pass",
    "CE-ITD-CUMULATIVE": "pass",
    "CE-TIE-ITD-ALLOCATION": "pass",
    "CE-TIE-MGMT-FEE": "pass",
    "CE-TIE-SUPPORT-TABS": "pass",  # 3Q and 4Q fees tie to Mgmt Fee Calc investor by investor and in total
    "CE-SUM-CHECKS-ZERO": "pass",
    "CE-DATE-VALIDITY": "pass",
    "CE-DATE-ORDER": "pass",
    # Patterns the FAs confirmed as legitimate.
    "CE-ALLOC-PLUG-DISCIPLINE": "pass",  # residual spread over the tied largest LPs
    "CE-ALLOC-ROUNDING": "pass",  # fees in whole dollars, investment in cents
    "CE-ITD-EVENT-SEQUENCE": "pass",  # transfers skipped, net call continues the call counter
    "CE-ITD-EVENT-BLOCK": "pass",  # overlay rows mark fee columns twice
    "CE-FMT-NO-FORMULA-ERRORS": "pass",  # #N/A only on a transferred-out investor's row
    "CE-ID-INVESTOR-KEYS": "pass",  # IDs on the Merge tab only; names match elsewhere
    "CE-WB-NO-PLACEHOLDERS": "pass",  # 'TBD' wire date pending cash movement
    "CE-WB-MERGE-TABS": "pass",  # DX IDs are the Fund / Investor IDs
    # Real findings: the 4Q fee driver (T5) links to the 3Q total, and the GP row's fee lookups
    # (S107, T107) read the column to the left. The values coincide, so only the links are wrong.
    "CE-ALLOC-REFERENCE-INTEGRITY": "fail",
    # A warning, not a failure: live links in prior ITD blocks on the $0 GP row.
    "CE-ITD-PRIOR-FROZEN": "needs_review",
    # Confirmed findings in the sample.
    "CE-WB-PAGE-BREAK-VIEW": "fail",
    "CE-WB-FILE-NAMING": "fail",
    "CE-WB-NO-HIDDEN-DATA": "fail",  # hidden Allocation columns hold investor values
    "CE-FMT-ACCOUNTING": "fail",  # subtotal rows use a 0-decimal format in 2-decimal columns
    # No prior event's workbook for the sample.
    "CE-XEV-HISTORY-UNCHANGED": "needs_review",
    "CE-XEV-ROLL-FORWARD": "needs_review",
    "CE-XEV-PLUG-CONSISTENCY": "needs_review",
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


def test_only_relevant_and_referenced_sheets_are_processed(result):
    processed = {page["label"] for page in result["pages"] if page["page_type"] != ["reference"]}
    referenced = {page["label"] for page in result["pages"] if page["page_type"] == ["reference"]}
    assert processed == EXPECTED_PROCESSED
    assert referenced == EXPECTED_REFERENCED
    assert not (processed | referenced) & NEVER_PROCESSED


@pytest.mark.parametrize("rule_id", sorted(EXPECTED))
def test_known_outcomes(result, rule_id):
    assessment = next(a for a in result["analysis"]["rule_assessments"] if a["rule_id"] == rule_id)
    assert assessment["verdict"] == EXPECTED[rule_id], assessment.get("summary")
