"""Formula parsing behind CE-TIE-SUPPORT-TABS (FA item 15: support tabs tie investor by investor)."""
from __future__ import annotations

import pytest

from src.pipeline.workbook.checks.support import parse_lookup


@pytest.mark.parametrize("formula,expected", [
    # The reference sample's fee pull, with a sheet-qualified criterion.
    ("=SUMIFS('Mgmt Fee Calc'!$F$9:$F$105,'Mgmt Fee Calc'!$B$9:$B$105,Allocation!$C10)",
     ("Mgmt Fee Calc", "F", (9, 105), (("B", "row", "C"),), False)),
    ("=SUMIFS('Distribution Waterfall'!$E:$E,'Distribution Waterfall'!$B:$B,$C10)",
     ("Distribution Waterfall", "E", None, (("B", "row", "C"),), False)),
    ("=SUMIFS(Fees!$F:$F,Fees!$B:$B,$C10,Fees!$C:$C,\"Main Fund\")",
     ("Fees", "F", None, (("B", "row", "C"), ("C", "text", "Main Fund")), False)),
    ("=SUMIF('Mgmt Fee Calc'!$B:$B,Allocation!$C10,'Mgmt Fee Calc'!E:E)",
     ("Mgmt Fee Calc", "E", None, (("B", "row", "C"),), False)),
    ("=-INDEX(Expenses!$G:$G,MATCH($D10,Expenses!$A:$A,0))",
     ("Expenses", "G", None, (("A", "row", "D"),), True)),
    ("=ROUND(VLOOKUP($C10,Breakout!$B$5:$H$60,4,FALSE),2)",
     ("Breakout", "E", (5, 60), (("B", "row", "C"),), True)),
    # A criterion fixed to one Allocation cell rather than the investor's row.
    ("=SUMIFS(Fees!$F:$F,Fees!$B:$B,$C$10)", ("Fees", "F", None, (("B", "cell", "C10"),), False)),
])
def test_lookup_shapes(formula, expected):
    lookup = parse_lookup(formula, 10, "Allocation")
    assert (lookup.sheet, lookup.value_column, lookup.rows, lookup.keys, lookup.first_match) == expected


@pytest.mark.parametrize("formula", [
    None, "", "=ROUND(S$5*$E10,2)", "='Mgmt Fee Calc'!$F$113",
    "=SUMIFS(Allocation!$F:$F,Allocation!$B:$B,$C10)",  # not a support tab (same sheet)
    "=SUMIFS('Fees'!$F:$F,'Fees'!$B:$B,ITD!$C10)",  # criterion on another sheet
])
def test_non_lookups_are_ignored(formula):
    lookup = parse_lookup(formula, 10, "Allocation")
    assert lookup is None or lookup.sheet == "Allocation"
