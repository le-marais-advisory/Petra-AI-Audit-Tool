"""Sheet-role rules that no LLM is involved in: an empty hidden Merge-like template is demoted."""
from __future__ import annotations

from src.pipeline.workbook.loader import load_workbook_model
from src.pipeline.workbook.roles import demote_empty_merge_tabs, propose_roles


def test_hidden_merge_template_full_of_errors_is_demoted(capital_event_fixtures):
    manifest = capital_event_fixtures.get(defect="ok_empty_hidden_merge_tab")
    model = load_workbook_model(manifest.path)
    proposed = propose_roles(model)
    assert proposed["Old Merge"] == "merge"  # hidden, Merge-like headers: the heuristic keeps it as notice data
    roles, notes = demote_empty_merge_tabs(model, dict(proposed))
    assert roles["Old Merge"] == "other"
    assert len(notes) == 1 and "Old Merge" in notes[0] and "no formula references it" in notes[0]
    assert roles["Merge"] == "merge"  # the real Merge tab is untouched


def test_referenced_merge_tab_full_of_errors_is_kept(capital_event_fixtures):
    manifest = capital_event_fixtures.get(defect="referenced_merge_tab_ref_errors")
    model = load_workbook_model(manifest.path)
    roles, notes = demote_empty_merge_tabs(model, {"Old Merge": "merge", "Merge": "merge"})
    assert roles["Old Merge"] == "merge" and notes == []


def test_hidden_merge_tabs_with_rows_are_kept(capital_event_fixtures):
    manifest = capital_event_fixtures.get("capital_call", "multi_vehicle")
    model = load_workbook_model(manifest.path)
    merges = {n for n, r in manifest.sheet_roles.items() if r == "merge"}
    roles, notes = demote_empty_merge_tabs(model, dict(manifest.sheet_roles))
    assert {n for n, r in roles.items() if r == "merge"} == merges and notes == []
