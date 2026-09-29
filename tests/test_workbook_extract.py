"""Typed extraction from a validated layout (plan section 3, step 5).

Given the golden layouts, ``extract_workbook_data`` must read back exactly the
numbers the generator put in the workbook.
"""
from __future__ import annotations

import datetime as dt
import importlib
from decimal import Decimal

import pytest

from tests.fixtures.generate_capital_event_fixtures import EVENT_TYPES, VARIANTS

CLEAN = [(e, v) for e in EVENT_TYPES for v in VARIANTS]
TOL = Decimal("0.005")


@pytest.fixture(scope="module")
def extract():
    loader = importlib.import_module("src.pipeline.workbook.loader")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    extract_mod = importlib.import_module("src.pipeline.workbook.extract")

    def run(manifest):
        model = loader.load_workbook_model(manifest.path)
        layouts = {name: layout.parse_layout(raw) for name, raw in manifest.layouts.items()}
        return extract_mod.extract_workbook_data(model, layouts)

    return run


def _d(value) -> Decimal:
    return Decimal(str(value))


@pytest.mark.parametrize("event_type,variant", CLEAN)
def test_allocation_investors(extract, capital_event_fixtures, event_type, variant):
    manifest = capital_event_fixtures.get(event_type, variant)
    data = extract(manifest)
    truth = manifest.truth["allocation"]["investors"]
    investors = {inv.name: inv for v in data.allocation.vehicles for inv in v.investors}
    assert set(investors) == set(truth)
    for name, expected in truth.items():
        inv = investors[name]
        assert inv.row == expected["row"]
        assert inv.is_gp == expected["is_gp"]
        assert _d(inv.commitment) == _d(expected["commitment"])
        for col, amount in expected["amounts"].items():
            assert abs(_d(inv.amounts[col]) - _d(amount)) <= TOL, (name, col)
        assert abs(abs(_d(inv.roll_forward["prior_contributions"])) - _d(expected["prior_contributions"])) <= TOL
        assert abs(_d(inv.roll_forward["remaining_commitment"]) - _d(expected["remaining_commitment"])) <= TOL
        assert inv.formulas  # stored formulas travel with the values


@pytest.mark.parametrize("event_type,variant", CLEAN)
def test_allocation_event_and_drivers(extract, capital_event_fixtures, event_type, variant):
    manifest = capital_event_fixtures.get(event_type, variant)
    alloc = extract(manifest).allocation
    truth = manifest.truth["allocation"]
    assert alloc.event.event_type == event_type
    assert alloc.event.notice_date == dt.date.fromisoformat(truth["notice_date"])
    assert alloc.event.due_date == dt.date.fromisoformat(truth["due_date"])
    for col, amount in truth["drivers"].items():
        assert abs(_d(alloc.fund_drivers[col]) - _d(amount)) <= TOL, col
    assert abs(_d(alloc.event_gross) - _d(truth["event_gross"])) <= TOL
    assert len(alloc.vehicles) == (2 if variant == "two_vehicles" else 1)


@pytest.mark.parametrize("event_type,variant", CLEAN)
def test_itd_blocks_and_cumulatives(extract, capital_event_fixtures, event_type, variant):
    manifest = capital_event_fixtures.get(event_type, variant)
    itd = extract(manifest).itd
    truth = manifest.truth["itd"]
    assert [b.label for b in itd.event_blocks] == truth["event_labels"]
    current = itd.current_block
    assert current.label == truth["current_block_label"]
    investors = {inv.name: inv for v in itd.vehicles for inv in v.investors}
    for name, cols in truth["current_block"].items():
        for col, amount in cols.items():
            assert abs(_d(investors[name].values[col]) - _d(amount)) <= TOL, (name, col)
    for name, cum in truth["cumulative"].items():
        for key in ("total_contributions", "unfunded", "total_distributions"):
            assert abs(_d(investors[name].cumulative[key]) - _d(cum[key])) <= TOL, (name, key)


def test_summary_merge_fee_and_holidays(extract, capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    data = extract(manifest)
    assert abs(_d(data.summary.event_total) - _d(manifest.truth["summary"]["event_total"])) <= TOL
    assert [_d(v) for v in data.summary.check_values] == [Decimal("0")]
    merge = data.merges[0]
    assert merge.vehicle == "Main Fund"
    assert all(row.investor_id and row.fund_id and row.file_name for row in merge.rows)
    assert abs(_d(data.mgmt_fee.total) - _d(manifest.truth["mgmt_fee"]["total"])) <= TOL
    assert data.mgmt_fee.period_labels == [manifest.truth["mgmt_fee"]["period"]]
    assert dt.date(2026, 5, 25) in data.holidays
    assert len(data.investor_data.rows) == len(data.investor_data.by_investor_id)


def test_extraction_without_a_role_leaves_it_empty(extract, capital_event_fixtures):
    # Distribution events do not process the fee tab.
    manifest = capital_event_fixtures.get("distribution")
    loader = importlib.import_module("src.pipeline.workbook.loader")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    extract_mod = importlib.import_module("src.pipeline.workbook.extract")
    model = loader.load_workbook_model(manifest.path)
    layouts = {n: layout.parse_layout(r) for n, r in manifest.layouts.items() if n in manifest.relevant_sheets}
    data = extract_mod.extract_workbook_data(model, layouts)
    assert data.mgmt_fee is None
    assert data.allocation is not None
