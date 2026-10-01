"""Document-type registry and the upload/API contract (plan section 1).

The user selects the document type; the server validates that the uploaded file's
format matches it. Target modules are imported inside fixtures so a missing module
fails these tests individually instead of aborting collection.
"""
from __future__ import annotations

import importlib
import io
import json
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests.fixtures.generate_capital_event_fixtures import EVENT_TYPES, RELEVANT_ROLES

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_BYTES = b"%PDF-1.4\n%fake\n"


@pytest.fixture(scope="module")
def registry():
    return importlib.import_module("src.document_types.registry")


@pytest.fixture(scope="module")
def xlsx_bytes(capital_event_fixtures) -> bytes:
    return capital_event_fixtures.get().path.read_bytes()


@pytest.fixture()
def client():
    validations = importlib.import_module("src.api.routers.validations")
    document_types = importlib.import_module("src.api.routers.document_types")
    rules = importlib.import_module("src.api.routers.rules")
    app = FastAPI()
    app.include_router(validations.router)
    app.include_router(document_types.router)
    app.include_router(rules.router)
    return TestClient(app)


# --- registry -----------------------------------------------------------------------


def test_registry_lists_the_supported_types(registry):
    ids = [spec.id for spec in registry.list_document_types()]
    assert ids[:2] == ["financial_statements", "capital_event_workbook"]


def test_financial_statements_spec_keeps_todays_behaviour(registry):
    spec = registry.get_document_type("financial_statements")
    assert spec.accepted_formats == ["pdf"]
    assert spec.label
    assert registry.validate_options(spec, {}) == {}


def test_capital_event_spec(registry):
    spec = registry.get_document_type("capital_event_workbook")
    assert spec.accepted_formats == ["xlsx", "xlsm"]
    event_type = spec.options_schema["properties"]["event_type"]
    assert event_type["enum"] == list(EVENT_TYPES)
    assert "event_type" in spec.options_schema["required"]


def test_unknown_document_type_raises(registry):
    with pytest.raises(KeyError):
        registry.get_document_type("nav_workbook")


@pytest.mark.parametrize("event_type", EVENT_TYPES)
def test_event_types_declare_relevant_sheet_roles(registry, event_type):
    spec = registry.get_document_type("capital_event_workbook")
    assert set(registry.relevant_roles(spec, event_type)) == RELEVANT_ROLES[event_type]


def test_validate_options_requires_a_known_event_type(registry):
    spec = registry.get_document_type("capital_event_workbook")
    assert registry.validate_options(spec, {"event_type": "capital_call"})["event_type"] == "capital_call"
    with pytest.raises(registry.InvalidOptionsError):
        registry.validate_options(spec, {})
    with pytest.raises(registry.InvalidOptionsError):
        registry.validate_options(spec, {"event_type": "rebalance"})


def test_detect_format(registry, xlsx_bytes):
    assert registry.detect_format(PDF_BYTES, "a.pdf") == "pdf"
    assert registry.detect_format(xlsx_bytes, "wb.xlsx") == "xlsx"
    assert registry.detect_format(xlsx_bytes, "wb.xlsm") == "xlsm"
    # Legacy binary .xls (OLE compound file) and arbitrary zips are not supported.
    assert registry.detect_format(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 32, "old.xls") is None
    assert registry.detect_format(b"PK\x03\x04not-a-workbook", "x.xlsx") is None


def test_ensure_format_rejects_a_mismatch(registry, xlsx_bytes):
    capital = registry.get_document_type("capital_event_workbook")
    statements = registry.get_document_type("financial_statements")
    assert registry.ensure_format(capital, xlsx_bytes, "wb.xlsx") == "xlsx"
    with pytest.raises(registry.UnsupportedFormatError):
        registry.ensure_format(capital, PDF_BYTES, "a.pdf")
    with pytest.raises(registry.UnsupportedFormatError):
        registry.ensure_format(statements, xlsx_bytes, "wb.xlsx")


def test_capital_event_pipeline_factory(registry):
    spec = registry.get_document_type("capital_event_workbook")
    pipeline = spec.pipeline_factory()
    workbook_pipeline = importlib.import_module("src.pipeline.workbook.pipeline")
    assert isinstance(pipeline, workbook_pipeline.WorkbookPipeline)


# --- API ----------------------------------------------------------------------------


def test_get_document_types(client):
    response = client.get("/document-types")
    assert response.status_code == 200
    types = {t["id"]: t for t in response.json()["document_types"]}
    assert types["capital_event_workbook"]["accepted_formats"] == ["xlsx", "xlsm"]
    assert types["capital_event_workbook"]["options_schema"]["properties"]["event_type"]["enum"] == list(EVENT_TYPES)
    assert types["financial_statements"]["accepted_formats"] == ["pdf"]
    assert types["capital_event_workbook"]["prior_document"]["waived_by_option"] == "first_event"
    assert types["financial_statements"]["prior_document"] is None


def _post_job(client, content: bytes, filename: str, mime: str, prior=None, **form):
    with patch("src.api.routers.validations.validation_job_service") as jobs:
        jobs.start_job.return_value.job_id = "job-1"
        jobs.start_job.return_value.status = "queued"
        jobs.start_job.return_value.message = "Queued"
        jobs.start_job.return_value.progress_current = 0
        jobs.start_job.return_value.progress_total = 0
        files = {"file": (filename, io.BytesIO(content), mime)}
        if prior is not None:
            files["prior_file"] = (prior[0], io.BytesIO(prior[1]), prior[2])
        response = client.post("/validations/jobs", files=files, data=form)
        return response, jobs.start_job


def test_upload_capital_event_workbook(client, xlsx_bytes):
    response, start_job = _post_job(
        client, xlsx_bytes, "egf.xlsx", XLSX_MIME,
        prior=("egf_prior.xlsx", xlsx_bytes, XLSX_MIME),
        document_type="capital_event_workbook", options_json=json.dumps({"event_type": "capital_call"}),
    )
    assert response.status_code == 200, response.text
    kwargs = start_job.call_args.kwargs
    assert kwargs["document_type"] == "capital_event_workbook"
    assert kwargs["options"] == {"event_type": "capital_call"}
    assert kwargs["source_filename"] == "egf.xlsx"
    assert kwargs["prior_file_path"] and kwargs["prior_source_filename"] == "egf_prior.xlsx"


def test_first_event_needs_no_prior_workbook(client, xlsx_bytes):
    response, start_job = _post_job(
        client, xlsx_bytes, "egf.xlsx", XLSX_MIME, document_type="capital_event_workbook",
        options_json=json.dumps({"event_type": "capital_call", "first_event": True}),
    )
    assert response.status_code == 200, response.text
    assert start_job.call_args.kwargs["prior_file_path"] is None
    assert start_job.call_args.kwargs["options"] == {"event_type": "capital_call", "first_event": True}


def test_prior_workbook_is_required_unless_first_event(client, xlsx_bytes):
    response, start_job = _post_job(client, xlsx_bytes, "egf.xlsx", XLSX_MIME, document_type="capital_event_workbook",
                                    options_json=json.dumps({"event_type": "capital_call"}))
    assert response.status_code == 422
    assert "prior" in response.json()["detail"].lower()
    start_job.assert_not_called()


def test_prior_workbook_and_first_event_are_exclusive(client, xlsx_bytes):
    response, _ = _post_job(client, xlsx_bytes, "egf.xlsx", XLSX_MIME, prior=("p.xlsx", xlsx_bytes, XLSX_MIME),
                            document_type="capital_event_workbook",
                            options_json=json.dumps({"event_type": "capital_call", "first_event": True}))
    assert response.status_code == 422


def test_prior_workbook_format_is_checked(client, xlsx_bytes):
    response, _ = _post_job(client, xlsx_bytes, "egf.xlsx", XLSX_MIME, prior=("p.pdf", PDF_BYTES, "application/pdf"),
                            document_type="capital_event_workbook", options_json=json.dumps({"event_type": "capital_call"}))
    assert response.status_code == 415


def test_prior_file_is_rejected_for_types_without_one(client, xlsx_bytes):
    response, _ = _post_job(client, PDF_BYTES, "fs.pdf", "application/pdf", prior=("p.pdf", PDF_BYTES, "application/pdf"))
    assert response.status_code == 422


def test_first_event_option_must_be_boolean(registry):
    spec = registry.get_document_type("capital_event_workbook")
    with pytest.raises(registry.InvalidOptionsError):
        registry.validate_options(spec, {"event_type": "capital_call", "first_event": "maybe"})


def test_capital_event_type_asks_for_the_prior_workbook(registry):
    spec = registry.get_document_type("capital_event_workbook")
    assert spec.prior_document["waived_by_option"] == "first_event"
    assert spec.prior_document["accepted_formats"] == ["xlsx", "xlsm"]
    assert spec.options_schema["properties"]["first_event"]["type"] == "boolean"
    assert registry.get_document_type("financial_statements").prior_document is None


def test_upload_defaults_to_financial_statements(client):
    response, start_job = _post_job(client, PDF_BYTES, "fs.pdf", "application/pdf")
    assert response.status_code == 200, response.text
    assert start_job.call_args.kwargs["document_type"] == "financial_statements"


def test_legacy_pdf_field_name_still_accepted(client):
    with patch("src.api.routers.validations.validation_job_service") as jobs:
        jobs.start_job.return_value.job_id = "job-1"
        jobs.start_job.return_value.status = "queued"
        jobs.start_job.return_value.message = "Queued"
        jobs.start_job.return_value.progress_current = 0
        jobs.start_job.return_value.progress_total = 0
        response = client.post("/validations/jobs", files={"pdf": ("fs.pdf", io.BytesIO(PDF_BYTES), "application/pdf")})
    assert response.status_code == 200, response.text


def test_format_mismatch_is_415(client, xlsx_bytes):
    response, start_job = _post_job(client, xlsx_bytes, "wb.xlsx", XLSX_MIME, document_type="financial_statements")
    assert response.status_code == 415
    response, _ = _post_job(client, PDF_BYTES, "fs.pdf", "application/pdf", document_type="capital_event_workbook",
                            options_json=json.dumps({"event_type": "capital_call"}))
    assert response.status_code == 415
    start_job.assert_not_called()


def test_legacy_xls_is_415(client):
    response, _ = _post_job(client, b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 64, "old.xls",
                            "application/vnd.ms-excel", document_type="capital_event_workbook",
                            options_json=json.dumps({"event_type": "capital_call"}))
    assert response.status_code == 415


def test_missing_event_type_is_422(client, xlsx_bytes):
    response, start_job = _post_job(client, xlsx_bytes, "wb.xlsx", XLSX_MIME, document_type="capital_event_workbook")
    assert response.status_code == 422
    start_job.assert_not_called()


def test_unknown_document_type_is_400(client):
    response, _ = _post_job(client, PDF_BYTES, "fs.pdf", "application/pdf", document_type="nav_workbook")
    assert response.status_code == 400


def test_rules_endpoint_filters_by_document_type_and_event(client):
    response = client.get("/rules", params={"document_type": "capital_event_workbook", "event_type": "capital_call"})
    assert response.status_code == 200
    ids = {r["id"] for r in response.json()["rules"]}
    assert ids and all(i.startswith("CE-") for i in ids)
    assert "CE-DIST-ROC-LIMIT" not in ids
    default = {r["id"] for r in client.get("/rules").json()["rules"]}
    assert default and not any(i.startswith("CE-") for i in default)
