"""Unit tests for the layout-mapping eval scorer (runs offline)."""
from __future__ import annotations

import copy

import pytest

from src.evaluation.layout_scoring import ANCHORS, score_layout


@pytest.mark.parametrize("variant", ["standard", "shifted", "two_vehicles"])
def test_golden_scores_perfectly(capital_event_fixtures, variant):
    manifest = capital_event_fixtures.get("capital_call", variant)
    for layout in manifest.layouts.values():
        score = score_layout(layout, layout)
        assert score.anchor_accuracy == 1.0 and score.field_accuracy == 1.0, layout["sheet"]


def test_every_role_has_anchors(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    assert {lay["role"] for lay in manifest.layouts.values()} <= set(ANCHORS)


def test_anchor_errors_are_reported(capital_event_fixtures):
    golden = next(lay for lay in capital_event_fixtures.get().layouts.values() if lay["role"] == "allocation")
    predicted = copy.deepcopy(golden)
    predicted["header_row"] += 1
    predicted["components"][0]["component_type"] = "org_expense"
    score = score_layout(predicted, golden)
    assert score.anchor_accuracy < 1.0
    assert "header_row" in score.mismatches
    assert "components[].column+component_type+side" in score.mismatches


def test_component_order_does_not_matter(capital_event_fixtures):
    golden = next(lay for lay in capital_event_fixtures.get().layouts.values() if lay["role"] == "allocation")
    predicted = copy.deepcopy(golden)
    predicted["components"].reverse()
    assert score_layout(predicted, golden).anchor_accuracy == 1.0


def test_non_anchor_differences_only_lower_field_accuracy(capital_event_fixtures):
    golden = next(lay for lay in capital_event_fixtures.get().layouts.values() if lay["role"] == "allocation")
    predicted = copy.deepcopy(golden)
    predicted["columns"]["received"] = "A"
    score = score_layout(predicted, golden)
    assert score.anchor_accuracy == 1.0
    assert score.field_accuracy < 1.0
