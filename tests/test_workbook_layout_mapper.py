"""Layout mapper orchestration with a scripted provider (no LLM)."""
from __future__ import annotations

import copy

import pytest

from src.pipeline.workbook import layout_mapper
from src.pipeline.workbook.loader import load_workbook_model
from src.providers.errors import TruncatedResponseError


class ScriptedProvider:
    def __init__(self, answers):
        self.answers = list(answers)
        self.prompts: list[str] = []
        self.schemas: list[dict] = []
        self.efforts: list[str | None] = []

    def complete_structured(self, system_prompt, user_content, json_schema, name="result", effort=None):
        self.prompts.append(user_content)
        self.schemas.append(json_schema)
        self.efforts.append(effort)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return copy.deepcopy(answer)


def _truncated():
    return TruncatedResponseError("budget spent on thinking", max_tokens=100, during_thinking=True)


def _alloc(manifest):
    return next(lay for lay in manifest.layouts.values() if lay["role"] == "allocation")


def test_accepts_a_valid_layout_first_time(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    provider = ScriptedProvider([_alloc(manifest)])
    layout, issues = layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, use_cache=False)
    assert issues == [] and layout is not None
    assert len(provider.prompts) == 1
    assert "Allocation" in provider.prompts[0] and "ROWS" in provider.prompts[0]
    assert provider.schemas[0]["properties"]["role"]


def test_reprompts_with_validator_findings(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    wrong = copy.deepcopy(_alloc(manifest))
    wrong["header_row"] += 1
    provider = ScriptedProvider([wrong, _alloc(manifest)])
    layout, issues = layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, use_cache=False)
    assert layout is not None and issues == []
    assert len(provider.prompts) == 2
    assert "header_mismatch" in provider.prompts[1]


def test_gives_up_after_the_retry(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    wrong = copy.deepcopy(_alloc(manifest))
    wrong["header_row"] += 1
    provider = ScriptedProvider([wrong, wrong])
    layout, issues = layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, use_cache=False)
    assert layout is None
    assert "header_mismatch" in {i.code for i in issues}


def test_schema_errors_are_retried(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    provider = ScriptedProvider([{"header_row": "six"}, _alloc(manifest)])
    layout, _ = layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, use_cache=False)
    assert layout is not None


def test_truncated_answer_is_retried_one_effort_level_lower(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    provider = ScriptedProvider([_truncated(), _alloc(manifest)])
    layout, issues = layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider,
                                                    use_cache=False, effort="medium")
    assert layout is not None and issues == []
    assert provider.efforts == ["medium", "low"]
    assert provider.prompts[0] == provider.prompts[1]


def test_truncation_does_not_use_up_the_validation_retry(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    wrong = copy.deepcopy(_alloc(manifest))
    wrong["header_row"] += 1
    provider = ScriptedProvider([_truncated(), wrong, _alloc(manifest)])
    layout, _ = layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider,
                                               use_cache=False, effort="high")
    assert layout is not None
    assert provider.efforts == ["high", "medium", "medium"]
    assert "header_mismatch" in provider.prompts[2]


def test_truncation_at_the_lowest_effort_is_raised(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    provider = ScriptedProvider([_truncated(), _truncated()])
    with pytest.raises(TruncatedResponseError):
        layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, use_cache=False, effort="medium")
    assert provider.efforts == ["medium", "low"]


def test_effort_defaults_to_the_setting(capital_event_fixtures, monkeypatch):
    from src.core.config import get_settings

    monkeypatch.setattr(get_settings(), "LAYOUT_MAPPING_EFFORT", "medium")
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    provider = ScriptedProvider([_alloc(manifest)])
    layout_mapper.map_sheet_layout(model, "Allocation", "allocation", provider, use_cache=False)
    assert provider.efforts == ["medium"]


def test_map_layouts_skips_other_sheets_and_reports_failures(capital_event_fixtures):
    manifest = capital_event_fixtures.get()
    model = load_workbook_model(manifest.path)
    roles = {"Allocation": "allocation", "Summary": "summary", "Notes": "other"}
    summary = next(lay for lay in manifest.layouts.values() if lay["role"] == "summary")
    bad_summary = {**summary, "check_cells": ["Z99"]}

    class ByRole:
        def complete_structured(self, system_prompt, user_content, json_schema, name="result", effort=None):
            return copy.deepcopy(_alloc(manifest) if name.startswith("allocation") else bad_summary)

    layouts, failures = layout_mapper.map_layouts_with_issues(model, roles, provider=ByRole())
    assert set(layouts) == {"Allocation"}
    assert set(failures) == {"Summary"}
