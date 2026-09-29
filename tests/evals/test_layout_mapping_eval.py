"""Layout-mapping eval: the LLM mapper must reproduce every golden anchor.

For each layout variant the relevant sheets are mapped with the configured text
provider and scored against the generator's golden layout. Anchors must be 100%
correct (after the validator's one re-prompt); field accuracy is reported.
"""
from __future__ import annotations

import importlib

import pytest

from tests.evals.layout_scoring import score_layout
from tests.fixtures.generate_capital_event_fixtures import EVENT_TYPES, VARIANTS

pytestmark = pytest.mark.eval

CASES = [(e, v) for e in EVENT_TYPES for v in VARIANTS] + [("capital_call", "standard", "probe_itd_overlay_rows")]


@pytest.mark.parametrize("case", CASES, ids=lambda c: "__".join(c))
def test_layout_mapping_matches_golden(capital_event_fixtures, case, record_property):
    manifest = capital_event_fixtures.get(*case)
    loader = importlib.import_module("src.pipeline.workbook.loader")
    mapper = importlib.import_module("src.pipeline.workbook.layout_mapper")
    model = loader.load_workbook_model(manifest.path)
    roles = {s: manifest.sheet_roles[s] for s in manifest.relevant_sheets}
    layouts = mapper.map_layouts(model, roles)
    failures = {}
    for sheet, golden in manifest.layouts.items():
        if sheet not in roles:
            continue
        predicted = layouts[sheet].model_dump(mode="json")
        score = score_layout(predicted, golden)
        record_property(f"{sheet}.field_accuracy", round(score.field_accuracy, 3))
        if score.anchor_accuracy < 1.0:
            failures[sheet] = score.mismatches
    assert failures == {}
