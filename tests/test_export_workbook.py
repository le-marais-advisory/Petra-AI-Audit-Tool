"""PDF export of a capital-event workbook result: sheet locators and workbook cover lines."""
from __future__ import annotations

import io
import json

import pdfplumber
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.api.routers import export
from src.api.routers.export import _page_locator_label, _rule_locator_label
from src.pipeline.workbook.layout import parse_layout
from src.pipeline.workbook.pipeline import WorkbookPipeline
from src.schemas.validation import DocumentValidationResponse
from src.services.rule_service import RuleService


class _Stub:
    def evaluate_rule(self, content, rule, system_prompt, **kwargs):
        return {"rule_id": rule["id"], "rule_name": rule["name"], "verdict": "pass", "summary": "ok", "reasoning": "ok",
                "findings": [], "confidence": "high", "citations": []}


def _result(capital_event_fixtures, defect="hardcoded_lp_cell"):
    manifest = capital_event_fixtures.get(defect=defect)
    pipeline = WorkbookPipeline(text_provider=_Stub(), role_assigner=lambda m, i, p: manifest.sheet_roles,
                                layout_mapper=lambda m, s, r: parse_layout(manifest.layouts[s]))
    rules = RuleService().load_rules(document_type="capital_event_workbook", event_type="capital_call")
    raw = pipeline.run(manifest.path, rules=rules, options={"event_type": "capital_call"},
                       source_filename=manifest.path.name)
    return DocumentValidationResponse(**raw)


def test_locators_name_sheets(capital_event_fixtures):
    response = _result(capital_event_fixtures)
    assessment = next(a for a in response.analysis.rule_assessments if a.rule_id == "CE-ALLOC-PER-LP-FORMULAS")
    assert _rule_locator_label(assessment) == "Sheet 'Allocation'"
    page_result = next(p for p in response.analysis.text_page_results if p.rule_id == "CE-ALLOC-PER-LP-FORMULAS")
    assert _page_locator_label(page_result) == "Sheet 'Allocation'"


def test_export_renders_workbook_cover(capital_event_fixtures):
    response = _result(capital_event_fixtures)
    app = FastAPI()
    app.include_router(export.router)
    body = {
        "document_id": response.document_id,
        "source_filename": response.source_filename,
        "page_count": response.page_count,
        "document_type": response.document_type,
        "options": response.options,
        "analysis": json.loads(response.analysis.model_dump_json()),
    }
    reply = TestClient(app).post("/export/pdf", json=body)
    assert reply.status_code == 200
    with pdfplumber.open(io.BytesIO(reply.content)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    assert "Event type: Capital call" in text
    assert f"Sheets processed: {response.page_count}" in text
    assert "Sheet 'Allocation'" in text
