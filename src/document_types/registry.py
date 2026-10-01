"""Registry of supported document types.

The user selects the document type for each run; several types may share a file
format (e.g. two different .xlsx workbook types), so the type is never inferred from
the file. Each type declares the formats it accepts, its rule files, the options it
needs at upload time, and the pipeline that processes it.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _REPO_ROOT / "config" / "document_types"

PDF_MAGIC = b"%PDF-"
ZIP_MAGIC = b"PK\x03\x04"


class UnsupportedFormatError(ValueError):
    """The uploaded file's format is not accepted by the selected document type."""


class InvalidOptionsError(ValueError):
    """The run options do not satisfy the document type's options schema."""


@dataclass
class DocumentTypeSpec:
    id: str
    label: str
    description: str
    accepted_formats: list[str]
    rule_files: list[str]
    pipeline_factory: Callable[[], Any]
    options_schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}, "required": []})
    config: dict[str, Any] = field(default_factory=dict)
    # A second upload the type needs (e.g. the prior event's workbook): label, accepted
    # formats, and the boolean option that waives it. None when the type takes one file.
    prior_document: dict[str, Any] | None = None

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "description": self.description,
            "accepted_formats": list(self.accepted_formats),
            "options_schema": self.options_schema,
            "prior_document": self.prior_document,
        }


def _pdf_pipeline_factory():
    from src.core.config import get_settings, load_app_yaml
    from src.pipeline.orchestrator import ValidationPipeline

    return ValidationPipeline(app_config=load_app_yaml(), settings=get_settings())


def _workbook_pipeline_factory():
    from src.pipeline.workbook.pipeline import WorkbookPipeline

    return WorkbookPipeline()


def _load_yaml(name: str) -> dict[str, Any]:
    return yaml.safe_load((_CONFIG_DIR / name).read_text(encoding="utf-8")) or {}


@lru_cache(maxsize=1)
def _registry() -> dict[str, DocumentTypeSpec]:
    capital = _load_yaml("capital_event.yaml")
    event_types = capital.get("event_types", [])
    specs = [
        DocumentTypeSpec(
            id="financial_statements",
            label="Financial Statements (PDF)",
            description="Fund financial statements: cover, balance sheet, operations, cash flows, schedule of investments.",
            accepted_formats=["pdf"],
            rule_files=["rules/rules.json", "rules/multi_page_rules.json"],
            pipeline_factory=_pdf_pipeline_factory,
        ),
        DocumentTypeSpec(
            id="capital_event_workbook",
            label="Capital Event Workbook",
            description="Excel roll-forward model for a capital call, distribution or net event.",
            accepted_formats=["xlsx", "xlsm"],
            rule_files=["rules/capital_event/workbook_rules.json"],
            pipeline_factory=_workbook_pipeline_factory,
            options_schema={
                "type": "object",
                "properties": {
                    "event_type": {
                        "type": "string",
                        "title": "Event type",
                        "enum": [e["id"] for e in event_types],
                        "enumLabels": [e.get("label", e["id"]) for e in event_types],
                    },
                    "event_label": {"type": "string", "title": "Event label (optional cross-check)"},
                    "notice_date": {"type": "string", "format": "date", "title": "Notice date (optional cross-check)"},
                    "first_event": {
                        "type": "boolean",
                        "title": "This is the fund's first capital event (no prior workbook)",
                    },
                },
                "required": ["event_type"],
            },
            config=capital,
            prior_document={
                "label": "Prior event workbook",
                "description": "The workbook of the most recent prior capital event, used to check that history "
                               "and the roll-forward carried over and that plugs are allocated consistently.",
                "accepted_formats": ["xlsx", "xlsm"],
                "waived_by_option": "first_event",
            },
        ),
    ]
    return {spec.id: spec for spec in specs}


def list_document_types() -> list[DocumentTypeSpec]:
    return list(_registry().values())


def get_document_type(document_type_id: str) -> DocumentTypeSpec:
    try:
        return _registry()[document_type_id]
    except KeyError:
        raise KeyError(f"Unknown document type: {document_type_id!r}") from None


def relevant_roles(spec: DocumentTypeSpec, event_type: str) -> list[str]:
    for event in spec.config.get("event_types", []):
        if event["id"] == event_type:
            return list(event.get("relevant_roles", []))
    raise InvalidOptionsError(f"Unknown event type {event_type!r} for {spec.id}")


def validate_options(spec: DocumentTypeSpec, options: dict[str, Any] | None) -> dict[str, Any]:
    options = dict(options or {})
    schema = spec.options_schema
    properties = schema.get("properties", {})
    for name in schema.get("required", []):
        if options.get(name) in (None, ""):
            raise InvalidOptionsError(f"Missing required option {name!r} for {spec.label}")
    unknown = set(options) - set(properties)
    if unknown:
        raise InvalidOptionsError(f"Unknown option(s) for {spec.label}: {', '.join(sorted(unknown))}")
    for name, value in options.items():
        if properties[name].get("type") == "boolean" and not isinstance(value, bool):
            raise InvalidOptionsError(f"Option {name!r} must be true or false, got {value!r}")
        allowed = properties[name].get("enum")
        if allowed is not None and value not in allowed:
            raise InvalidOptionsError(f"Option {name!r} must be one of {allowed}, got {value!r}")
    return options


def detect_format(content: bytes, filename: str | None) -> str | None:
    """Return the file format from its bytes (and extension, to tell xlsx from xlsm)."""
    if content.startswith(PDF_MAGIC):
        return "pdf"
    if content.startswith(ZIP_MAGIC):
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "xl/workbook.xml" not in names:
            return None
        is_macro = "xl/vbaProject.bin" in names or (filename or "").lower().endswith(".xlsm")
        return "xlsm" if is_macro else "xlsx"
    return None


def ensure_format(spec: DocumentTypeSpec, content: bytes, filename: str | None, prior: bool = False) -> str:
    formats = spec.prior_document["accepted_formats"] if prior and spec.prior_document else spec.accepted_formats
    detected = detect_format(content, filename)
    if detected is None or detected not in formats:
        accepted = ", ".join(f".{f}" for f in formats)
        what = spec.prior_document["label"] if prior and spec.prior_document else spec.label
        raise UnsupportedFormatError(f"{what} accepts {accepted} files only.")
    return detected


def prior_document_required(spec: DocumentTypeSpec, options: dict[str, Any]) -> bool:
    """Whether the run needs the prior document (i.e. it has one and the option waiving it is off)."""
    if spec.prior_document is None:
        return False
    return not options.get(spec.prior_document["waived_by_option"], False)
