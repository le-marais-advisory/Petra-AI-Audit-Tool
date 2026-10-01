"""WorkbookPipeline end to end, offline (plan sections 2-5).

The LLM-facing steps (role confirmation, layout mapping, hybrid rules) are replaced by
fakes, so this pins the orchestration: only relevant sheets are mapped and emitted,
deterministic rules never reach the LLM, and the response keeps the existing
DocumentValidationResponse shape with sheet labels.
"""
from __future__ import annotations

import importlib

import pytest

from src.schemas.validation import DocumentValidationResponse
from src.services.rule_service import RuleService
from tests.fixtures.generate_capital_event_fixtures import DETERMINISTIC_RULE_IDS, HYBRID_RULE_IDS


class FakeTextProvider:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def evaluate_rule(self, document_content: str, rule: dict, system_prompt: str) -> dict:
        self.calls.append((rule["id"], document_content))
        return {
            "rule_id": rule["id"],
            "rule_name": rule["name"],
            "verdict": "needs_review",
            "summary": "fake",
            "reasoning": "fake",
            "findings": [],
            "confidence": "low",
            "citations": [],
        }


@pytest.fixture()
def run_pipeline(capital_event_fixtures):
    pipeline_mod = importlib.import_module("src.pipeline.workbook.pipeline")
    layout = importlib.import_module("src.pipeline.workbook.layout")

    def run(event_type="capital_call", variant="standard", defect=None, with_prior=False, options=None):
        manifest = capital_event_fixtures.get(event_type, variant, defect, with_prior)
        provider = FakeTextProvider()
        mapped: list[str] = []
        # Route each workbook (current / prior) to its own golden layouts.
        by_file = {manifest.path.name: manifest}
        if manifest.prior:
            by_file[manifest.prior.path.name] = manifest.prior

        def role_assigner(model, inventory, proposed):
            return by_file[model.file_name].sheet_roles

        def layout_mapper(model, sheet_name, role):
            source = by_file[model.file_name]
            if source is manifest:
                mapped.append(sheet_name)
            return layout.parse_layout(source.layouts[sheet_name])

        pipeline = pipeline_mod.WorkbookPipeline(text_provider=provider, role_assigner=role_assigner,
                                                 layout_mapper=layout_mapper)
        rules = RuleService().load_rules(document_type="capital_event_workbook", event_type=event_type)
        result = pipeline.run(manifest.path, rules=rules, options=options or {"event_type": event_type},
                              source_filename=manifest.path.name,
                              prior_file_path=str(manifest.prior.path) if manifest.prior else None,
                              prior_source_filename=manifest.prior.path.name if manifest.prior else None)
        return manifest, result, provider, mapped

    return run


def test_response_shape(run_pipeline):
    manifest, result, _, _ = run_pipeline()
    response = DocumentValidationResponse(**result)
    assert response.document_type == "capital_event_workbook"
    assert response.options == {"event_type": "capital_call"}
    assert response.source_filename == manifest.path.name


def test_only_relevant_sheets_are_mapped_and_emitted(run_pipeline):
    manifest, result, _, mapped = run_pipeline()
    assert sorted(mapped) == sorted(s for s in manifest.relevant_sheets if s in manifest.layouts)
    labels = [p["label"] for p in result["pages"]]
    # Relevant sheets first, then the sheets their formulas reference (scanned, not mapped).
    assert labels == manifest.relevant_sheets + manifest.reference_sheets
    for page in result["pages"]:
        assert page["page"] == list(manifest.sheet_roles).index(page["label"]) + 1
        expected_type = "reference" if page["label"] in manifest.reference_sheets else manifest.sheet_roles[page["label"]]
        assert page["page_type"] == [expected_type]


def test_deterministic_rules_never_reach_the_llm(run_pipeline):
    _, _, provider, _ = run_pipeline()
    called = {rule_id for rule_id, _ in provider.calls}
    assert called and called <= set(HYBRID_RULE_IDS)


def test_hybrid_prompts_carry_facts_and_only_relevant_sheets(run_pipeline):
    manifest, _, provider, _ = run_pipeline()
    irrelevant = [s for s in manifest.sheet_roles if s not in manifest.relevant_sheets]
    for rule_id, content in provider.calls:
        assert "COMPUTED FACTS" in content, rule_id
        for sheet in irrelevant:
            assert f"<sheet name=\"{sheet}\"" not in content, (rule_id, sheet)


def test_deterministic_verdicts_flow_into_the_assessments(run_pipeline):
    manifest, result, _, _ = run_pipeline()
    assessments = {a["rule_id"]: a for a in result["analysis"]["rule_assessments"]}
    for rule_id in DETERMINISTIC_RULE_IDS:
        if rule_id not in assessments:
            continue  # filtered out for this event type
        expected = manifest.expected_verdicts[rule_id]
        if expected is not None:
            assert assessments[rule_id]["verdict"] == expected, rule_id


def test_defect_surfaces_with_sheet_citation(run_pipeline):
    manifest, result, _, _ = run_pipeline(defect="hardcoded_lp_cell")
    assessment = next(a for a in result["analysis"]["rule_assessments"] if a["rule_id"] == "CE-ALLOC-PER-LP-FORMULAS")
    assert assessment["verdict"] == "fail"
    assert any(c.get("sheet") == "Allocation" and c.get("cell") for c in assessment["citations"])


def test_layout_failure_degrades_to_needs_review(capital_event_fixtures):
    pipeline_mod = importlib.import_module("src.pipeline.workbook.pipeline")
    layout = importlib.import_module("src.pipeline.workbook.layout")
    manifest = capital_event_fixtures.get()

    def broken_mapper(model, sheet_name, role):
        raw = dict(manifest.layouts[sheet_name])
        if role == "itd":
            raw = {**raw, "event_header_row": raw["event_header_row"] + 40}
        return layout.parse_layout(raw)

    pipeline = pipeline_mod.WorkbookPipeline(text_provider=FakeTextProvider(),
                                             role_assigner=lambda m, i, p: manifest.sheet_roles,
                                             layout_mapper=broken_mapper)
    rules = RuleService().load_rules(document_type="capital_event_workbook", event_type="capital_call")
    result = pipeline.run(manifest.path, rules=rules, options={"event_type": "capital_call"})
    assessments = {a["rule_id"]: a for a in result["analysis"]["rule_assessments"]}
    assert assessments["CE-ITD-CUMULATIVE"]["verdict"] == "needs_review"
    assert "layout" in assessments["CE-ITD-CUMULATIVE"]["summary"].lower()
    assert assessments["CE-ALLOC-REFOOT"]["verdict"] == "pass"


def test_validation_service_dispatches_by_document_type(capital_event_fixtures, monkeypatch):
    service_mod = importlib.import_module("src.services.validation_service")
    captured = {}

    class Recorder:
        def run(self, file_path, rules=None, options=None, source_filename=None, **kwargs):
            captured.update(file_path=file_path, options=options, rule_ids=[r["id"] for r in rules or []])
            return {"ok": True}

    registry = importlib.import_module("src.document_types.registry")
    spec = registry.get_document_type("capital_event_workbook")
    monkeypatch.setattr(spec, "pipeline_factory", lambda: Recorder())
    manifest = capital_event_fixtures.get()
    service_mod.ValidationService().validate_document(
        file_path=str(manifest.path), source_filename=manifest.path.name,
        document_type="capital_event_workbook", options={"event_type": "distribution"})
    assert captured["options"] == {"event_type": "distribution"}
    assert "CE-NET-EVENT-STRUCTURE" not in captured["rule_ids"]
    assert "CE-DIST-ROC-LIMIT" in captured["rule_ids"]


def _verdicts(result):
    return {a["rule_id"]: a["verdict"] for a in result["analysis"]["rule_assessments"]}


def test_prior_workbook_drives_the_cross_event_rules(run_pipeline):
    _, result, _, _ = run_pipeline(with_prior=True)
    verdicts = _verdicts(result)
    for rule_id in ("CE-XEV-HISTORY-UNCHANGED", "CE-XEV-ROLL-FORWARD", "CE-XEV-PLUG-CONSISTENCY"):
        assert verdicts[rule_id] == "pass", rule_id
    assert result["options"] == {"event_type": "capital_call"}


def test_cross_event_defect_is_caught_end_to_end(run_pipeline):
    _, result, _, _ = run_pipeline(defect="plug_pattern_changed")
    verdicts = _verdicts(result)
    assert verdicts["CE-XEV-PLUG-CONSISTENCY"] == "fail"
    assert verdicts["CE-ALLOC-PLUG-DISCIPLINE"] == "pass"  # spreading is allowed on its own


def test_first_event_marks_cross_event_rules_not_applicable(run_pipeline):
    _, result, _, _ = run_pipeline(options={"event_type": "capital_call", "first_event": True})
    verdicts = _verdicts(result)
    assert verdicts["CE-XEV-HISTORY-UNCHANGED"] == "not_applicable"
