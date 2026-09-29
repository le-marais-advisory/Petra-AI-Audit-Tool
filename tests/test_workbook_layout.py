"""SheetLayout models and the deterministic layout validator (plan section 3, steps 3-4).

The LLM layout mapper's output is only trusted after ``validate_layout`` accepts it.
Golden layouts from the fixture generator must validate cleanly; each corruption must
be reported with a specific issue code so the mapper can be re-prompted with it.
"""
from __future__ import annotations

import copy
import importlib

import pytest

from tests.fixtures.generate_capital_event_fixtures import EVENT_TYPES, VARIANTS

CLEAN = [(e, v) for e in EVENT_TYPES for v in VARIANTS]
LAYOUT_ROLES = ("allocation", "itd", "summary", "merge", "mgmt_fee", "investor_data", "holiday_calendar")


@pytest.fixture(scope="module")
def layout_mod():
    return importlib.import_module("src.pipeline.workbook.layout")


@pytest.fixture(scope="module")
def validator():
    return importlib.import_module("src.pipeline.workbook.layout_validator")


@pytest.fixture(scope="module")
def loader():
    return importlib.import_module("src.pipeline.workbook.loader")


def _layout(manifest, role):
    return copy.deepcopy(next(lay for lay in manifest.layouts.values() if lay["role"] == role))


@pytest.mark.parametrize("event_type,variant", CLEAN)
def test_golden_layouts_parse(layout_mod, capital_event_fixtures, event_type, variant):
    manifest = capital_event_fixtures.get(event_type, variant)
    for sheet, raw in manifest.layouts.items():
        parsed = layout_mod.parse_layout(raw)
        assert parsed.role == raw["role"]
        assert parsed.sheet == sheet


@pytest.mark.parametrize("role", LAYOUT_ROLES)
def test_each_role_exposes_a_json_schema_for_structured_output(layout_mod, role):
    schema = layout_mod.layout_json_schema(role)
    assert schema["type"] == "object"
    assert "role" in schema["properties"]


def test_unknown_role_is_rejected(layout_mod):
    with pytest.raises(ValueError):
        layout_mod.parse_layout({"role": "balance_sheet", "sheet": "BS"})


@pytest.mark.parametrize("event_type,variant", CLEAN)
def test_golden_layouts_validate(layout_mod, validator, loader, capital_event_fixtures, event_type, variant):
    manifest = capital_event_fixtures.get(event_type, variant)
    model = loader.load_workbook_model(manifest.path)
    for raw in manifest.layouts.values():
        issues = validator.validate_layout(model, layout_mod.parse_layout(raw))
        assert issues == [], (raw["sheet"], [(i.code, i.message) for i in issues])


def _mutations():
    def header_off_by_one(lay):
        lay["header_row"] += 1

    def investor_range_short(lay):
        lay["vehicles"][0]["investor_rows"][1] -= 2

    def component_shifted(lay):
        lay["components"][0]["column"] = lay["columns"]["commitment"]

    def driver_below_header(lay):
        lay["fund_driver_row"] = lay["header_row"] + 1
        lay["vehicles"][0]["driver_row"] = lay["header_row"] + 1

    def column_out_of_range(lay):
        lay["roll_forward"]["remaining_commitment"] = "XFA"

    def subtotal_row_wrong(lay):
        lay["vehicles"][0]["subtotal_rows"]["total"] -= 1

    return {
        "header_off_by_one": ("allocation", header_off_by_one, "header_mismatch"),
        "investor_range_short": ("allocation", investor_range_short, "investor_range_mismatch"),
        "component_shifted": ("allocation", component_shifted, "header_mismatch"),
        "driver_below_header": ("allocation", driver_below_header, "driver_row_position"),
        "column_out_of_range": ("allocation", column_out_of_range, "column_out_of_range"),
        "subtotal_row_wrong": ("allocation", subtotal_row_wrong, "subtotal_row_mismatch"),
    }


def _itd_mutations():
    def block_overlap(lay):
        lay["event_blocks"][0]["last_column"] = lay["event_blocks"][1]["first_column"]

    def band_row_wrong(lay):
        lay["classification_rows"]["cost_contributions"] = lay["event_header_row"]

    def two_current_blocks(lay):
        lay["event_blocks"][0]["is_current"] = True

    def event_label_wrong(lay):
        lay["event_blocks"][-1]["first_column"] = lay["event_blocks"][-1]["components"][1]["column"]

    return {
        "block_overlap": ("itd", block_overlap, "block_overlap"),
        "band_row_wrong": ("itd", band_row_wrong, "classification_row_mismatch"),
        "two_current_blocks": ("itd", two_current_blocks, "current_block_count"),
        "event_label_wrong": ("itd", event_label_wrong, "header_mismatch"),
    }


def _other_mutations():
    def summary_check_empty(lay):
        lay["check_cells"] = ["Z99"]

    def merge_rows_short(lay):
        lay["last_data_row"] -= 3

    def fee_column_wrong(lay):
        lay["fee_columns"][0]["column"] = "B"

    return {
        "summary_check_empty": ("summary", summary_check_empty, "cell_empty"),
        "merge_rows_short": ("merge", merge_rows_short, "investor_range_mismatch"),
        "fee_column_wrong": ("mgmt_fee", fee_column_wrong, "header_mismatch"),
    }


ALL_MUTATIONS = {**_mutations(), **_itd_mutations(), **_other_mutations()}


@pytest.mark.parametrize("variant", ["standard", "shifted", "two_vehicles"])
@pytest.mark.parametrize("name", sorted(ALL_MUTATIONS))
def test_corrupted_layouts_are_rejected(layout_mod, validator, loader, capital_event_fixtures, name, variant):
    role, mutate, code = ALL_MUTATIONS[name]
    manifest = capital_event_fixtures.get("capital_call", variant)
    model = loader.load_workbook_model(manifest.path)
    raw = _layout(manifest, role)
    mutate(raw)
    issues = validator.validate_layout(model, layout_mod.parse_layout(raw))
    assert code in {i.code for i in issues}, [(i.code, i.message) for i in issues]


def test_issues_are_actionable(layout_mod, validator, loader, capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = loader.load_workbook_model(manifest.path)
    raw = _layout(manifest, "allocation")
    raw["header_row"] += 1
    issue = next(i for i in validator.validate_layout(model, layout_mod.parse_layout(raw)) if i.code == "header_mismatch")
    assert issue.sheet == "Allocation"
    assert issue.cell  # points at the cell that disagreed
    assert issue.message
