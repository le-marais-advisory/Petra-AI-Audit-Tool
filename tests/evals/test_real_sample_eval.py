"""Local-only eval against a real capital-event workbook.

Real client workbooks must never be committed. Point the eval at one with:

    CAPITAL_EVENT_SAMPLE_PATH=temp/capital-event-rules/sample-workbook.xlsx \\
        pytest tests/evals/test_real_sample_eval.py -m eval

Expected outcomes reflect the local reference sample (a capital call) under the
FA-calibrated rules. Its prior event's workbook is not available, so the cross-event
rules need review.

A second, multi-vehicle net-event sample with its prior workbook (a recycling fund with
hidden Merge tabs and a GP look-through block) runs with:

    CAPITAL_EVENT_NET_SAMPLE_PATH=".../BPCP IV - Capital Call #19 - 08.04.2026.xlsm" \\
    CAPITAL_EVENT_NET_PRIOR_PATH=".../BPCP IV - Distribution #8 - 12.11.2025 V4.xlsm" \\
        pytest tests/evals/test_real_sample_eval.py -m eval -k net
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.services.validation_service import ValidationService

pytestmark = pytest.mark.eval

SAMPLE = os.getenv("CAPITAL_EVENT_SAMPLE_PATH")
NET_SAMPLE = os.getenv("CAPITAL_EVENT_NET_SAMPLE_PATH")
NET_PRIOR = os.getenv("CAPITAL_EVENT_NET_PRIOR_PATH")

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
    "CE-XEV-ITD-ROLL-FORWARD": "needs_review",
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


# --- multi-vehicle net event with its prior workbook ---------------------------------------

NET_EXPECTED = {
    # Multi-vehicle scoping: four vehicle blocks, the fourth a GP look-through block, no fund driver row.
    "CE-ALLOC-VEHICLE-TIE": "pass",
    "CE-ALLOC-GROSS-TIE": "pass",
    "CE-ALLOC-COMMITMENTS": "pass",
    "CE-ALLOC-PER-LP-FORMULAS": "pass",
    # Investors keyed by (vehicle, name): one trust sits in two vehicles.
    "CE-XEV-ROLL-FORWARD": "pass",
    "CE-TIE-ITD-COMMITMENTS": "pass",
    # One ITD "Investment" column sums four Allocation investment columns.
    "CE-TIE-ITD-ALLOCATION": "pass",
    # Five fee quarters pulled from two fee tabs per vehicle ("Q3 2025 - Q3 2026 Mgmt Fees").
    "CE-TIE-MGMT-FEE": "pass",
    # Combined labels ("Capital Call #9 & Distribution #1") advance both counters.
    "CE-ITD-EVENT-SEQUENCE": "pass",
    "CE-DATE-VALIDITY": "pass",
    "CE-DATE-ORDER": "pass",
    # Recycling fund: the recallable column is a cap formula and LPs have called above commitment.
    "CE-ITD-CUMULATIVE": "needs_review",
    "CE-XEV-ITD-ROLL-FORWARD": "needs_review",
    "CE-RF-FOOTING": "needs_review",
    # Real findings in the sample.
    "CE-XEV-HISTORY-UNCHANGED": "fail",  # one Executive Fund investor's Capital Call #1 split edited by a penny
    "CE-TIE-MERGE": "fail",  # the hidden Merge tabs omit three of the four investment columns
    "CE-SUM-CHECKS-ZERO": "fail",  # the Merge tabs' own check cells show the omission
    "CE-ALLOC-REFOOT": "fail",  # a hidden column's LP subtotal sums 6 of 25 rows
    "CE-ALLOC-PLUG-DISCIPLINE": "fail",  # a plug on the GP row and plugs off the largest LP
    "CE-FMT-NO-FORMULA-ERRORS": "fail",  # #REF! on a Merge tab, #N/A / #DIV/0! on notice templates
    "CE-WB-NO-HIDDEN-DATA": "fail",  # hidden populated Allocation columns
    "CE-WB-PAGE-BREAK-VIEW": "fail",
    "CE-ID-INVESTOR-KEYS": "fail",  # the Merge "Investor ID" column is a row counter
    # Client-named file: reviewed, not failed.
    "CE-WB-FILE-NAMING": "needs_review",
    "CE-ALLOC-PRO-RATA-PARITY": "needs_review",  # distributions allocated on commitment %, no basis column
}


@pytest.fixture(scope="module")
def net_result():
    if not NET_SAMPLE or not NET_PRIOR or not Path(NET_SAMPLE).exists() or not Path(NET_PRIOR).exists():
        pytest.skip("set CAPITAL_EVENT_NET_SAMPLE_PATH and CAPITAL_EVENT_NET_PRIOR_PATH to the local net-event pair")
    return ValidationService().validate_document(
        file_path=NET_SAMPLE,
        source_filename=Path(NET_SAMPLE).name,
        document_type="capital_event_workbook",
        options={"event_type": "net_event"},
        prior_file_path=NET_PRIOR,
        prior_source_filename=Path(NET_PRIOR).name,
    )


def _assessment(result, rule_id):
    return next(a for a in result["analysis"]["rule_assessments"] if a["rule_id"] == rule_id)


def test_net_hidden_merge_tabs_are_processed(net_result):
    processed = {page["label"] for page in net_result["pages"] if page["page_type"] != ["reference"]}
    assert {"BPCP IV, L.P. Merge", "BPCP IV (A), L.P. Merge", "BPCP IV Executive Merge", "BPCP IV Management"} <= processed


@pytest.mark.parametrize("rule_id", sorted(NET_EXPECTED))
def test_net_known_outcomes(net_result, rule_id):
    assessment = _assessment(net_result, rule_id)
    assert assessment["verdict"] == NET_EXPECTED[rule_id], (assessment.get("summary"), assessment.get("findings"))


def test_net_history_finding_names_the_edited_investor_only(net_result):
    findings = _assessment(net_result, "CE-XEV-HISTORY-UNCHANGED")["findings"]
    assert findings and all("Chaikin" in f for f in findings)
    assert not any("Sustar" in f for f in findings)


def test_net_merge_finding_names_the_missing_columns(net_result):
    findings = _assessment(net_result, "CE-TIE-MERGE")["findings"]
    assert any("Water Lilies" in f and "VRC" in f and "TAS" in f for f in findings)
