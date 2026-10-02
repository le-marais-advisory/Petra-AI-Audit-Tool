"""Label and header parsing shared by the workbook checks and facts."""
from __future__ import annotations

import pytest

from src.pipeline.workbook.checks._common import event_number, event_numbers, quarter_key, quarter_range


@pytest.mark.parametrize("label,expected", [
    ("Capital Call #9 & Distribution #1- 7/12/2021", {"call": 9, "distribution": 1}),
    ("Capital Call #16 and Distribution #5 - 7/11/2024", {"call": 16, "distribution": 5}),
    ("Net Capital Call #19 - 7/21/26", {"call": 19}),
    ("Distribution #8 - 12/11/2025", {"distribution": 8}),
    ("Distribution #1 & Capital Call #9", {"distribution": 1, "call": 9}),
    ("Transfer - 10/1/2018", {}),
])
def test_event_numbers_per_family(label, expected):
    assert event_numbers(label) == expected


def test_event_number_is_the_first_number():
    assert event_number("Capital Call #9 & Distribution #1") == 9
    assert event_number("no number") is None


@pytest.mark.parametrize("header,expected", [
    ("Q3 2025 - Q3 2026 Mgmt Fees ", ["Q3-2025", "Q4-2025", "Q1-2026", "Q2-2026", "Q3-2026"]),
    ("Q2 2026 - Q3 2026 Mgmt Fees", ["Q2-2026", "Q3-2026"]),
    ("3Q26 Mgmt Fees", ["Q3-2026"]),
    ("Q3'25 to Q1'26", ["Q3-2025", "Q4-2025", "Q1-2026"]),
    ("Mgmt Fees", []),
])
def test_quarter_range_expands_a_header(header, expected):
    assert quarter_range(header) == expected


def test_quarter_key_is_the_first_quarter():
    assert quarter_key("Q3 2025 - Q3 2026 Mgmt Fees") == "Q3-2025"
